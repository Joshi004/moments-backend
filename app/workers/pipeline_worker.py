"""
Pipeline worker - consumes pipeline requests from Redis Stream.
Executes complete pipelines asynchronously with non-blocking Redis operations.
"""
import asyncio
import json
import logging
import signal
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.core.redis import get_async_redis_client
from app.core.config import get_settings
from app.services.pipeline.orchestrator import execute_pipeline
from app.services.pipeline.lock import (
    acquire_lock,
    release_lock,
    force_release_lock,
    get_lock_data,
)
from app.services.pipeline.redis_history import archive_active_to_history
from app.services.pipeline.status import (
    get_current_stage,
    update_pipeline_status,
    mark_stage_failed,
    cleanup_orphaned_status,
)

logger = logging.getLogger(__name__)


class PipelineWorker:
    """Background worker that consumes pipeline requests from Redis Stream."""

    STREAM_KEY = "pipeline:requests"
    GROUP_NAME = "pipeline_workers"
    BLOCK_TIMEOUT_MS = 5000

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

    # ------------------------------------------------------------------
    # Component 1a: Worker Heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """
        Continuously write a heartbeat key to Redis.

        Key: worker:{consumer_name}:heartbeat
        TTL:  settings.heartbeat_ttl_seconds (default 30 s)
        Interval: settings.heartbeat_interval_seconds (default 10 s)

        If this task stops (worker crash / kill), Redis auto-expires the key
        after TTL seconds, letting other workers detect the crash.
        """
        heartbeat_key = f"worker:{self.consumer_name}:heartbeat"
        interval = self.settings.heartbeat_interval_seconds
        ttl = self.settings.heartbeat_ttl_seconds
        logger.info(
            f"Heartbeat loop started for {self.consumer_name} "
            f"(interval={interval}s, ttl={ttl}s)"
        )
        try:
            while self.running:
                try:
                    await self.redis.set(heartbeat_key, str(time.time()), ex=ttl)
                except Exception as e:
                    logger.warning(f"Failed to write heartbeat: {e}")
                await asyncio.sleep(interval)
        finally:
            # Best-effort cleanup on graceful shutdown.
            try:
                await self.redis.delete(heartbeat_key)
                logger.info(f"Deleted heartbeat key for {self.consumer_name}")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Consumer group management
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Component 4: DB orphan cleanup on startup
    # ------------------------------------------------------------------

    async def _cleanup_orphaned_db_records(self) -> None:
        """
        On startup, find pipeline_history rows stuck in 'running' state that
        are older than status_ttl_seconds and mark them as 'failed'.

        These are records from a previous worker crash that was never recovered.
        This runs once per worker boot and is non-fatal if it fails.
        """
        try:
            from datetime import timedelta
            from app.database.session import get_session_factory
            from app.repositories import pipeline_history_db_repository

            cutoff = datetime.utcnow() - timedelta(
                seconds=self.settings.status_ttl_seconds
            )
            session_factory = get_session_factory()
            async with session_factory() as session:
                orphaned = await pipeline_history_db_repository.get_orphaned_running(
                    session, older_than=cutoff
                )
                if not orphaned:
                    logger.info("No orphaned pipeline_history DB records found on startup")
                    return

                logger.warning(
                    f"Found {len(orphaned)} orphaned pipeline_history record(s) on startup; "
                    "marking as failed"
                )
                for record in orphaned:
                    await pipeline_history_db_repository.update_status(
                        session,
                        history_id=record.id,
                        status="failed",
                        completed_at=datetime.utcnow(),
                        error_stage="worker_crashed",
                        error_message="Worker process died unexpectedly (detected on restart)",
                    )
                await session.commit()
                logger.info(
                    f"Marked {len(orphaned)} orphaned DB record(s) as failed"
                )
        except Exception as e:
            logger.error(f"Startup orphan DB cleanup failed (non-fatal): {e}")

    # ------------------------------------------------------------------
    # Message reading
    # ------------------------------------------------------------------

    async def _claim_stale_message(self) -> Optional[Dict[str, Any]]:
        """
        Try to claim stale messages from crashed workers.

        Component 2 (DLQ): if a message has been delivered more than
        max_message_retries times, move it to the dead letter stream and
        return None so the worker does not attempt to process it again.
        """
        try:
            result = await self.redis.xautoclaim(
                self.STREAM_KEY,
                self.GROUP_NAME,
                self.consumer_name,
                min_idle_time=self.settings.claim_min_idle_ms,
                start_id="0-0",
                count=1,
            )

            if result and len(result) > 1 and result[1]:
                messages = result[1]
                if messages:
                    message_id, message_data = messages[0]

                    # Component 2: check delivery count returned by XAUTOCLAIM
                    delivery_count = await self._get_delivery_count(message_id)
                    if delivery_count > self.settings.max_message_retries:
                        logger.warning(
                            f"Message {message_id} for video "
                            f"{message_data.get('video_id')} has been delivered "
                            f"{delivery_count} times (max={self.settings.max_message_retries}). "
                            "Moving to dead letter queue."
                        )
                        await self._move_to_dead_letter(message_id, message_data, delivery_count)
                        return None

                    logger.info(
                        f"Claimed stale message {message_id} "
                        f"(delivery #{delivery_count})"
                    )
                    return {
                        "id": message_id,
                        "video_id": message_data.get("video_id"),
                        "request_id": message_data.get("request_id"),
                        "config": message_data.get("config"),
                    }
        except Exception as e:
            logger.error(f"Error claiming stale messages: {e}")

        return None

    async def _get_delivery_count(self, message_id: str) -> int:
        """
        Query the PEL to get the delivery count for a specific message.

        Returns 1 as a safe fallback if the count cannot be determined.
        """
        try:
            pel_entries = await self.redis.xpending_range(
                self.STREAM_KEY,
                self.GROUP_NAME,
                min=message_id,
                max=message_id,
                count=1,
            )
            if pel_entries:
                return pel_entries[0].get("times_delivered", 1)
        except Exception as e:
            logger.warning(f"Could not fetch delivery count for {message_id}: {e}")
        return 1

    async def _move_to_dead_letter(
        self,
        message_id: str,
        message_data: Dict[str, Any],
        delivery_count: int,
    ) -> None:
        """
        Write message to the dead letter stream, mark pipeline status as failed,
        and ACK the original message to remove it from the main stream.
        """
        video_id = message_data.get("video_id", "unknown")
        settings = self.settings

        try:
            dlq_entry = {
                "original_id": message_id,
                "video_id": video_id,
                "request_id": message_data.get("request_id", ""),
                "config": message_data.get("config", "{}"),
                "delivery_count": str(delivery_count),
                "failed_at": str(time.time()),
                "reason": "exceeded_max_retries",
            }
            dlq_id = await self.redis.xadd(
                settings.dead_letter_stream,
                dlq_entry,
                maxlen=10000,  # Cap stream size to avoid unbounded growth
            )
            # Set approximate TTL on the stream via XTRIM is not per-entry;
            # individual entry expiry is handled by the maxlen cap above.
            logger.warning(
                f"Moved message {message_id} to DLQ as {dlq_id} "
                f"(video={video_id}, deliveries={delivery_count})"
            )
        except Exception as e:
            logger.error(f"Failed to write message {message_id} to DLQ: {e}")

        # Mark pipeline status as failed so frontend stops polling
        try:
            await update_pipeline_status(video_id, "failed")
            status_key = f"pipeline:{video_id}:active"
            await self.redis.hset(
                status_key,
                mapping={
                    "error_stage": "dead_letter",
                    "error_message": (
                        f"Job failed after {delivery_count} attempts. "
                        "Moved to dead letter queue for investigation."
                    ),
                    "completed_at": str(time.time()),
                },
            )
        except Exception as e:
            logger.error(f"Failed to update status to failed for DLQ message {message_id}: {e}")

        # ACK original message to remove it from the main stream
        try:
            await self.redis.xack(self.STREAM_KEY, self.GROUP_NAME, message_id)
        except Exception as e:
            logger.error(f"Failed to ACK DLQ message {message_id}: {e}")

    async def _read_new_message(self) -> Optional[Dict[str, Any]]:
        """Read new message from stream (non-blocking - yields to event loop)."""
        try:
            result = await self.redis.xreadgroup(
                groupname=self.GROUP_NAME,
                consumername=self.consumer_name,
                streams={self.STREAM_KEY: ">"},
                count=1,
                block=self.BLOCK_TIMEOUT_MS
            )

            if result:
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

    # ------------------------------------------------------------------
    # DB history helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Core pipeline processing
    # ------------------------------------------------------------------

    async def _process_message(self, message: Dict[str, Any]) -> None:
        """
        Process a single pipeline request.

        Includes Component 1c-1d: if the lock cannot be acquired, check the
        heartbeat of the current lock owner. If expired (owner crashed), force-
        release the lock, clean up orphaned status, and re-acquire to process.
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

        # Attempt lock acquisition
        lock_acquired = await acquire_lock(video_id, request_id, self.consumer_name)

        if not lock_acquired:
            # Component 1c: lock exists — check if the owner is still alive
            lock_data = await get_lock_data(video_id)
            heartbeat_key = lock_data.get("heartbeat_key") if lock_data else None

            if heartbeat_key:
                heartbeat_alive = await self.redis.exists(heartbeat_key)
                if heartbeat_alive:
                    logger.info(
                        f"Lock owner for {video_id} is alive (heartbeat present), skipping"
                    )
                    return

                # Component 1d: heartbeat expired — original worker is dead
                logger.warning(
                    f"Lock owner for {video_id} appears dead (heartbeat expired). "
                    "Force-releasing lock and cleaning up orphaned status."
                )
                await cleanup_orphaned_status(video_id)
                await archive_active_to_history(video_id)
                await force_release_lock(video_id)

                # Re-acquire lock for this worker
                lock_acquired = await acquire_lock(video_id, request_id, self.consumer_name)
                if not lock_acquired:
                    logger.error(
                        f"Failed to re-acquire lock for {video_id} after force-release. "
                        "Another worker may have picked it up simultaneously."
                    )
                    return
                logger.info(f"Successfully re-acquired lock for {video_id} after crash recovery")
            else:
                # No heartbeat key in lock data — legacy lock or unknown owner; skip
                logger.warning(
                    f"Could not acquire lock for {video_id} and no heartbeat key found. "
                    "Skipping to avoid conflicting with an unknown lock owner."
                )
                return

        pipeline_history_id: Optional[int] = None
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            pipeline_history_id = await self._create_db_history_record(
                request_id=request_id,
                video_id=video_id,
                pipeline_type="full",
                started_at=started_at,
            )

            result = await execute_pipeline(
                video_id, config, pipeline_history_id=pipeline_history_id
            )

            if pipeline_history_id:
                await self._update_db_history_record(pipeline_history_id, result, started_at)

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

        Ensures the message is always acknowledged even if processing fails,
        preventing message redelivery for failed pipelines.
        """
        try:
            await self._process_message(message)
        finally:
            await self._acknowledge_message(message["id"])

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self):
        """
        Main worker loop with support for concurrent pipeline execution.

        Maintains a set of active pipeline tasks up to max_concurrent_pipelines
        limit. Each pipeline runs as an independent async task.
        """
        self.redis = await get_async_redis_client()

        await self.ensure_consumer_group()

        # Component 4: clean up orphaned DB records from previous crashes
        await self._cleanup_orphaned_db_records()

        # Component 1a: start heartbeat background task
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        logger.info(
            f"Pipeline worker started: {self.consumer_name} "
            f"(max concurrent pipelines: {self.max_concurrent_pipelines})"
        )

        active_tasks: set = set()

        try:
            while self.running:
                try:
                    # Clean up completed tasks
                    done_tasks = {t for t in active_tasks if t.done()}
                    for task in done_tasks:
                        try:
                            await task
                        except Exception as e:
                            logger.error(f"Pipeline task failed: {e}")
                    active_tasks -= done_tasks

                    if len(active_tasks) < self.max_concurrent_pipelines:
                        message = await self._claim_stale_message()

                        if message is None:
                            message = await self._read_new_message()

                        if message:
                            task = asyncio.create_task(
                                self._process_and_acknowledge(message)
                            )
                            active_tasks.add(task)
                            logger.info(
                                f"Started pipeline task for {message['video_id']} "
                                f"({len(active_tasks)}/{self.max_concurrent_pipelines} active)"
                            )
                    else:
                        await asyncio.sleep(0.5)

                except Exception as e:
                    logger.exception(f"Error in worker loop: {e}")
                    await asyncio.sleep(1)
        finally:
            # Stop heartbeat before waiting for active tasks
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

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
