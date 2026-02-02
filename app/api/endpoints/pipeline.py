"""
Pipeline API endpoints.
Provides REST API for unified video processing pipeline.
"""
import json
import time
import logging
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

from app.core.redis import get_redis_client
from app.models.pipeline_schemas import (
    PipelineStartRequest,
    PipelineStartResponse,
    PipelineStatusResponse,
    StageStatusResponse,
    MomentSummary,
    PipelineStage,
    StageStatus,
)
from app.services.pipeline.status import initialize_status, get_status, get_active_status
from app.services.pipeline.lock import is_locked, set_cancellation_flag
from app.services.pipeline.redis_history import get_latest_run, get_all_runs
from app.services.moments_service import load_moments
from app.utils.video import get_video_by_id

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
    
    # Refinement progress
    elif stage == PipelineStage.MOMENT_REFINEMENT:
        refinement_total = status_data.get("refinement_total")
        refinement_processed = status_data.get("refinement_processed")
        
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
    
    return StageStatusResponse(
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration,
        skipped=(skipped_str == "true"),
        skip_reason=skip_reason if skip_reason else None,
        error=None,
        progress=progress,
    )


def _build_status_response(status_data: Dict[str, str]) -> PipelineStatusResponse:
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
    
    started_at = 0.0
    try:
        started_at = float(started_at_str)
    except ValueError:
        pass
    
    completed_at = None
    total_duration = None
    if completed_at_str:
        try:
            completed_at = float(completed_at_str)
            if started_at:
                total_duration = completed_at - started_at
        except ValueError:
            pass
    
    # Build stages dictionary
    stages = {}
    for stage in PipelineStage:
        stages[stage.value] = _build_stage_status_response(status_data, stage)
    
    # Load moments and separate by refinement status
    video_filename = f"{video_id}.mp4"
    moments = load_moments(video_filename) or []
    
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
    # Validate video exists
    video = get_video_by_id(video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"Video not found: {video_id}")
    
    # Check if already processing
    locked, lock_info = is_locked(video_id)
    if locked:
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline already running for video '{video_id}'"
        )
    
    # Generate request ID
    request_id = f"pipeline:{video_id}:{int(time.time() * 1000)}"
    
    # Initialize status in Redis
    initialize_status(video_id, request_id, request.dict())
    
    # Add to stream
    redis = get_redis_client()
    message_id = redis.xadd("pipeline:requests", {
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
async def get_pipeline_status(video_id: str):
    """
    Get current pipeline status for a video.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Pipeline status response
    """
    # Check Redis for active pipeline
    status_data = get_active_status(video_id)
    if status_data:
        return _build_status_response(status_data)
    
    # Not active - check Redis history for last run
    latest_run = get_latest_run(video_id)
    if latest_run:
        return _build_status_response(latest_run)
    
    # Never run
    return PipelineStatusResponse(
        request_id="",
        video_id=video_id,
        status="never_run",
        generation_model="",
        refinement_model="",
        started_at=0,
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
    locked, _ = is_locked(video_id)
    if not locked:
        raise HTTPException(
            status_code=400,
            detail=f"No pipeline running for video '{video_id}'"
        )
    
    set_cancellation_flag(video_id)
    logger.info(f"Cancellation requested for pipeline: {video_id}")
    
    return {"message": "Cancellation requested", "video_id": video_id}


@router.get("/{video_id}/history")
def get_pipeline_history(video_id: str):
    """
    Get all historical pipeline runs for a video from Redis.
    
    Args:
        video_id: Video identifier
    
    Returns:
        History object with list of runs
    """
    # Get all runs from Redis (newest first)
    runs_data = get_all_runs(video_id)
    
    # Build response list from Redis data
    runs = []
    for run_data in runs_data:
        # Build stages from Redis hash data
        stages = {}
        for stage in PipelineStage:
            stages[stage.value] = _build_stage_status_response(run_data, stage)
        
        # Load moments and separate by refinement status
        video_filename = f"{video_id}.mp4"
        moments = load_moments(video_filename) or []
        
        coarse_moments = [
            {
                "id": m['id'],
                "title": m['title'],
                "start_time": m['start_time'],
                "end_time": m['end_time']
            }
            for m in moments if not m.get('is_refined', False)
        ]
        
        refined_moments = [
            {
                "id": m['id'],
                "title": m['title'],
                "start_time": m['start_time'],
                "end_time": m['end_time']
            }
            for m in moments if m.get('is_refined', False)
        ]
        
        # Build run entry
        run_entry = {
            "request_id": run_data.get("request_id", ""),
            "video_id": run_data.get("video_id", ""),
            "status": run_data.get("status", "unknown"),
            "generation_model": run_data.get("generation_model", ""),
            "refinement_model": run_data.get("refinement_model", ""),
            "started_at": float(run_data.get("started_at", "0")),
            "completed_at": float(run_data.get("completed_at", "0")) if run_data.get("completed_at") else None,
            "total_duration_seconds": None,
            "stages": stages,
            "error_stage": run_data.get("error_stage") or None,
            "error_message": run_data.get("error_message") or None,
            "coarse_moments": coarse_moments,
            "refined_moments": refined_moments,
        }
        
        # Calculate total duration
        if run_entry["started_at"] and run_entry["completed_at"]:
            run_entry["total_duration_seconds"] = run_entry["completed_at"] - run_entry["started_at"]
        
        runs.append(run_entry)
    
    return {"video_id": video_id, "runs": runs, "count": len(runs)}





