"""
Video deletion API endpoint.
Handles scoped deletion of videos and associated resources.
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.video_delete_service import VideoDeleteService
from app.core.logging import (
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()
logger = logging.getLogger(__name__)

_VALID_SCOPES = {"all", "video_file", "moments", "refined_moments"}


@router.delete("/videos/{video_id}")
async def delete_video(
    video_id: str,
    scope: str = Query(..., description="What to delete: all, video_file, moments, refined_moments"),
    moment_ids: Optional[str] = Query(
        None,
        description="Comma-separated moment identifiers. Only used when scope=moments.",
    ),
    force: bool = Query(False, description="Skip active pipeline check"),
):
    """
    Delete a video or a subset of its associated resources.

    scope=all          — Full deletion: GCS files, temp files, Redis state, DB record.
    scope=video_file   — Delete video + audio from GCS; set cloud_url to NULL in DB.
                         Moments, clips, transcript, and the video record are preserved.
    scope=moments      — Delete specific or all moments with their clips and clip thumbnails.
                         Provide moment_ids to target specific moments; omit for all.
    scope=refined_moments — Delete only refined child moments with their clips and thumbnails.
                            Root moments and the video record are preserved.
    """
    if scope not in _VALID_SCOPES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Invalid scope '{scope}'. Must be one of: {', '.join(sorted(_VALID_SCOPES))}.",
                "video_id": video_id,
            },
        )

    parsed_moment_ids: Optional[list[str]] = None
    if scope == "moments" and moment_ids:
        parsed_moment_ids = [m.strip() for m in moment_ids.split(",") if m.strip()]

    start_time = time.time()
    operation = "delete_video"

    log_operation_start(
        logger="app.api.endpoints.delete",
        function="delete_video",
        operation=operation,
        message=f"Starting video deletion: {video_id}",
        context={
            "video_id": video_id,
            "scope": scope,
            "moment_ids": parsed_moment_ids,
            "force": force,
            "request_id": get_request_id(),
        },
    )

    try:
        service = VideoDeleteService()
        result = await service.delete_video(
            video_id=video_id,
            scope=scope,
            moment_ids=parsed_moment_ids,
            force=force,
        )

        if result.status == "failed":
            duration = time.time() - start_time
            log_operation_error(
                logger="app.api.endpoints.delete",
                function="delete_video",
                operation=operation,
                error=Exception("; ".join(result.errors)),
                message="Video deletion failed",
                context={
                    "video_id": video_id,
                    "scope": scope,
                    "errors": result.errors,
                    "duration_seconds": duration,
                },
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": result.errors[0] if result.errors else "Deletion failed",
                    "video_id": video_id,
                    "all_errors": result.errors,
                },
            )

        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.delete",
            function="delete_video",
            operation=operation,
            message=f"Video deletion {result.status}",
            context={
                "video_id": video_id,
                "scope": scope,
                "status": result.status,
                "deleted": result.deleted,
                "errors": result.errors,
                "duration_seconds": duration,
            },
        )

        return {
            "status": result.status,
            "video_id": result.video_id,
            "deleted": result.deleted,
            "errors": result.errors if result.errors else None,
            "duration_ms": result.duration_ms,
        }

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.delete",
            function="delete_video",
            operation=operation,
            error=e,
            message="Unexpected error during video deletion",
            context={
                "video_id": video_id,
                "scope": scope,
                "duration_seconds": duration,
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during deletion: {str(e)}",
        )
