"""
Pipeline history persistence using Redis.
Stores completed pipeline runs with 24-hour TTL.

All functions are async for non-blocking Redis operations.
"""
import json
import time
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from app.core.redis import get_async_redis_client
from app.core.config import get_settings
from app.models.pipeline_schemas import PipelineStage

logger = logging.getLogger(__name__)


def _get_run_key(request_id: str) -> str:
    """Get Redis key for a pipeline run."""
    return f"pipeline:run:{request_id}"


def _get_history_key(video_id: str) -> str:
    """Get Redis key for video's run history (sorted set)."""
    return f"pipeline:{video_id}:history"


async def archive_active_to_history(
    video_id: str,
    pipeline_history_id: Optional[int] = None,
) -> Optional[str]:
    """
    Move active pipeline to history with TTL.

    Also acts as a DB safety net: if pipeline_history_id is provided and the
    DB record is still in 'running' status (e.g. the worker crashed before
    it could update), this function finalises it from the Redis hash data.

    Args:
        video_id: Video identifier
        pipeline_history_id: Optional numeric DB id of the pipeline_history record

    Returns:
        request_id of archived run, or None if no active pipeline
    """
    redis = await get_async_redis_client()
    settings = get_settings()

    # Get active pipeline key
    active_key = f"pipeline:{video_id}:active"
    status_data = await redis.hgetall(active_key)

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
    await redis.hset(run_key, mapping=status_data)
    await redis.expire(run_key, settings.pipeline_history_ttl)

    # Add to history sorted set (score = timestamp for chronological ordering)
    history_key = _get_history_key(video_id)
    await redis.zadd(history_key, {request_id: timestamp})

    # Cleanup old runs if exceeding max limit
    await cleanup_old_runs(video_id)

    # Delete active pipeline
    await redis.delete(active_key)

    logger.info(
        f"Archived pipeline run to Redis: {request_id} for {video_id} "
        f"with {settings.pipeline_history_ttl}s TTL"
    )

    # DB safety net: ensure the pipeline_history record is finalised
    if pipeline_history_id is not None:
        await _db_safety_net(pipeline_history_id, status_data)

    return request_id


async def _db_safety_net(
    pipeline_history_id: int,
    status_data: Dict[str, Any],
) -> None:
    """
    Finalise a pipeline_history DB record if it is still in 'running' state.

    This runs after the primary worker update. It only acts when the record
    was not updated (e.g. the worker crashed before the DB write). It extracts
    the final state from the archived Redis hash data.
    """
    try:
        from app.database.session import get_session_factory
        from app.repositories import pipeline_history_db_repository

        session_factory = get_session_factory()
        async with session_factory() as session:
            record = await pipeline_history_db_repository.get_by_id(session, pipeline_history_id)
            if record is None or record.status != "running":
                return

            logger.warning(
                f"Safety net: DB pipeline_history id={pipeline_history_id} "
                "still 'running' after archive; finalising from Redis data"
            )

            # Decode bytes values from Redis
            def _str(val) -> str:
                if isinstance(val, bytes):
                    return val.decode()
                return val or ""

            redis_status = _str(status_data.get("status", ""))
            if redis_status in ("completed", "failed", "cancelled"):
                final_status = redis_status
            else:
                final_status = "failed"

            completed_at_str = _str(status_data.get("completed_at", ""))
            started_at_str = _str(status_data.get("started_at", ""))
            duration_seconds = None
            completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if completed_at_str:
                try:
                    ts = float(completed_at_str)
                    completed_at = datetime.utcfromtimestamp(ts)
                    if started_at_str:
                        started_ts = float(started_at_str)
                        duration_seconds = ts - started_ts
                except ValueError:
                    pass

            clips_processed_str = _str(status_data.get("clips_processed", ""))
            total_clips = int(clips_processed_str) if clips_processed_str.isdigit() else None

            error_stage = _str(status_data.get("error_stage", "")) or None
            error_message = _str(status_data.get("error_message", "")) or None

            await pipeline_history_db_repository.update_status(
                session,
                history_id=pipeline_history_id,
                status=final_status,
                completed_at=completed_at,
                duration_seconds=duration_seconds,
                total_clips_created=total_clips,
                error_stage=error_stage,
                error_message=error_message,
            )
            await session.commit()
            logger.info(
                f"Safety net: finalised pipeline_history id={pipeline_history_id} "
                f"status={final_status}"
            )
    except Exception as e:
        logger.error(f"DB safety net failed for pipeline_history id={pipeline_history_id}: {e}")


async def get_run_by_id(request_id: str) -> Optional[Dict[str, Any]]:
    """
    Get specific pipeline run by request_id.
    
    Args:
        request_id: Pipeline request identifier
        
    Returns:
        Run data dictionary or None if not found
    """
    redis = await get_async_redis_client()
    run_key = _get_run_key(request_id)
    
    run_data = await redis.hgetall(run_key)
    
    if not run_data:
        return None
    
    return run_data


async def get_latest_run(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the most recent completed pipeline run for a video.
    
    Args:
        video_id: Video identifier
        
    Returns:
        Latest run data or None if no history
    """
    redis = await get_async_redis_client()
    history_key = _get_history_key(video_id)
    
    # Get most recent run (highest score = latest timestamp)
    run_ids = await redis.zrevrange(history_key, 0, 0)
    
    if not run_ids:
        return None
    
    request_id = run_ids[0]
    return await get_run_by_id(request_id)


async def get_all_runs(video_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Get all pipeline runs for a video, ordered from most recent to oldest.
    
    Args:
        video_id: Video identifier
        limit: Optional limit on number of runs to return
        
    Returns:
        List of run data dictionaries (newest first)
    """
    redis = await get_async_redis_client()
    history_key = _get_history_key(video_id)
    
    # Get all run IDs in reverse chronological order
    if limit:
        run_ids = await redis.zrevrange(history_key, 0, limit - 1)
    else:
        run_ids = await redis.zrevrange(history_key, 0, -1)
    
    if not run_ids:
        return []
    
    # Fetch each run's data
    runs = []
    for request_id in run_ids:
        run_data = await get_run_by_id(request_id)
        if run_data:
            runs.append(run_data)
    
    return runs


async def cleanup_old_runs(video_id: str) -> int:
    """
    Remove runs beyond the max_runs limit for a video.
    Keeps the most recent runs.
    
    Args:
        video_id: Video identifier
        
    Returns:
        Number of runs removed
    """
    redis = await get_async_redis_client()
    settings = get_settings()
    history_key = _get_history_key(video_id)
    
    # Count total runs
    total_runs = await redis.zcard(history_key)
    
    if total_runs <= settings.pipeline_history_max_runs:
        return 0
    
    # Calculate how many to remove
    to_remove = total_runs - settings.pipeline_history_max_runs
    
    # Get oldest run IDs to remove (lowest scores)
    old_run_ids = await redis.zrange(history_key, 0, to_remove - 1)
    
    # Remove from sorted set
    removed_count = 0
    for request_id in old_run_ids:
        # Remove from sorted set
        await redis.zrem(history_key, request_id)
        
        # Delete the run hash (it might already be expired by TTL, but clean up anyway)
        run_key = _get_run_key(request_id)
        await redis.delete(run_key)
        
        removed_count += 1
    
    logger.info(f"Cleaned up {removed_count} old pipeline runs for {video_id}")
    
    return removed_count


async def delete_all_history(video_id: str) -> bool:
    """
    Delete all history for a video (for testing/cleanup).
    
    Args:
        video_id: Video identifier
        
    Returns:
        True if deleted, False otherwise
    """
    redis = await get_async_redis_client()
    history_key = _get_history_key(video_id)
    
    # Get all run IDs
    run_ids = await redis.zrange(history_key, 0, -1)
    
    # Delete all run hashes
    for request_id in run_ids:
        run_key = _get_run_key(request_id)
        await redis.delete(run_key)
    
    # Delete the sorted set
    await redis.delete(history_key)
    
    logger.info(f"Deleted all pipeline history for {video_id}")
    
    return True
