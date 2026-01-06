"""
Pipeline status tracking using Redis Hash.
Each pipeline stores its status in a Redis Hash: pipeline:{video_id}:status
"""
import json
import time
import logging
from typing import Optional, Dict, Any
from app.core.redis import get_redis_client
from app.models.pipeline_schemas import PipelineStage, StageStatus

logger = logging.getLogger(__name__)


def _get_status_key(video_id: str) -> str:
    """Get Redis key for pipeline status."""
    return f"pipeline:{video_id}:status"


def _get_stage_prefix(stage: PipelineStage) -> str:
    """Get the prefix for stage fields in the hash."""
    return stage.value


def initialize_status(video_id: str, request_id: str, config: dict) -> None:
    """
    Initialize pipeline status in Redis Hash.
    
    Args:
        video_id: Video identifier
        request_id: Unique request ID for this pipeline run
        config: Pipeline configuration dictionary
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    
    # Base status fields
    status_data = {
        "request_id": request_id,
        "video_id": video_id,
        "status": "pending",
        "model": config.get("model", "qwen3_vl_fp8"),
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
    
    redis.hset(status_key, mapping=status_data)
    logger.info(f"Initialized pipeline status for {video_id}: {request_id}")


def update_stage_status(video_id: str, stage: PipelineStage, 
                        status: StageStatus, **kwargs) -> None:
    """
    Update a specific stage's status with additional data.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
        status: New status for the stage
        **kwargs: Additional fields to update
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    updates = {f"{prefix}_status": status.value}
    
    # Add any additional fields
    for key, value in kwargs.items():
        updates[f"{prefix}_{key}"] = str(value)
    
    redis.hset(status_key, mapping=updates)
    logger.debug(f"Updated {stage.value} status to {status.value} for {video_id}")


def mark_stage_started(video_id: str, stage: PipelineStage) -> None:
    """
    Mark a stage as started and record the start time.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    updates = {
        f"{prefix}_status": StageStatus.PROCESSING.value,
        f"{prefix}_started_at": str(time.time()),
    }
    
    redis.hset(status_key, mapping=updates)
    logger.info(f"Started stage {stage.value} for {video_id}")


def mark_stage_completed(video_id: str, stage: PipelineStage) -> None:
    """
    Mark a stage as completed and record the end time.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    # Get start time to calculate duration
    start_time_str = redis.hget(status_key, f"{prefix}_started_at")
    current_time = time.time()
    
    updates = {
        f"{prefix}_status": StageStatus.COMPLETED.value,
        f"{prefix}_completed_at": str(current_time),
    }
    
    redis.hset(status_key, mapping=updates)
    
    # Log with duration if available
    if start_time_str:
        try:
            duration = current_time - float(start_time_str)
            logger.info(f"Completed stage {stage.value} for {video_id} in {duration:.2f}s")
        except ValueError:
            logger.info(f"Completed stage {stage.value} for {video_id}")
    else:
        logger.info(f"Completed stage {stage.value} for {video_id}")


def mark_stage_skipped(video_id: str, stage: PipelineStage, reason: str) -> None:
    """
    Mark a stage as skipped with a reason.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
        reason: Reason for skipping
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    updates = {
        f"{prefix}_status": StageStatus.SKIPPED.value,
        f"{prefix}_skipped": "true",
        f"{prefix}_skip_reason": reason,
    }
    
    redis.hset(status_key, mapping=updates)
    logger.info(f"Skipped stage {stage.value} for {video_id}: {reason}")


def mark_stage_failed(video_id: str, stage: PipelineStage, error: str) -> None:
    """
    Mark a stage as failed with an error message.
    
    Args:
        video_id: Video identifier
        stage: Pipeline stage
        error: Error message
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    prefix = _get_stage_prefix(stage)
    
    current_time = time.time()
    
    updates = {
        f"{prefix}_status": StageStatus.FAILED.value,
        f"{prefix}_completed_at": str(current_time),
        "error_stage": stage.value,
        "error_message": error,
    }
    
    redis.hset(status_key, mapping=updates)
    logger.error(f"Failed stage {stage.value} for {video_id}: {error}")


def update_pipeline_status(video_id: str, status: str) -> None:
    """
    Update the overall pipeline status.
    
    Args:
        video_id: Video identifier
        status: Pipeline status (pending, processing, completed, failed, cancelled)
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    
    updates = {"status": status}
    
    if status in ["completed", "failed", "cancelled"]:
        updates["completed_at"] = str(time.time())
    
    redis.hset(status_key, mapping=updates)
    logger.info(f"Updated pipeline status to {status} for {video_id}")


def update_current_stage(video_id: str, stage: PipelineStage) -> None:
    """
    Update the current stage being processed.
    
    Args:
        video_id: Video identifier
        stage: Current pipeline stage
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    redis.hset(status_key, "current_stage", stage.value)


def get_current_stage(video_id: str) -> Optional[str]:
    """
    Get the current stage being processed.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Current stage name or None
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    current_stage = redis.hget(status_key, "current_stage")
    return current_stage if current_stage else None


def update_refinement_progress(video_id: str, total: int, processed: int) -> None:
    """
    Update refinement progress.
    
    Args:
        video_id: Video identifier
        total: Total number of moments to refine
        processed: Number of moments processed so far
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    redis.hset(status_key, mapping={
        "refinement_total": str(total),
        "refinement_processed": str(processed),
    })


def get_status(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Get current pipeline status from Redis.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Status dictionary or None if not found
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    
    status_data = redis.hgetall(status_key)
    
    if not status_data:
        return None
    
    return status_data


def delete_status(video_id: str) -> None:
    """
    Delete pipeline status from Redis (after saving to history).
    
    Args:
        video_id: Video identifier
    """
    redis = get_redis_client()
    status_key = _get_status_key(video_id)
    redis.delete(status_key)
    logger.info(f"Deleted pipeline status for {video_id}")





