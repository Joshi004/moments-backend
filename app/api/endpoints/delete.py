"""
Video deletion API endpoint.
Handles comprehensive deletion of videos and associated resources.
"""
import logging
import time
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from app.services.video_delete_service import VideoDeleteService
from app.core.logging import (
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.delete("/videos/{video_id}")
async def delete_video(
    video_id: str,
    force: bool = Query(False, description="Skip active pipeline check"),
    # Local file options
    skip_local_video: bool = Query(False, description="Keep local video file"),
    skip_local_audio: bool = Query(False, description="Keep local audio file"),
    skip_local_thumbnail: bool = Query(False, description="Keep local thumbnail"),
    skip_local_transcript: bool = Query(False, description="Keep local transcript"),
    skip_local_moments: bool = Query(False, description="Keep local moments metadata"),
    skip_local_clips: bool = Query(False, description="Keep local video clips"),
    # GCS options
    skip_gcs_audio: bool = Query(False, description="Keep GCS audio file"),
    skip_gcs_clips: bool = Query(False, description="Keep GCS video clips"),
    skip_gcs_thumbnails: bool = Query(False, description="Keep GCS thumbnail file"),
    # State options
    skip_redis: bool = Query(False, description="Keep Redis state"),
    skip_registry: bool = Query(False, description="Keep URL registry entry")
):
    """
    Delete video and all associated resources.
    
    By default, deletes everything. Use skip_* parameters to preserve specific resources.
    
    Args:
        video_id: Video identifier
        force: Skip active pipeline check (delete anyway)
        skip_local_video: Keep local video file
        skip_local_audio: Keep local audio file
        skip_local_thumbnail: Keep local thumbnail
        skip_local_transcript: Keep local transcript
        skip_local_moments: Keep local moments metadata
        skip_local_clips: Keep local video clips
        skip_gcs_audio: Keep GCS audio file
        skip_gcs_clips: Keep GCS video clips
        skip_redis: Keep Redis state (pipeline status, locks)
        skip_registry: Keep URL registry entry
    
    Returns:
        Deletion result with status and details
    """
    start_time = time.time()
    operation = "delete_video"
    
    log_operation_start(
        logger="app.api.endpoints.delete",
        function="delete_video",
        operation=operation,
        message=f"Starting video deletion: {video_id}",
            context={
                "video_id": video_id,
                "force": force,
                "skip_local_video": skip_local_video,
                "skip_local_audio": skip_local_audio,
                "skip_local_thumbnail": skip_local_thumbnail,
                "skip_local_transcript": skip_local_transcript,
                "skip_local_moments": skip_local_moments,
                "skip_local_clips": skip_local_clips,
                "skip_gcs_audio": skip_gcs_audio,
                "skip_gcs_clips": skip_gcs_clips,
                "skip_gcs_thumbnails": skip_gcs_thumbnails,
                "skip_redis": skip_redis,
                "skip_registry": skip_registry,
                "request_id": get_request_id()
            }
    )
    
    try:
        # Create service and perform deletion
        service = VideoDeleteService()
        result = await service.delete_video(
            video_id=video_id,
            skip_local_video=skip_local_video,
            skip_local_audio=skip_local_audio,
            skip_local_thumbnail=skip_local_thumbnail,
            skip_local_transcript=skip_local_transcript,
            skip_local_moments=skip_local_moments,
            skip_local_clips=skip_local_clips,
            skip_gcs_audio=skip_gcs_audio,
            skip_gcs_clips=skip_gcs_clips,
            skip_gcs_thumbnails=skip_gcs_thumbnails,
            skip_redis=skip_redis,
            skip_registry=skip_registry,
            force=force
        )
        
        # Check if deletion failed
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
                    "errors": result.errors,
                    "duration_seconds": duration
                }
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": result.errors[0] if result.errors else "Deletion failed",
                    "video_id": video_id,
                    "all_errors": result.errors
                }
            )
        
        # Log completion
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.delete",
            function="delete_video",
            operation=operation,
            message=f"Video deletion {result.status}",
            context={
                "video_id": video_id,
                "status": result.status,
                "deleted": result.deleted,
                "errors": result.errors,
                "duration_seconds": duration
            }
        )
        
        # Return result
        return {
            "status": result.status,
            "video_id": result.video_id,
            "deleted": result.deleted,
            "errors": result.errors if result.errors else None,
            "duration_ms": result.duration_ms
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
                "duration_seconds": duration
            }
        )
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during deletion: {str(e)}"
        )
