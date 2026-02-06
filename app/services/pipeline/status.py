"""
Pipeline status tracking using Redis Hash.
Each pipeline stores its status in a Redis Hash: pipeline:{video_id}:status

All functions are async for non-blocking Redis operations.
"""
import json
import time
import logging
from typing import Optional, Dict, Any
from app.core.redis import get_async_redis_client
from app.models.pipeline_schemas import PipelineStage, StageStatus

logger = logging.getLogger(__name__)


def _get_status_key(video_id: str) -> str:
    """Get Redis key for ACTIVE pipeline status."""
    return f"pipeline:{video_id}:active"


def _get_stage_prefix(stage: PipelineStage) -> str:
    """Get the prefix for stage fields in the hash."""
    return stage.value


async def initialize_status(video_id: str, request_id: str, config: dict) -> None:
    """
    Initialize pipeline status in Redis Hash.
    
    Args:
        video_id: Video identifier
        request_id: Unique request ID for this pipeline run
        config: Pipeline configuration dictionary
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    
    # Base status fields
    status_data = {
        "request_id": request_id,
        "video_id": video_id,
        "status": "pending",
        "generation_model": config.get("generation_model", "qwen3_vl_fp8"),
        "refinement_model": config.get("refinement_model", "qwen3_vl_fp8"),
        "config": json.dumps(config),
        "started_at": str(time.time()),
        "completed_at": "",
        "current_stage": "",
        "error_stage": "",
        "error_message": "",
    }
    
    # Initialize all stage statuses to pending
    for stage in PipelineStage:
        prefix = _get_stage_prefix(stage)
        status_data[f"{prefix}_status"] = StageStatus.PENDING.value
        status_data[f"{prefix}_started_at"] = ""
        status_data[f"{prefix}_completed_at"] = ""
        status_data[f"{prefix}_skipped"] = "false"
        status_data[f"{prefix}_skip_reason"] = ""
    
    # Special fields for refinement progress
    status_data["refinement_total"] = "0"
    status_data["refinement_processed"] = "0"
    status_data["refinement_successful"] = "0"
    
    # Special fields for clip extraction progress
    status_data["clips_total"] = "0"
    status_data["clips_processed"] = "0"
    status_data["clips_failed"] = "0"
    
    # Special fields for video download progress
    status_data["download_bytes"] = "0"
    status_data["download_total"] = "0"
    status_data["download_percentage"] = "0"
    
    # Special fields for audio upload progress
    status_data["upload_bytes"] = "0"
    status_data["upload_total"] = "0"
    status_data["upload_percentage"] = "0"
    
    # Special fields for clip upload progress
    status_data["clip_upload_current"] = "0"
    status_data["clip_upload_total_clips"] = "0"
    status_data["clip_upload_bytes"] = "0"
    status_data["clip_upload_total_bytes"] = "0"
    status_data["clip_upload_percentage"] = "0"
    
    await redis.hset(status_key, mapping=status_data)
    logger.info(f"Initialized pipeline status for {video_id}: {request_id}")


async def update_stage_status(video_id: str, stage: PipelineStage, 
                        status: StageStatus, **kwargs) -> None:
    """
    Update a specific stage's status with additional data.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
        status: New status for the stage
        **kwargs: Additional fields to update
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    updates = {f"{prefix}_status": status.value}
    
    # Add any additional fields
    for key, value in kwargs.items():
        updates[f"{prefix}_{key}"] = str(value)
    
    await redis.hset(status_key, mapping=updates)
    logger.debug(f"Updated {stage.value} status to {status.value} for {video_id}")


async def mark_stage_started(video_id: str, stage: PipelineStage) -> None:
    """
    Mark a stage as started and record the start time.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    updates = {
        f"{prefix}_status": StageStatus.PROCESSING.value,
        f"{prefix}_started_at": str(time.time()),
    }
    
    await redis.hset(status_key, mapping=updates)
    logger.info(f"Started stage {stage.value} for {video_id}")


async def mark_stage_completed(video_id: str, stage: PipelineStage) -> None:
    """
    Mark a stage as completed and record the end time.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    # Get start time to calculate duration
    start_time_str = await redis.hget(status_key, f"{prefix}_started_at")
    current_time = time.time()
    
    updates = {
        f"{prefix}_status": StageStatus.COMPLETED.value,
        f"{prefix}_completed_at": str(current_time),
    }
    
    await redis.hset(status_key, mapping=updates)
    
    # Log with duration if available
    if start_time_str:
        try:
            duration = current_time - float(start_time_str)
            logger.info(f"Completed stage {stage.value} for {video_id} in {duration:.2f}s")
        except ValueError:
            logger.info(f"Completed stage {stage.value} for {video_id}")
    else:
        logger.info(f"Completed stage {stage.value} for {video_id}")


async def mark_stage_skipped(video_id: str, stage: PipelineStage, reason: str) -> None:
    """
    Mark a stage as skipped with a reason.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
        reason: Reason for skipping
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    updates = {
        f"{prefix}_status": StageStatus.SKIPPED.value,
        f"{prefix}_skipped": "true",
        f"{prefix}_skip_reason": reason,
    }
    
    await redis.hset(status_key, mapping=updates)
    logger.info(f"Skipped stage {stage.value} for {video_id}: {reason}")


async def mark_stage_failed(video_id: str, stage: PipelineStage, error: str) -> None:
    """
    Mark a stage as failed with an error message.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
        error: Error message
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    current_time = time.time()
    
    updates = {
        f"{prefix}_status": StageStatus.FAILED.value,
        f"{prefix}_completed_at": str(current_time),
        "error_stage": stage.value,
        "error_message": error,
    }
    
    await redis.hset(status_key, mapping=updates)
    logger.error(f"Failed stage {stage.value} for {video_id}: {error}")


async def update_pipeline_status(video_id: str, status: str) -> None:
    """
    Update the overall pipeline status.
    
    Args:
        video_id: Video identifier
        status: Pipeline status (pending, processing, completed, failed, cancelled)
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    
    updates = {"status": status}
    
    if status in ["completed", "failed", "cancelled"]:
        updates["completed_at"] = str(time.time())
    
    await redis.hset(status_key, mapping=updates)
    logger.info(f"Updated pipeline status to {status} for {video_id}")


async def update_current_stage(video_id: str, stage: PipelineStage) -> None:
    """
    Update the current stage being processed.
    
    Args:
        video_id: Video identifier
        stage: Current pipeline stage
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    await redis.hset(status_key, "current_stage", stage.value)


async def get_current_stage(video_id: str) -> Optional[str]:
    """
    Get the current stage being processed.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Current stage name or None
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    current_stage = await redis.hget(status_key, "current_stage")
    return current_stage if current_stage else None


async def update_refinement_progress(video_id: str, total: int, processed: int, successful: int = None) -> None:
    """
    Update refinement progress.
    
    Args:
        video_id: Video identifier
        total: Total number of moments to refine
        processed: Number of moments processed (attempted) so far
        successful: Number of moments successfully refined (optional)
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    mapping = {
        "refinement_total": str(total),
        "refinement_processed": str(processed),
    }
    if successful is not None:
        mapping["refinement_successful"] = str(successful)
    await redis.hset(status_key, mapping=mapping)


async def update_clip_extraction_progress(video_id: str, total: int, processed: int, failed: int = 0) -> None:
    """
    Update clip extraction progress.
    
    Args:
        video_id: Video identifier
        total: Total number of clips to extract
        processed: Number of clips processed so far
        failed: Number of clips that failed (optional)
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    mapping = {
        "clips_total": str(total),
        "clips_processed": str(processed),
        "clips_failed": str(failed),
    }
    await redis.hset(status_key, mapping=mapping)


async def get_status(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Get current pipeline status from Redis.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Status dictionary or None if not found
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    
    status_data = await redis.hgetall(status_key)
    
    if not status_data:
        return None
    
    return status_data


async def delete_status(video_id: str) -> None:
    """
    Delete pipeline status from Redis (after saving to history).
    
    Args:
        video_id: Video identifier
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    await redis.delete(status_key)
    logger.info(f"Deleted pipeline status for {video_id}")


async def get_active_status(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Get active pipeline status (alias for get_status for clarity).
    
    Args:
        video_id: Video identifier
        
    Returns:
        Status dictionary or None if not found
    """
    return await get_status(video_id)


async def get_stage_status(video_id: str, stage: PipelineStage) -> Optional[StageStatus]:
    """
    Get current status of a specific pipeline stage.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage to check
        
    Returns:
        Stage status or None if pipeline not found
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    status_str = await redis.hget(status_key, f"{prefix}_status")
    
    if not status_str:
        return None
    
    try:
        return StageStatus(status_str)
    except ValueError:
        return None


async def get_stage_error(video_id: str, stage: PipelineStage) -> Optional[str]:
    """
    Get error message for a failed stage.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage to check
        
    Returns:
        Error message or None if no error
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    
    # Check if this is the error stage
    error_stage = await redis.hget(status_key, "error_stage")
    if error_stage == stage.value:
        error_message = await redis.hget(status_key, "error_message")
        return error_message if error_message else None
    
    return None


async def set_stage_error(video_id: str, stage: PipelineStage, error: str) -> None:
    """
    Set error message on a stage (for use by service threads).
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage that failed
        error: Error message
    """
    redis = await get_async_redis_client()
    status_key = _get_status_key(video_id)
    
    updates = {
        "error_stage": stage.value,
        "error_message": error,
    }
    
    await redis.hset(status_key, mapping=updates)
    logger.error(f"Set error on stage {stage.value} for {video_id}: {error}")
