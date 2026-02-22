"""
Pipeline API endpoints.
Provides REST API for unified video processing pipeline.

All endpoints use async Redis for non-blocking operations.
"""
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_async_redis_client
from app.database.dependencies import get_db
from app.models.pipeline_schemas import (
    PipelineStartRequest,
    PipelineStartResponse,
    PipelineStatusResponse,
    StageStatusResponse,
    MomentSummary,
    PipelineStage,
    StageStatus,
)
from app.services.pipeline.status import initialize_status, get_active_status
from app.services.pipeline.lock import is_locked, set_cancellation_flag
from app.services.pipeline.redis_history import get_latest_run
from app.services.moments_service import load_moments

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


def _build_stage_status_response(status_data: Dict[str, str], stage: PipelineStage) -> StageStatusResponse:
    """Build StageStatusResponse from Redis status data."""
    prefix = stage.value
    
    status_str = status_data.get(f"{prefix}_status", "pending")
    try:
        status = StageStatus(status_str)
    except ValueError:
        status = StageStatus.PENDING
    
    started_at_str = status_data.get(f"{prefix}_started_at", "")
    completed_at_str = status_data.get(f"{prefix}_completed_at", "")
    skipped_str = status_data.get(f"{prefix}_skipped", "false")
    skip_reason = status_data.get(f"{prefix}_skip_reason", "")
    
    started_at = None
    completed_at = None
    duration = None
    
    if started_at_str:
        try:
            started_at = float(started_at_str)
        except ValueError:
            pass
    
    if completed_at_str:
        try:
            completed_at = float(completed_at_str)
        except ValueError:
            pass
    
    if started_at and completed_at:
        duration = completed_at - started_at
    
    # Build progress information if available
    progress = None
    
    # Download progress
    if stage == PipelineStage.VIDEO_DOWNLOAD:
        download_bytes = status_data.get("download_bytes")
        download_total = status_data.get("download_total")
        download_percentage = status_data.get("download_percentage")
        
        if download_bytes or download_total or download_percentage:
            progress = {}
            if download_bytes:
                try:
                    progress["bytes_downloaded"] = int(download_bytes)
                except ValueError:
                    pass
            if download_total:
                try:
                    progress["total_bytes"] = int(download_total)
                except ValueError:
                    pass
            if download_percentage:
                try:
                    progress["percentage"] = int(download_percentage)
                except ValueError:
                    pass
    
    # Upload progress
    elif stage == PipelineStage.AUDIO_UPLOAD:
        upload_bytes = status_data.get("upload_bytes")
        upload_total = status_data.get("upload_total")
        upload_percentage = status_data.get("upload_percentage")
        
        if upload_bytes or upload_total or upload_percentage:
            progress = {}
            if upload_bytes:
                try:
                    progress["bytes_uploaded"] = int(upload_bytes)
                except ValueError:
                    pass
            if upload_total:
                try:
                    progress["total_bytes"] = int(upload_total)
                except ValueError:
                    pass
            if upload_percentage:
                try:
                    progress["percentage"] = int(upload_percentage)
                except ValueError:
                    pass
    
    # Clip upload progress
    elif stage == PipelineStage.CLIP_UPLOAD:
        clip_current = status_data.get("clip_upload_current")
        clip_total_clips = status_data.get("clip_upload_total_clips")
        clip_bytes = status_data.get("clip_upload_bytes")
        clip_total_bytes = status_data.get("clip_upload_total_bytes")
        clip_percentage = status_data.get("clip_upload_percentage")
        
        if any([clip_current, clip_total_clips, clip_bytes, clip_total_bytes, clip_percentage]):
            progress = {}
            if clip_current:
                try:
                    progress["current_clip"] = int(clip_current)
                except ValueError:
                    pass
            if clip_total_clips:
                try:
                    progress["total_clips"] = int(clip_total_clips)
                except ValueError:
                    pass
            if clip_bytes:
                try:
                    progress["bytes_uploaded"] = int(clip_bytes)
                except ValueError:
                    pass
            if clip_total_bytes:
                try:
                    progress["total_bytes"] = int(clip_total_bytes)
                except ValueError:
                    pass
            if clip_percentage:
                try:
                    progress["percentage"] = int(clip_percentage)
                except ValueError:
                    pass
    
    # Clip extraction progress
    elif stage == PipelineStage.CLIP_EXTRACTION:
        clips_total = status_data.get("clips_total")
        clips_processed = status_data.get("clips_processed")
        clips_failed = status_data.get("clips_failed")
        
        if clips_total or clips_processed:
            progress = {}
            if clips_total:
                try:
                    progress["total"] = int(clips_total)
                except ValueError:
                    pass
            if clips_processed:
                try:
                    progress["processed"] = int(clips_processed)
                except ValueError:
                    pass
            if clips_failed:
                try:
                    progress["failed"] = int(clips_failed)
                except ValueError:
                    pass
    
    # Refinement progress
    elif stage == PipelineStage.MOMENT_REFINEMENT:
        refinement_total = status_data.get("refinement_total")
        refinement_processed = status_data.get("refinement_processed")
        refinement_successful = status_data.get("refinement_successful")
        refinement_errors = status_data.get("refinement_errors")
        
        if refinement_total or refinement_processed:
            progress = {}
            if refinement_total:
                try:
                    progress["total"] = int(refinement_total)
                except ValueError:
                    pass
            if refinement_processed:
                try:
                    progress["processed"] = int(refinement_processed)
                except ValueError:
                    pass
            if refinement_successful:
                try:
                    progress["successful"] = int(refinement_successful)
                    progress["failed"] = progress.get("processed", 0) - progress["successful"]
                except ValueError:
                    pass
            if refinement_errors:
                try:
                    progress["errors"] = json.loads(refinement_errors)
                except (json.JSONDecodeError, TypeError):
                    progress["errors"] = [refinement_errors]
    
    # Determine stage-level error
    stage_error = None
    error_stage_value = status_data.get("error_stage", "")
    if error_stage_value == prefix:
        error_msg = status_data.get("error_message", "")
        if error_msg:
            stage_error = error_msg
    
    return StageStatusResponse(
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration,
        skipped=(skipped_str == "true"),
        skip_reason=skip_reason if skip_reason else None,
        error=stage_error,
        progress=progress,
    )


async def _build_status_response(status_data: Dict[str, str]) -> PipelineStatusResponse:
    """Build PipelineStatusResponse from Redis status data."""
    # Parse basic fields
    request_id = status_data.get("request_id", "")
    video_id = status_data.get("video_id", "")
    status = status_data.get("status", "unknown")
    generation_model = status_data.get("generation_model", "")
    refinement_model = status_data.get("refinement_model", "")
    started_at_str = status_data.get("started_at", "0")
    completed_at_str = status_data.get("completed_at", "")
    current_stage = status_data.get("current_stage", "")
    error_stage = status_data.get("error_stage", "")
    error_message = status_data.get("error_message", "")
    
    started_at_epoch = 0.0
    try:
        started_at_epoch = float(started_at_str)
    except ValueError:
        pass

    completed_at_epoch = None
    total_duration = None
    if completed_at_str:
        try:
            completed_at_epoch = float(completed_at_str)
            if started_at_epoch:
                total_duration = completed_at_epoch - started_at_epoch
        except ValueError:
            pass

    # Convert epoch floats to ISO 8601 strings (canonical format)
    started_at: Optional[str] = None
    if started_at_epoch:
        started_at = datetime.fromtimestamp(started_at_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    completed_at: Optional[str] = None
    if completed_at_epoch:
        completed_at = datetime.fromtimestamp(completed_at_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Build stages dictionary
    stages = {}
    for stage in PipelineStage:
        stages[stage.value] = _build_stage_status_response(status_data, stage)
    
    # Load moments and separate by refinement status
    video_filename = f"{video_id}.mp4"
    moments = await load_moments(video_filename) or []
    
    coarse_moments = [
        MomentSummary(
            id=m['id'],
            title=m['title'],
            start_time=m['start_time'],
            end_time=m['end_time']
        )
        for m in moments if not m.get('is_refined', False)
    ]
    
    refined_moments = [
        MomentSummary(
            id=m['id'],
            title=m['title'],
            start_time=m['start_time'],
            end_time=m['end_time']
        )
        for m in moments if m.get('is_refined', False)
    ]
    
    return PipelineStatusResponse(
        request_id=request_id,
        video_id=video_id,
        status=status,
        generation_model=generation_model,
        refinement_model=refinement_model,
        started_at=started_at,
        completed_at=completed_at,
        total_duration_seconds=total_duration,
        current_stage=current_stage if current_stage else None,
        stages=stages,
        error_stage=error_stage if error_stage else None,
        error_message=error_message if error_message else None,
        coarse_moments=coarse_moments,
        refined_moments=refined_moments,
    )


@router.post("/{video_id}/start", response_model=PipelineStartResponse)
async def start_pipeline(video_id: str, request: PipelineStartRequest):
    """
    Start unified pipeline for a video.
    
    Args:
        video_id: Video identifier
        request: Pipeline configuration
    
    Returns:
        Pipeline start response with request_id
    
    Raises:
        HTTPException: If video not found or pipeline already running
    """
    # Validate video exists in database (source of truth after Phase 11)
    from app.database.session import get_session_factory
    from app.repositories import video_db_repository

    session_factory = get_session_factory()
    async with session_factory() as session:
        video = await video_db_repository.get_by_identifier(session, video_id)
        if not video:
            raise HTTPException(status_code=404, detail=f"Video not found: {video_id}")
    
    # Check if already processing
    locked, lock_info = await is_locked(video_id)
    if locked:
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline already running for video '{video_id}'"
        )
    
    # Generate request ID
    request_id = f"pipeline:{video_id}:{int(time.time() * 1000)}"
    
    # Initialize status in Redis
    await initialize_status(video_id, request_id, request.dict())
    
    # Add to stream
    redis = await get_async_redis_client()
    message_id = await redis.xadd("pipeline:requests", {
        "request_id": request_id,
        "video_id": video_id,
        "config": json.dumps(request.dict()),
        "requested_at": str(time.time())
    })
    
    logger.info(f"Started pipeline for {video_id}: {request_id}, stream message: {message_id}")
    
    return PipelineStartResponse(
        request_id=request_id,
        video_id=video_id,
        status="queued",
        message="Pipeline started successfully"
    )


@router.get("/{video_id}/status", response_model=PipelineStatusResponse)
async def get_pipeline_status(video_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get current pipeline status for a video.

    Fallback chain:
    1. Active Redis hash (pipeline currently running)
    2. Cached Redis run hash (completed within last 24h)
    3. Most recent pipeline_history DB record (permanent fallback)
    4. never_run (no history at all)

    Args:
        video_id: Video identifier

    Returns:
        Pipeline status response
    """
    # 1. Check Redis for active pipeline
    status_data = await get_active_status(video_id)
    if status_data:
        return await _build_status_response(status_data)

    # 2. Not active - check Redis history for last run
    latest_run = await get_latest_run(video_id)
    if latest_run:
        return await _build_status_response(latest_run)

    # 3. DB fallback - most recent pipeline_history record
    try:
        from app.repositories import pipeline_history_db_repository
        db_runs = await pipeline_history_db_repository.get_by_video_identifier(
            db, video_id, limit=1
        )
        if db_runs:
            run = db_runs[0]
            return PipelineStatusResponse(
                request_id=run.identifier,
                video_id=video_id,
                status=run.status,
                generation_model="",
                refinement_model="",
                started_at=run.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if run.started_at else None,
                completed_at=run.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ") if run.completed_at else None,
                total_duration_seconds=run.duration_seconds,
                current_stage=None,
                stages={},
                error_stage=run.error_stage,
                error_message=run.error_message,
                coarse_moments=[],
                refined_moments=[],
            )
    except Exception as db_err:
        logger.warning(f"DB fallback for status failed for {video_id}: {db_err}")

    # 4. Never run
    return PipelineStatusResponse(
        request_id="",
        video_id=video_id,
        status="never_run",
        generation_model="",
        refinement_model="",
        started_at=None,
        completed_at=None,
        total_duration_seconds=None,
        current_stage=None,
        stages={},
        error_stage=None,
        error_message=None,
        coarse_moments=[],
        refined_moments=[],
    )


@router.post("/{video_id}/cancel")
async def cancel_pipeline(video_id: str):
    """
    Request cancellation of running pipeline.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Cancellation confirmation message
    
    Raises:
        HTTPException: If no pipeline is running
    """
    locked, _ = await is_locked(video_id)
    if not locked:
        raise HTTPException(
            status_code=400,
            detail=f"No pipeline running for video '{video_id}'"
        )
    
    await set_cancellation_flag(video_id)
    logger.info(f"Cancellation requested for pipeline: {video_id}")
    
    return {"message": "Cancellation requested", "video_id": video_id}


def _history_record_to_dict(run) -> Dict[str, Any]:
    """Convert a PipelineHistory ORM instance to an API response dict."""
    return {
        "id": run.id,
        "identifier": run.identifier,
        "pipeline_type": run.pipeline_type,
        "status": run.status,
        "started_at": run.started_at.strftime("%Y-%m-%dT%H:%M:%SZ") if run.started_at else None,
        "completed_at": run.completed_at.strftime("%Y-%m-%dT%H:%M:%SZ") if run.completed_at else None,
        "total_duration_seconds": run.duration_seconds,
        "total_moments_generated": run.total_moments_generated,
        "total_clips_created": run.total_clips_created,
        "error_stage": run.error_stage,
        "error_message": run.error_message,
        "generation_config_id": run.generation_config_id,
        "video_id": video_id_from_run(run),
    }


def video_id_from_run(run) -> str:
    """Extract video string identifier from a PipelineHistory record's identifier field.

    The identifier is 'pipeline:{video_id}:{timestamp}', so we extract the middle part.
    Falls back gracefully if the format differs.
    """
    try:
        parts = run.identifier.split(":")
        if len(parts) >= 3 and parts[0] == "pipeline":
            return ":".join(parts[1:-1])
    except Exception:
        pass
    return ""


@router.get("/{video_id}/history")
async def get_pipeline_history(
    video_id: str,
    limit: int = 20,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get historical pipeline runs for a video from the database (permanent).

    Args:
        video_id: Video string identifier
        limit: Maximum number of runs to return (default 20)
        status: Optional filter by status ('completed', 'failed', 'cancelled', 'running')

    Returns:
        History object with list of runs ordered newest first
    """
    from app.repositories import pipeline_history_db_repository

    runs = await pipeline_history_db_repository.get_by_video_identifier(
        db, video_id, limit=limit, status_filter=status
    )

    run_dicts = []
    for run in runs:
        d = _history_record_to_dict(run)
        # Override video_id with the one from the URL path (more reliable)
        d["video_id"] = video_id
        run_dicts.append(d)

    return {"video_id": video_id, "total_runs": len(run_dicts), "runs": run_dicts, "count": len(run_dicts)}
