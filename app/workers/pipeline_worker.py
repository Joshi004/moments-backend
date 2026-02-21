"""
Pipeline worker - consumes pipeline requests from Redis Stream.
Executes complete pipelines asynchronously with non-blocking Redis operations.
"""
import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.core.redis import get_async_redis_client
from app.core.config import get_settings
from app.services.pipeline.orchestrator import execute_pipeline
from app.services.pipeline.lock import acquire_lock, release_lock
from app.services.pipeline.redis_history import archive_active_to_history
from app.services.pipeline.status import delete_status, get_current_stage, update_pipeline_status, mark_stage_failed

logger = logging.getLogger(__name__)


class PipelineWorker:
    """Background worker that consumes pipeline requests from Redis Stream."""
    
    STREAM_KEY = "pipeline:requests"
    GROUP_NAME = "pipeline_workers"
    BLOCK_TIMEOUT_MS = 5000
    CLAIM_MIN_IDLE_MS = 60000  # 1 minute
    
    def __init__(self):
        """Initialize worker with settings. Redis client is initialized async in run()."""
        self.redis = None  # Initialized async in run()
        self.settings = get_settings()
        self.consumer_name = f"worker-{self.settings.container_id}"
        self.running = True
        self.max_concurrent_pipelines = self.settings.max_concurrent_pipelines
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signal."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False
    
    async def ensure_consumer_group(self):
        """Create consumer group if it doesn't exist."""
        try:
            await self.redis.xgroup_create(
                self.STREAM_KEY,
                self.GROUP_NAME,
                id="0",
                mkstream=True
            )
            logger.info(f"Created consumer group '{self.GROUP_NAME}' on stream '{self.STREAM_KEY}'")
        except Exception as e:
            if "BUSYGROUP" in str(e):
                logger.debug("Consumer group already exists")
            else:
                logger.error(f"Error creating consumer group: {e}")
                raise
    
    async def _claim_stale_message(self) -> Optional[Dict[str, Any]]:
        """Try to claim stale messages from crashed workers (non-blocking)."""
        try:
            result = await self.redis.xautoclaim(
                self.STREAM_KEY,
                self.GROUP_NAME,
                self.consumer_name,
                min_idle_time=self.CLAIM_MIN_IDLE_MS,
                start_id="0-0",
                count=1
            )
            
            if result and len(result) > 1 and result[1]:
                messages = result[1]
                if messages:
                    message_id, message_data = messages[0]
                    logger.info(f"Claimed stale message {message_id}")
                    return {
                        "id": message_id,
                        "video_id": message_data.get("video_id"),
                        "request_id": message_data.get("request_id"),
                        "config": message_data.get("config"),
                    }
        except Exception as e:
            logger.error(f"Error claiming stale messages: {e}")
        
        return None
    
    async def _read_new_message(self) -> Optional[Dict[str, Any]]:
        """Read new message from stream (non-blocking - yields to event loop)."""
        try:
            # This now yields to the event loop during the block period
            # instead of freezing the entire application
            result = await self.redis.xreadgroup(
                groupname=self.GROUP_NAME,
                consumername=self.consumer_name,
                streams={self.STREAM_KEY: ">"},
                count=1,
                block=self.BLOCK_TIMEOUT_MS
            )
            
            if result:
                # result = [(stream_name, [(message_id, message_data), ...])]
                stream_name, messages = result[0]
                if messages:
                    message_id, message_data = messages[0]
                    return {
                        "id": message_id,
                        "video_id": message_data.get("video_id"),
                        "request_id": message_data.get("request_id"),
                        "config": message_data.get("config"),
                    }
        except Exception as e:
            logger.error(f"Error reading from stream: {e}")
        
        return None
    
    async def _create_db_history_record(
        self,
        request_id: str,
        video_id: str,
        pipeline_type: str,
        started_at: datetime,
    ) -> Optional[int]:
        """
        Create a pipeline_history DB record at pipeline start.

        Returns the numeric DB id on success, or None if creation fails.
        Failure is non-fatal; the pipeline continues without a DB record.
        """
        try:
            from app.database.session import get_session_factory
            from app.repositories import video_db_repository, pipeline_history_db_repository

            session_factory = get_session_factory()
            async with session_factory() as session:
                video = await video_db_repository.get_by_identifier(session, video_id)
                if video is None:
                    logger.warning(
                        f"Video '{video_id}' not found in DB; pipeline_history record skipped"
                    )
                    return None

                record = await pipeline_history_db_repository.create(
                    session,
                    identifier=request_id,
                    video_id=video.id,
                    pipeline_type=pipeline_type,
                    status="running",
                    started_at=started_at,
                )
                await session.commit()
                logger.info(
                    f"Created pipeline_history id={record.id} for request_id={request_id}"
                )
                return record.id
        except Exception as e:
            logger.error(f"Failed to create pipeline_history DB record for {request_id}: {e}")
            return None

    async def _update_db_history_record(
        self,
        history_id: int,
        result: Dict[str, Any],
        started_at: datetime,
    ) -> None:
        """
        Update the pipeline_history DB record with the final outcome.

        Failure is non-fatal; logged but does not propagate.
        """
        try:
            from app.database.session import get_session_factory
            from app.repositories import pipeline_history_db_repository

            completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            duration = result.get("duration_seconds")

            if result.get("cancelled"):
                status = "cancelled"
            elif result.get("success"):
                status = "completed"
            else:
                status = "failed"

            session_factory = get_session_factory()
            async with session_factory() as session:
                await pipeline_history_db_repository.update_status(
                    session,
                    history_id=history_id,
                    status=status,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    total_moments_generated=result.get("total_moments_generated"),
                    total_clips_created=result.get("total_clips_created"),
                    error_stage=result.get("error_stage"),
                    error_message=result.get("error_message"),
                )
                await session.commit()
            logger.info(
                f"Updated pipeline_history id={history_id} status={status}"
            )
        except Exception as e:
            logger.error(f"Failed to update pipeline_history id={history_id}: {e}")

    async def _process_message(self, message: Dict[str, Any]) -> None:
        """
        Process a single pipeline request.

        Args:
            message: Message dictionary with id, video_id, request_id, config
        """
        video_id = message["video_id"]
        request_id = message["request_id"]
        config_str = message["config"]

        try:
            config = json.loads(config_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse config for {request_id}: {e}")
            return

        logger.info(f"Processing pipeline: {request_id}")

        # Acquire lock
        if not await acquire_lock(video_id, request_id):
            logger.warning(f"Could not acquire lock for {video_id}, skipping")
            return

        pipeline_history_id: Optional[int] = None
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            # Create DB history record at pipeline start (non-fatal if it fails)
            pipeline_history_id = await self._create_db_history_record(
                request_id=request_id,
                video_id=video_id,
                pipeline_type="full",  # Will be refined via orchestrator return value
                started_at=started_at,
            )

            # Execute pipeline
            result = await execute_pipeline(
                video_id, config, pipeline_history_id=pipeline_history_id
            )

            # Update DB record with outcome
            if pipeline_history_id:
                await self._update_db_history_record(pipeline_history_id, result, started_at)

            # Archive to Redis history
            try:
                run_id = await archive_active_to_history(
                    video_id, pipeline_history_id=pipeline_history_id
                )
                if run_id:
                    logger.info(f"Archived pipeline run to Redis: {run_id}")
                else:
                    logger.warning(f"Failed to archive pipeline run for {video_id}")
            except Exception as e:
                logger.error(f"Failed to archive history for {video_id}: {e}")

            success = result.get("success", False)
            logger.info(f"Pipeline completed: {request_id}, success={success}")

        except Exception as e:
            logger.exception(f"Pipeline execution failed: {request_id}")
            # Mark as failed so UI doesn't show "in progress" forever
            try:
                current_stage_str = await get_current_stage(video_id)
                if current_stage_str:
                    from app.models.pipeline_schemas import PipelineStage
                    try:
                        failed_stage = PipelineStage(current_stage_str)
                        await mark_stage_failed(video_id, failed_stage, str(e))
                    except ValueError:
                        pass
                await update_pipeline_status(video_id, "failed")
            except Exception as status_err:
                logger.error(f"Failed to update status to failed for {video_id}: {status_err}")

            # Update DB record with failure
            if pipeline_history_id:
                await self._update_db_history_record(
                    pipeline_history_id,
                    {
                        "success": False,
                        "error_stage": "pipeline_error",
                        "error_message": str(e),
                    },
                    started_at,
                )

            # Try to archive history even on failure
            try:
                run_id = await archive_active_to_history(
                    video_id, pipeline_history_id=pipeline_history_id
                )
                if run_id:
                    logger.info(f"Archived failed pipeline run to Redis: {run_id}")
            except Exception as hist_err:
                logger.error(f"Failed to archive history after error for {video_id}: {hist_err}")
        finally:
            await release_lock(video_id)
    
    async def _acknowledge_message(self, message_id: str) -> None:
        """Acknowledge processed message (non-blocking)."""
        try:
            await self.redis.xack(self.STREAM_KEY, self.GROUP_NAME, message_id)
            logger.debug(f"Acknowledged message {message_id}")
        except Exception as e:
            logger.error(f"Error acknowledging message {message_id}: {e}")
    
    async def _process_and_acknowledge(self, message: Dict[str, Any]) -> None:
        """
        Process a pipeline message and acknowledge it.
        
        This wrapper ensures the message is always acknowledged even if processing fails,
        preventing message redelivery for failed pipelines.
        
        Args:
            message: Message dictionary with id, video_id, request_id, config
        """
        try:
            await self._process_message(message)
        finally:
            # Always acknowledge, even if processing failed
            await self._acknowledge_message(message["id"])
    
    async def run(self):
        """
        Main worker loop with support for concurrent pipeline execution.
        
        Maintains a set of active pipeline tasks up to max_concurrent_pipelines limit.
        Each pipeline runs as an independent async task, allowing multiple pipelines
        to execute concurrently while sharing resources via global semaphores.
        """
        # Initialize async Redis client
        self.redis = await get_async_redis_client()
        
        await self.ensure_consumer_group()
        logger.info(
            f"Pipeline worker started: {self.consumer_name} "
            f"(max concurrent pipelines: {self.max_concurrent_pipelines})"
        )
        
        active_tasks: set = set()
        
        while self.running:
            try:
                # Clean up completed tasks
                done_tasks = {t for t in active_tasks if t.done()}
                for task in done_tasks:
                    try:
                        # Re-raise any exceptions for logging
                        await task
                    except Exception as e:
                        logger.error(f"Pipeline task failed: {e}")
                active_tasks -= done_tasks
                
                # Start new pipelines if under limit
                if len(active_tasks) < self.max_concurrent_pipelines:
                    # Try to claim stale messages first (non-blocking)
                    message = await self._claim_stale_message()
                    
                    # If no stale messages, read new ones (non-blocking)
                    if message is None:
                        message = await self._read_new_message()
                    
                    if message:
                        # Create task for concurrent execution
                        task = asyncio.create_task(
                            self._process_and_acknowledge(message)
                        )
                        active_tasks.add(task)
                        logger.info(
                            f"Started pipeline task for {message['video_id']} "
                            f"({len(active_tasks)}/{self.max_concurrent_pipelines} active)"
                        )
                else:
                    # At capacity, wait briefly before checking again
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.exception(f"Error in worker loop: {e}")
                await asyncio.sleep(1)
        
        # Graceful shutdown: wait for active tasks to complete
        if active_tasks:
            logger.info(
                f"Shutting down: waiting for {len(active_tasks)} active pipeline(s) to complete..."
            )
            await asyncio.gather(*active_tasks, return_exceptions=True)
        
        logger.info(f"Pipeline worker {self.consumer_name} shut down")


async def ensure_pipeline_consumer_group():
    """
    Ensure consumer group exists (called on startup).
    Async version for proper non-blocking initialization.
    """
    redis = await get_async_redis_client()
    try:
        await redis.xgroup_create("pipeline:requests", "pipeline_workers", id="0", mkstream=True)
        logger.info("Pipeline consumer group initialized")
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.error(f"Failed to create consumer group: {e}")
            raise
        logger.debug("Pipeline consumer group already exists")


async def start_pipeline_worker():
    """
    Start the pipeline worker (called in background).
    Runs until shutdown signal received.
    """
    worker = PipelineWorker()
    await worker.run()





