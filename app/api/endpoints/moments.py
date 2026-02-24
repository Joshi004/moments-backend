"""
Moment-related API endpoints.
Handles moment CRUD operations.
"""
from fastapi import APIRouter, HTTPException, Depends
import time

from sqlalchemy.ext.asyncio import AsyncSession
from app.database.dependencies import get_db
from app.repositories import video_db_repository
from app.models.schemas import MomentResponse
from app.services.moments_service import load_moments, add_moment
from app.core.logging import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()


@router.get("/videos/{video_id}/moments", response_model=list[MomentResponse])
async def get_moments(video_id: str, db: AsyncSession = Depends(get_db)):
    """Get all moments for a video."""
    start_time = time.time()
    operation = "get_moments"

    log_event(
        level="DEBUG",
        logger="app.api.endpoints.moments",
        function="get_moments",
        operation=operation,
        event="operation_start",
        message=f"Getting moments for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )

    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            log_event(
                level="WARNING",
                logger="app.api.endpoints.moments",
                function="get_moments",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")

        moments = await load_moments(f"{video_id}.mp4")

        duration = time.time() - start_time
        log_event(
            level="DEBUG",
            logger="app.api.endpoints.moments",
            function="get_moments",
            operation=operation,
            event="operation_complete",
            message="Successfully retrieved moments",
            context={
                "video_id": video_id,
                "moment_count": len(moments),
                "duration_seconds": duration
            }
        )

        return [MomentResponse(**moment) for moment in moments]

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.moments",
            function="get_moments",
            operation=operation,
            error=e,
            message="Error getting moments",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.post("/videos/{video_id}/moments", response_model=MomentResponse, status_code=201)
async def create_moment(video_id: str, moment: MomentResponse, db: AsyncSession = Depends(get_db)):
    """Add a new moment to a video."""
    start_time = time.time()
    operation = "create_moment"

    log_operation_start(
        logger="app.api.endpoints.moments",
        function="create_moment",
        operation=operation,
        message=f"Creating moment for {video_id}",
        context={
            "video_id": video_id,
            "moment": {
                "start_time": moment.start_time,
                "end_time": moment.end_time,
                "title": moment.title
            },
            "request_id": get_request_id()
        }
    )

    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # Use duration from database; fall back to 0 if not available
        video_duration = video.duration_seconds if video.duration_seconds else 0.0
        if video_duration <= 0:
            raise HTTPException(status_code=500, detail="Could not determine video duration")

        moment_dict = {
            "start_time": moment.start_time,
            "end_time": moment.end_time,
            "title": moment.title
        }

        # Add moment with validation (async -- saves to database)
        success, error_message, created_moment = await add_moment(f"{video_id}.mp4", moment_dict, video_duration)

        if not success:
            raise HTTPException(status_code=400, detail=error_message)

        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.moments",
            function="create_moment",
            operation=operation,
            message="Successfully created moment",
            context={
                "video_id": video_id,
                "moment_id": created_moment.get("id"),
                "duration_seconds": duration
            }
        )

        return MomentResponse(**created_moment)

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.moments",
            function="create_moment",
            operation=operation,
            error=e,
            message="Error creating moment",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise
