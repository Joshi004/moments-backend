"""
Pipeline history persistence using Redis.
Stores completed pipeline runs with 24-hour TTL.
"""
import json
import time
import logging
from typing import List, Dict, Optional, Any
from app.core.redis import get_redis_client
from app.core.config import get_settings
from app.models.pipeline_schemas import PipelineStage

logger = logging.getLogger(__name__)


def _get_run_key(request_id: str) -> str:
    """Get Redis key for a pipeline run."""
    return f"pipeline:run:{request_id}"


def _get_history_key(video_id: str) -> str:
    """Get Redis key for video's run history (sorted set)."""
    return f"pipeline:{video_id}:history"


def archive_active_to_history(video_id: str) -> Optional[str]:
    """
    Move active pipeline to history with TTL.
    
    Args:
        video_id: Video identifier
        
    Returns:
        request_id of archived run, or None if no active pipeline
    """
    redis = get_redis_client()
    settings = get_settings()
    
    # Get active pipeline key
    active_key = f"pipeline:{video_id}:active"
    status_data = redis.hgetall(active_key)
    
    if not status_data:
        logger.warning(f"No active pipeline found for {video_id}, cannot archive")
        return None
    
    # Extract request_id and timestamp
    request_id = status_data.get("request_id", "")
    if not request_id:
        logger.error(f"Active pipeline for {video_id} has no request_id")
        return None
    
    completed_at_str = status_data.get("completed_at", "")
    if completed_at_str:
        try:
            timestamp = float(completed_at_str)
        except ValueError:
            timestamp = time.time()
    else:
        timestamp = time.time()
    
    # Save to run hash with TTL
    run_key = _get_run_key(request_id)
    redis.hset(run_key, mapping=status_data)
    redis.expire(run_key, settings.pipeline_history_ttl)
    
    # Add to history sorted set (score = timestamp for chronological ordering)
    history_key = _get_history_key(video_id)
    redis.zadd(history_key, {request_id: timestamp})
    
    # Cleanup old runs if exceeding max limit
    cleanup_old_runs(video_id)
    
    # Delete active pipeline
    redis.delete(active_key)
    
    logger.info(f"Archived pipeline run to Redis: {request_id} for {video_id} with {settings.pipeline_history_ttl}s TTL")
    
    return request_id


def get_run_by_id(request_id: str) -> Optional[Dict[str, Any]]:
    """
    Get specific pipeline run by request_id.
    
    Args:
        request_id: Pipeline request identifier
        
    Returns:
        Run data dictionary or None if not found
    """
    redis = get_redis_client()
    run_key = _get_run_key(request_id)
    
    run_data = redis.hgetall(run_key)
    
    if not run_data:
        return None
    
    return run_data


def get_latest_run(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the most recent completed pipeline run for a video.
    
    Args:
        video_id: Video identifier
        
    Returns:
        Latest run data or None if no history
    """
    redis = get_redis_client()
    history_key = _get_history_key(video_id)
    
    # Get most recent run (highest score = latest timestamp)
    run_ids = redis.zrevrange(history_key, 0, 0)
    
    if not run_ids:
        return None
    
    request_id = run_ids[0]
    return get_run_by_id(request_id)


def get_all_runs(video_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Get all pipeline runs for a video, ordered from most recent to oldest.
    
    Args:
        video_id: Video identifier
        limit: Optional limit on number of runs to return
        
    Returns:
        List of run data dictionaries (newest first)
    """
    redis = get_redis_client()
    history_key = _get_history_key(video_id)
    
    # Get all run IDs in reverse chronological order
    if limit:
        run_ids = redis.zrevrange(history_key, 0, limit - 1)
    else:
        run_ids = redis.zrevrange(history_key, 0, -1)
    
    if not run_ids:
        return []
    
    # Fetch each run's data
    runs = []
    for request_id in run_ids:
        run_data = get_run_by_id(request_id)
        if run_data:
            runs.append(run_data)
    
    return runs


def cleanup_old_runs(video_id: str) -> int:
    """
    Remove runs beyond the max_runs limit for a video.
    Keeps the most recent runs.
    
    Args:
        video_id: Video identifier
        
    Returns:
        Number of runs removed
    """
    redis = get_redis_client()
    settings = get_settings()
    history_key = _get_history_key(video_id)
    
    # Count total runs
    total_runs = redis.zcard(history_key)
    
    if total_runs <= settings.pipeline_history_max_runs:
        return 0
    
    # Calculate how many to remove
    to_remove = total_runs - settings.pipeline_history_max_runs
    
    # Get oldest run IDs to remove (lowest scores)
    old_run_ids = redis.zrange(history_key, 0, to_remove - 1)
    
    # Remove from sorted set
    removed_count = 0
    for request_id in old_run_ids:
        # Remove from sorted set
        redis.zrem(history_key, request_id)
        
        # Delete the run hash (it might already be expired by TTL, but clean up anyway)
        run_key = _get_run_key(request_id)
        redis.delete(run_key)
        
        removed_count += 1
    
    logger.info(f"Cleaned up {removed_count} old pipeline runs for {video_id}")
    
    return removed_count


def delete_all_history(video_id: str) -> bool:
    """
    Delete all history for a video (for testing/cleanup).
    
    Args:
        video_id: Video identifier
        
    Returns:
        True if deleted, False otherwise
    """
    redis = get_redis_client()
    history_key = _get_history_key(video_id)
    
    # Get all run IDs
    run_ids = redis.zrange(history_key, 0, -1)
    
    # Delete all run hashes
    for request_id in run_ids:
        run_key = _get_run_key(request_id)
        redis.delete(run_key)
    
    # Delete the sorted set
    redis.delete(history_key)
    
    logger.info(f"Deleted all pipeline history for {video_id}")
    
    return True

