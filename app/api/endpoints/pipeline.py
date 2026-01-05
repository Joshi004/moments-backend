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
    PipelineStage,
    StageStatus,
)
from app.services.pipeline.status import initialize_status, get_status
from app.services.pipeline.lock import is_locked, set_cancellation_flag
from app.services.pipeline.history import load_history
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
    
    return StageStatusResponse(
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration,
        skipped=(skipped_str == "true"),
        skip_reason=skip_reason if skip_reason else None,
        error=None,
    )


def _build_status_response(status_data: Dict[str, str]) -> PipelineStatusResponse:
    """Build PipelineStatusResponse from Redis status data."""
    # Parse basic fields
    request_id = status_data.get("request_id", "")
    video_id = status_data.get("video_id", "")
    status = status_data.get("status", "unknown")
    model = status_data.get("model", "")
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
    
    return PipelineStatusResponse(
        request_id=request_id,
        video_id=video_id,
        status=status,
        model=model,
        started_at=started_at,
        completed_at=completed_at,
        total_duration_seconds=total_duration,
        current_stage=current_stage if current_stage else None,
        stages=stages,
        error_stage=error_stage if error_stage else None,
        error_message=error_message if error_message else None,
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
    status_data = get_status(video_id)
    if status_data:
        return _build_status_response(status_data)
    
    # Not in Redis - check history for last run
    history = await load_history(video_id)
    if history:
        latest = history[-1]
        
        # Build stages from history
        stages = {}
        for stage_name, stage_data in latest.get("stages", {}).items():
            try:
                stage_status = StageStatus(stage_data.get("status", "pending"))
            except ValueError:
                stage_status = StageStatus.PENDING
            
            stages[stage_name] = StageStatusResponse(
                status=stage_status,
                started_at=stage_data.get("started_at"),
                completed_at=stage_data.get("completed_at"),
                duration_seconds=stage_data.get("duration_seconds"),
                skipped=stage_data.get("skipped", False),
                skip_reason=stage_data.get("skip_reason"),
                error=None,
            )
        
        return PipelineStatusResponse(
            request_id=latest.get("request_id", ""),
            video_id=video_id,
            status="not_running",
            model=latest.get("model", ""),
            started_at=latest.get("started_at", 0),
            completed_at=latest.get("completed_at"),
            total_duration_seconds=latest.get("total_duration_seconds"),
            current_stage=None,
            stages=stages,
            error_stage=latest.get("error_stage"),
            error_message=latest.get("error_message"),
        )
    
    # Never run
    return PipelineStatusResponse(
        request_id="",
        video_id=video_id,
        status="never_run",
        model="",
        started_at=0,
        completed_at=None,
        total_duration_seconds=None,
        current_stage=None,
        stages={},
        error_stage=None,
        error_message=None,
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
async def get_pipeline_history(video_id: str):
    """
    Get all historical pipeline runs for a video.
    
    Args:
        video_id: Video identifier
    
    Returns:
        History object with list of runs
    """
    history = await load_history(video_id)
    return {"video_id": video_id, "runs": history, "count": len(history)}





