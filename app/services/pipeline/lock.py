"""
Pipeline lock mechanism using Redis.
Prevents concurrent pipeline runs for the same video.

All functions are async for non-blocking Redis operations.
"""
import json
import time
import logging
from typing import Tuple, Optional, Dict
from app.core.redis import get_async_redis_client
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Lock TTL - 30 minutes
LOCK_TTL = 1800


def _get_lock_key(video_id: str) -> str:
    """Get Redis key for pipeline lock."""
    return f"pipeline:{video_id}:lock"


def _get_cancel_key(video_id: str) -> str:
    """Get Redis key for cancellation flag."""
    return f"pipeline:{video_id}:cancel"


async def acquire_lock(video_id: str, request_id: str) -> bool:
    """
    Acquire exclusive lock for pipeline processing.
    Uses Redis SET NX (set if not exists) for atomic acquisition.
    
    Args:
        video_id: Video identifier
        request_id: Unique request ID for this pipeline run
    
    Returns:
        True if lock acquired, False if already locked
    """
    redis = await get_async_redis_client()
    lock_key = _get_lock_key(video_id)
    
    lock_data = {
        "request_id": request_id,
        "locked_at": time.time(),
        "container_id": get_settings().container_id,
    }
    
    # Try to set lock with NX (only if not exists) and TTL
    success = await redis.set(
        lock_key,
        json.dumps(lock_data),
        nx=True,  # Only set if key doesn't exist
        ex=LOCK_TTL  # TTL in seconds
    )
    
    if success:
        logger.info(f"Acquired pipeline lock for {video_id}: {request_id}")
        return True
    else:
        logger.warning(f"Failed to acquire lock for {video_id} (already locked)")
        return False


async def release_lock(video_id: str) -> None:
    """
    Release pipeline lock.
    
    Args:
        video_id: Video identifier
    """
    redis = await get_async_redis_client()
    lock_key = _get_lock_key(video_id)
    await redis.delete(lock_key)
    logger.info(f"Released pipeline lock for {video_id}")


async def is_locked(video_id: str) -> Tuple[bool, Optional[Dict]]:
    """
    Check if pipeline is locked for a video.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Tuple of (is_locked, lock_info_dict)
    """
    redis = await get_async_redis_client()
    lock_key = _get_lock_key(video_id)
    
    lock_data = await redis.get(lock_key)
    
    if lock_data:
        try:
            lock_info = json.loads(lock_data)
            return True, lock_info
        except json.JSONDecodeError:
            logger.error(f"Failed to decode lock data for {video_id}")
            return True, None
    
    return False, None


async def refresh_lock(video_id: str) -> bool:
    """
    Refresh lock TTL during long operations.
    
    Args:
        video_id: Video identifier
    
    Returns:
        True if lock was refreshed, False if lock doesn't exist
    """
    redis = await get_async_redis_client()
    lock_key = _get_lock_key(video_id)
    
    # Check if lock exists
    if await redis.exists(lock_key):
        await redis.expire(lock_key, LOCK_TTL)
        logger.debug(f"Refreshed pipeline lock for {video_id}")
        return True
    
    return False


# Cancellation functions


async def set_cancellation_flag(video_id: str) -> None:
    """
    Set cancellation flag for a running pipeline.
    Worker checks this between stages.
    
    Args:
        video_id: Video identifier
    """
    redis = await get_async_redis_client()
    cancel_key = _get_cancel_key(video_id)
    
    # Set cancel flag with 5 minute TTL
    await redis.set(cancel_key, "1", ex=300)
    logger.info(f"Set cancellation flag for {video_id}")


async def check_cancellation(video_id: str) -> bool:
    """
    Check if cancellation was requested.
    Called by worker between stages.
    
    Args:
        video_id: Video identifier
    
    Returns:
        True if cancellation was requested
    """
    redis = await get_async_redis_client()
    cancel_key = _get_cancel_key(video_id)
    
    return await redis.exists(cancel_key) > 0


async def clear_cancellation(video_id: str) -> None:
    """
    Clear cancellation flag after handling.
    
    Args:
        video_id: Video identifier
    """
    redis = await get_async_redis_client()
    cancel_key = _get_cancel_key(video_id)
    await redis.delete(cancel_key)
    logger.info(f"Cleared cancellation flag for {video_id}")
