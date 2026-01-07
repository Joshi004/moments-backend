"""
Pipeline worker - consumes pipeline requests from Redis Stream.
Executes complete pipelines asynchronously.
"""
import asyncio
import json
import logging
import signal
from typing import Optional, Dict, Any

from app.core.redis import get_redis_client
from app.core.config import get_settings
from app.services.pipeline.orchestrator import execute_pipeline
from app.services.pipeline.lock import acquire_lock, release_lock
from app.services.pipeline.redis_history import archive_active_to_history
from app.services.pipeline.status import delete_status, get_current_stage

logger = logging.getLogger(__name__)


class PipelineWorker:
    """Background worker that consumes pipeline requests from Redis Stream."""
    
    STREAM_KEY = "pipeline:requests"
    GROUP_NAME = "pipeline_workers"
    BLOCK_TIMEOUT_MS = 5000
    CLAIM_MIN_IDLE_MS = 60000  # 1 minute
    
    def __init__(self):
        """Initialize worker with Redis connection and settings."""
        self.redis = get_redis_client()
        self.settings = get_settings()
        self.consumer_name = f"worker-{self.settings.container_id}"
        self.running = True
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signal."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False
    
    def ensure_consumer_group(self):
        """Create consumer group if it doesn't exist."""
        try:
            self.redis.xgroup_create(
                self.STREAM_KEY,
                self.GROUP_NAME,
                id="0",
                mkstream=True
            )
            # #region agent log
            import json; open('/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.cursor/debug.log', 'a').write(json.dumps({"sessionId": "debug-session", "runId": "pre-fix", "hypothesisId": "B", "location": "pipeline_worker.py:54", "message": "Consumer group created", "data": {"stream_key": self.STREAM_KEY, "group_name": self.GROUP_NAME}, "timestamp": __import__('time').time() * 1000}) + '\n')
            # #endregion
            logger.info(f"Created consumer group '{self.GROUP_NAME}' on stream '{self.STREAM_KEY}'")
        except Exception as e:
            if "BUSYGROUP" in str(e):
                # #region agent log
                import json; open('/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.cursor/debug.log', 'a').write(json.dumps({"sessionId": "debug-session", "runId": "pre-fix", "hypothesisId": "B", "location": "pipeline_worker.py:57", "message": "Consumer group already exists", "data": {"stream_key": self.STREAM_KEY, "group_name": self.GROUP_NAME}, "timestamp": __import__('time').time() * 1000}) + '\n')
                # #endregion
                logger.debug("Consumer group already exists")
            else:
                logger.error(f"Error creating consumer group: {e}")
                raise
    
    async def _claim_stale_message(self) -> Optional[Dict[str, Any]]:
        """Try to claim stale messages from crashed workers."""
        try:
            result = self.redis.xautoclaim(
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
        """Read new message from stream."""
        # #region agent log
        import json; open('/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.cursor/debug.log', 'a').write(json.dumps({"sessionId": "debug-session", "runId": "pre-fix", "hypothesisId": "A,D", "location": "pipeline_worker.py:93", "message": "Before xreadgroup", "data": {"stream_key": self.STREAM_KEY, "group_name": self.GROUP_NAME, "consumer_name": self.consumer_name, "block_timeout_ms": self.BLOCK_TIMEOUT_MS}, "timestamp": __import__('time').time() * 1000}) + '\n')
        # #endregion
        try:
            result = self.redis.xreadgroup(
                groupname=self.GROUP_NAME,
                consumername=self.consumer_name,
                streams={self.STREAM_KEY: ">"},
                count=1,
                block=self.BLOCK_TIMEOUT_MS
            )
            
            # #region agent log
            import json; open('/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.cursor/debug.log', 'a').write(json.dumps({"sessionId": "debug-session", "runId": "pre-fix", "hypothesisId": "C", "location": "pipeline_worker.py:101", "message": "xreadgroup result", "data": {"result_type": str(type(result)), "result_bool": bool(result), "result_len": len(result) if result else 0}, "timestamp": __import__('time').time() * 1000}) + '\n')
            # #endregion
            if result:
                # result = [(stream_name, [(message_id, message_data), ...])]
                stream_name, messages = result[0]
                # #region agent log
                import json; open('/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.cursor/debug.log', 'a').write(json.dumps({"sessionId": "debug-session", "runId": "pre-fix", "hypothesisId": "C", "location": "pipeline_worker.py:104", "message": "Stream data parsed", "data": {"stream_name": stream_name, "messages_count": len(messages), "has_messages": bool(messages)}, "timestamp": __import__('time').time() * 1000}) + '\n')
                # #endregion
                if messages:
                    message_id, message_data = messages[0]
                    # #region agent log
                    import json; open('/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.cursor/debug.log', 'a').write(json.dumps({"sessionId": "debug-session", "runId": "pre-fix", "hypothesisId": "C", "location": "pipeline_worker.py:106", "message": "Message extracted", "data": {"message_id": message_id, "video_id": message_data.get("video_id"), "request_id": message_data.get("request_id"), "has_config": bool(message_data.get("config"))}, "timestamp": __import__('time').time() * 1000}) + '\n')
                    # #endregion
                    return {
                        "id": message_id,
                        "video_id": message_data.get("video_id"),
                        "request_id": message_data.get("request_id"),
                        "config": message_data.get("config"),
                    }
        except Exception as e:
            # #region agent log
            import json; open('/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.cursor/debug.log', 'a').write(json.dumps({"sessionId": "debug-session", "runId": "pre-fix", "hypothesisId": "A,E", "location": "pipeline_worker.py:113", "message": "xreadgroup exception", "data": {"error_type": type(e).__name__, "error_msg": str(e), "is_timeout": "timeout" in str(e).lower()}, "timestamp": __import__('time').time() * 1000}) + '\n')
            # #endregion
            logger.error(f"Error reading from stream: {e}")
        
        return None
    
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
        if not acquire_lock(video_id, request_id):
            logger.warning(f"Could not acquire lock for {video_id}, skipping")
            return
        
        try:
            # Execute pipeline
            result = await execute_pipeline(video_id, config)
            
            # Archive to Redis history with TTL
            try:
                run_id = archive_active_to_history(video_id)
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
            # Try to archive history even on failure
            try:
                run_id = archive_active_to_history(video_id)
                if run_id:
                    logger.info(f"Archived failed pipeline run to Redis: {run_id}")
            except Exception as hist_err:
                logger.error(f"Failed to archive history after error for {video_id}: {hist_err}")
        finally:
            release_lock(video_id)
    
    async def _acknowledge_message(self, message_id: str) -> None:
        """Acknowledge processed message."""
        try:
            self.redis.xack(self.STREAM_KEY, self.GROUP_NAME, message_id)
            logger.debug(f"Acknowledged message {message_id}")
        except Exception as e:
            logger.error(f"Error acknowledging message {message_id}: {e}")
    
    async def run(self):
        """Main worker loop."""
        self.ensure_consumer_group()
        logger.info(f"Pipeline worker started: {self.consumer_name}")
        
        while self.running:
            try:
                # Try to claim stale messages first
                message = await self._claim_stale_message()
                
                # If no stale messages, read new ones
                if message is None:
                    message = await self._read_new_message()
                
                if message:
                    await self._process_message(message)
                    await self._acknowledge_message(message["id"])
                    
            except Exception as e:
                logger.exception(f"Error in worker loop: {e}")
                await asyncio.sleep(1)
        
        logger.info(f"Pipeline worker {self.consumer_name} shutting down")


def ensure_pipeline_consumer_group():
    """
    Ensure consumer group exists (called on startup).
    Can be called from synchronous context.
    """
    redis = get_redis_client()
    try:
        redis.xgroup_create("pipeline:requests", "pipeline_workers", id="0", mkstream=True)
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





