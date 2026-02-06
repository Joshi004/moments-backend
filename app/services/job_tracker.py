"""
Async job tracking using Redis Hashes.
Clean replacement for the deprecated JobRepository system.

Each job is stored as a Redis Hash: job:{job_type}:{video_id}[:{sub_id}]
Uses async Redis client for non-blocking operations.
"""
import time
import logging
from typing import Optional, Dict, Any
from app.core.redis import get_async_redis_client
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _get_job_key(job_type: str, video_id: str, sub_id: Optional[str] = None) -> str:
    """
    Generate Redis key for a job.
    
    Args:
        job_type: Type of job (e.g., "clip_extraction", "audio_extraction")
        video_id: Video identifier
        sub_id: Optional sub-identifier (e.g., moment_id for refinement)
        
    Returns:
        Redis key string
    """
    if sub_id:
        return f"job:{job_type}:{video_id}:{sub_id}"
    return f"job:{job_type}:{video_id}"


async def create_job(
    job_type: str,
    video_id: str,
    sub_id: Optional[str] = None,
    **kwargs
) -> bool:
    """
    Create a new job with atomic lock acquisition.
    
    Args:
        job_type: Type of job
        video_id: Video identifier
        sub_id: Optional sub-identifier
        **kwargs: Additional job-specific fields
        
    Returns:
        True if job created, False if already exists
    """
    settings = get_settings()
    redis = await get_async_redis_client()
    job_key = _get_job_key(job_type, video_id, sub_id)
    
    # Check if job already exists
    exists = await redis.exists(job_key)
    if exists:
        logger.warning(f"Job already exists: {job_key}")
        return False
    
    # Create job hash with base fields
    job_data = {
        "job_type": job_type,
        "video_id": video_id,
        "status": "processing",
        "started_at": str(time.time()),
        "completed_at": "",
        "error": "",
        **{k: str(v) for k, v in kwargs.items()}
    }
    
    if sub_id:
        job_data["sub_id"] = sub_id
    
    # Set hash with TTL
    await redis.hset(job_key, mapping=job_data)
    await redis.expire(job_key, settings.job_lock_ttl)
    
    logger.info(f"Created job: {job_key}")
    return True


async def get_job(
    job_type: str,
    video_id: str,
    sub_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get job data from Redis.
    
    Args:
        job_type: Type of job
        video_id: Video identifier
        sub_id: Optional sub-identifier
        
    Returns:
        Job dictionary or None if not found
    """
    redis = await get_async_redis_client()
    job_key = _get_job_key(job_type, video_id, sub_id)
    
    job_data = await redis.hgetall(job_key)
    
    if not job_data:
        return None
    
    # Check for timeout
    if job_data.get("status") == "processing":
        try:
            settings = get_settings()
            started_at = float(job_data.get("started_at", "0"))
            elapsed = time.time() - started_at
            if elapsed > settings.job_lock_ttl - 60:  # Within 1 min of timeout
                await fail_job(job_type, video_id, "Job timed out", sub_id)
                job_data["status"] = "timeout"
                job_data["error"] = "Job timed out"
        except (ValueError, TypeError):
            pass
    
    return job_data


async def update_progress(
    job_type: str,
    video_id: str,
    sub_id: Optional[str] = None,
    **fields
) -> bool:
    """
    Update job progress fields.
    
    Args:
        job_type: Type of job
        video_id: Video identifier
        sub_id: Optional sub-identifier
        **fields: Fields to update (e.g., total_moments=10, processed_moments=5)
        
    Returns:
        True if updated, False if job not found
    """
    redis = await get_async_redis_client()
    job_key = _get_job_key(job_type, video_id, sub_id)
    
    exists = await redis.exists(job_key)
    if not exists:
        logger.warning(f"Cannot update non-existent job: {job_key}")
        return False
    
    # Convert all values to strings for Redis hash
    updates = {k: str(v) for k, v in fields.items()}
    
    await redis.hset(job_key, mapping=updates)
    logger.debug(f"Updated progress for job: {job_key}, fields: {list(fields.keys())}")
    
    return True


async def complete_job(
    job_type: str,
    video_id: str,
    sub_id: Optional[str] = None,
    **fields
) -> bool:
    """
    Mark job as completed.
    
    Args:
        job_type: Type of job
        video_id: Video identifier
        sub_id: Optional sub-identifier
        **fields: Optional final fields to set
        
    Returns:
        True if updated, False if job not found
    """
    settings = get_settings()
    redis = await get_async_redis_client()
    job_key = _get_job_key(job_type, video_id, sub_id)
    
    exists = await redis.exists(job_key)
    if not exists:
        logger.warning(f"Cannot complete non-existent job: {job_key}")
        return False
    
    updates = {
        "status": "completed",
        "completed_at": str(time.time()),
        **{k: str(v) for k, v in fields.items()}
    }
    
    await redis.hset(job_key, mapping=updates)
    
    # Set shorter TTL for completed jobs
    await redis.expire(job_key, settings.job_result_ttl)
    
    logger.info(f"Completed job: {job_key}")
    return True


async def fail_job(
    job_type: str,
    video_id: str,
    error: str,
    sub_id: Optional[str] = None
) -> bool:
    """
    Mark job as failed with error message.
    
    Args:
        job_type: Type of job
        video_id: Video identifier
        error: Error message
        sub_id: Optional sub-identifier
        
    Returns:
        True if updated, False if job not found
    """
    settings = get_settings()
    redis = await get_async_redis_client()
    job_key = _get_job_key(job_type, video_id, sub_id)
    
    exists = await redis.exists(job_key)
    if not exists:
        logger.warning(f"Cannot fail non-existent job: {job_key}")
        return False
    
    updates = {
        "status": "failed",
        "completed_at": str(time.time()),
        "error": error
    }
    
    await redis.hset(job_key, mapping=updates)
    
    # Set shorter TTL for failed jobs
    await redis.expire(job_key, settings.job_result_ttl)
    
    logger.error(f"Failed job: {job_key}, error: {error}")
    return True


async def is_running(
    job_type: str,
    video_id: str,
    sub_id: Optional[str] = None
) -> bool:
    """
    Check if a job is currently running.
    
    Args:
        job_type: Type of job
        video_id: Video identifier
        sub_id: Optional sub-identifier
        
    Returns:
        True if job is running, False otherwise
    """
    job = await get_job(job_type, video_id, sub_id)
    return job is not None and job.get("status") == "processing"


async def delete_job(
    job_type: str,
    video_id: str,
    sub_id: Optional[str] = None
) -> bool:
    """
    Delete a job from Redis.
    
    Args:
        job_type: Type of job
        video_id: Video identifier
        sub_id: Optional sub-identifier
        
    Returns:
        True if deleted, False if not found
    """
    redis = await get_async_redis_client()
    job_key = _get_job_key(job_type, video_id, sub_id)
    
    result = await redis.delete(job_key)
    
    if result > 0:
        logger.info(f"Deleted job: {job_key}")
        return True
    else:
        logger.warning(f"Job not found for deletion: {job_key}")
        return False
