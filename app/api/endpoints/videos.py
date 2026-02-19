"""
Video-related API endpoints.
Handles video listing, retrieval, streaming, and thumbnails.
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from pathlib import Path
import time
import cv2
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import VideoResponse
from app.utils.video import get_video_files, get_videos_directory
from app.services.thumbnail_service import generate_thumbnail, get_thumbnail_path, get_thumbnail_url
from app.services.audio_service import check_audio_exists
from app.services.transcript_service import check_transcript_exists
from app.database.dependencies import get_db
from app.repositories import video_db_repository
from app.core.logging import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds."""
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0.0
        cap.release()
        return duration
    except Exception:
        return 0.0


@router.get("/videos", response_model=list[VideoResponse])
async def list_videos(db: AsyncSession = Depends(get_db)):
    """List all available videos from database."""
    start_time = time.time()
    operation = "list_videos"
    
    log_event(
        level="DEBUG",
        logger="app.api.endpoints.videos",
        function="list_videos",
        operation=operation,
        event="operation_start",
        message="Listing all videos from database",
        context={"request_id": get_request_id()}
    )

    try:
        # Query database for all videos
        videos_from_db = await video_db_repository.list_all(db)
        
        log_event(
            level="DEBUG",
            logger="app.api.endpoints.videos",
            function="list_videos",
            operation=operation,
            event="database_query_complete",
            message="Retrieved videos from database",
            context={"video_count": len(videos_from_db)}
        )
        
        videos = []
        for video in videos_from_db:
            video_filename = f"{video.identifier}.mp4"
            thumbnail_url = get_thumbnail_url(video_filename)
            
            # Check filesystem for audio; check database for transcript
            has_audio = check_audio_exists(video_filename)
            audio_filename = video.identifier + ".wav"
            has_transcript = await check_transcript_exists(audio_filename)
            
            videos.append(VideoResponse(
                id=video.identifier,
                filename=video_filename,
                title=video.title or video.identifier.replace("-", " ").replace("_", " ").title(),
                thumbnail_url=thumbnail_url,
                has_audio=has_audio,
                has_transcript=has_transcript,
                duration_seconds=video.duration_seconds,
                cloud_url=video.cloud_url,
                source_url=video.source_url,
                created_at=video.created_at.isoformat() if video.created_at else None
            ))
        
        duration = time.time() - start_time
        log_event(
            level="DEBUG",
            logger="app.api.endpoints.videos",
            function="list_videos",
            operation=operation,
            event="operation_complete",
            message="Successfully listed videos",
            context={"video_count": len(videos), "duration_seconds": duration}
        )
        
        return videos
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.videos",
            function="list_videos",
            operation=operation,
            error=e,
            message="Error listing videos",
            context={"duration_seconds": duration}
        )
        raise HTTPException(status_code=500, detail=f"Error listing videos: {str(e)}")


@router.get("/videos/{video_id}", response_model=VideoResponse)
async def get_video(video_id: str, db: AsyncSession = Depends(get_db)):
    """Get metadata for a specific video from database."""
    start_time = time.time()
    operation = "get_video"
    
    log_operation_start(
        logger="app.api.endpoints.videos",
        function="get_video",
        operation=operation,
        message=f"Getting video metadata for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )
    
    try:
        # Query database for video
        video = await video_db_repository.get_by_identifier(db, video_id)
        
        if not video:
            log_event(
                level="WARNING",
                logger="app.api.endpoints.videos",
                function="get_video",
                operation=operation,
                event="validation_error",
                message="Video not found in database",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        video_filename = f"{video.identifier}.mp4"
        thumbnail_url = get_thumbnail_url(video_filename)
        
        # Check filesystem for audio; check database for transcript
        has_audio = check_audio_exists(video_filename)
        audio_filename = video.identifier + ".wav"
        has_transcript = await check_transcript_exists(audio_filename)
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.videos",
            function="get_video",
            operation=operation,
            message="Successfully retrieved video metadata",
            context={
                "video_id": video_id,
                "filename": video_filename,
                "has_audio": has_audio,
                "has_transcript": has_transcript,
                "duration_seconds": duration
            }
        )
        
        return VideoResponse(
            id=video.identifier,
            filename=video_filename,
            title=video.title or video.identifier.replace("-", " ").replace("_", " ").title(),
            thumbnail_url=thumbnail_url,
            has_audio=has_audio,
            has_transcript=has_transcript,
            duration_seconds=video.duration_seconds,
            cloud_url=video.cloud_url,
            source_url=video.source_url,
            created_at=video.created_at.isoformat() if video.created_at else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.videos",
            function="get_video",
            operation=operation,
            error=e,
            message="Error getting video",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/stream")
async def stream_video(video_id: str, db: AsyncSession = Depends(get_db)):
    """
    Redirect to GCS signed URL for video streaming.
    
    This endpoint returns a 302 redirect to a signed GCS URL, allowing the browser
    to stream the video directly from Google Cloud Storage without proxying through
    the backend. GCS handles Range requests and byte-range streaming natively.
    """
    start_time = time.time()
    operation = "stream_video"
    
    log_operation_start(
        logger="app.api.endpoints.videos",
        function="stream_video",
        operation=operation,
        message=f"Generating signed URL for video {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )
    
    try:
        # Look up video in database
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            log_event(
                level="WARNING",
                logger="app.api.endpoints.videos",
                function="stream_video",
                operation=operation,
                event="validation_error",
                message="Video not found in database",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Generate signed URL for GCS video
        from app.services.pipeline.upload_service import GCSUploader
        uploader = GCSUploader()
        signed_url = uploader.get_video_signed_url(video.identifier, f"{video.identifier}.mp4")
        
        if not signed_url:
            log_event(
                level="ERROR",
                logger="app.api.endpoints.videos",
                function="stream_video",
                operation=operation,
                event="gcs_error",
                message="Video not available in cloud storage",
                context={"video_id": video_id, "cloud_url": video.cloud_url}
            )
            raise HTTPException(status_code=404, detail="Video not available in cloud storage")
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.videos",
            function="stream_video",
            operation=operation,
            message="Redirecting to GCS signed URL",
            context={
                "video_id": video_id,
                "duration_seconds": duration
            }
        )
        
        return RedirectResponse(url=signed_url, status_code=302)
            
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.videos",
            function="stream_video",
            operation=operation,
            error=e,
            message="Error generating signed URL",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/url")
async def get_video_url(video_id: str, db: AsyncSession = Depends(get_db)):
    """
    Get signed URL for video streaming with expiry information.
    
    Returns JSON with the signed GCS URL and expiration time in seconds.
    This endpoint is used by the frontend for URL lifecycle management,
    allowing proactive refresh before expiry and error recovery.
    
    Returns:
        {
            "url": "https://storage.googleapis.com/...",
            "expires_in_seconds": 14400
        }
    """
    start_time = time.time()
    operation = "get_video_url"
    
    log_event(
        level="DEBUG",
        logger="app.api.endpoints.videos",
        function="get_video_url",
        operation=operation,
        event="operation_start",
        message=f"Getting signed URL for video {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )
    
    try:
        # Look up video in database
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            log_event(
                level="WARNING",
                logger="app.api.endpoints.videos",
                function="get_video_url",
                operation=operation,
                event="validation_error",
                message="Video not found in database",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Generate signed URL for GCS video
        from app.services.pipeline.upload_service import GCSUploader
        from app.core.config import get_settings
        
        uploader = GCSUploader()
        signed_url = uploader.get_video_signed_url(video.identifier, f"{video.identifier}.mp4")
        
        if not signed_url:
            log_event(
                level="ERROR",
                logger="app.api.endpoints.videos",
                function="get_video_url",
                operation=operation,
                event="gcs_error",
                message="Video not available in cloud storage",
                context={"video_id": video_id, "cloud_url": video.cloud_url}
            )
            raise HTTPException(status_code=404, detail="Video not available in cloud storage")
        
        # Get expiry time from settings
        settings = get_settings()
        expires_in_seconds = int(settings.gcs_signed_url_expiry_hours * 3600)
        
        duration = time.time() - start_time
        log_event(
            level="DEBUG",
            logger="app.api.endpoints.videos",
            function="get_video_url",
            operation=operation,
            event="operation_complete",
            message="Generated signed URL",
            context={
                "video_id": video_id,
                "expires_in_seconds": expires_in_seconds,
                "duration_seconds": duration
            }
        )
        
        return {
            "url": signed_url,
            "expires_in_seconds": expires_in_seconds
        }
            
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.videos",
            function="get_video_url",
            operation=operation,
            error=e,
            message="Error generating signed URL",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/thumbnail")
async def get_thumbnail(video_id: str):
    """Get video thumbnail. Generates thumbnail if it doesn't exist."""
    start_time = time.time()
    operation = "get_thumbnail"
    
    log_operation_start(
        logger="app.api.endpoints.videos",
        function="get_thumbnail",
        operation=operation,
        message=f"Getting thumbnail for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )
    
    try:
        video_files = get_video_files()
        
        # Find video by matching stem
        video_file = None
        for vf in video_files:
            if vf.stem == video_id:
                video_file = vf
                break
        
        if not video_file or not video_file.exists():
            log_event(
                level="WARNING",
                logger="app.api.endpoints.videos",
                function="get_thumbnail",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Get or generate thumbnail
        thumbnail_path = get_thumbnail_path(video_file.name)
        
        # Generate thumbnail if it doesn't exist
        if not thumbnail_path.exists():
            log_event(
                level="INFO",
                logger="app.api.endpoints.videos",
                function="get_thumbnail",
                operation=operation,
                event="operation_start",
                message="Generating thumbnail",
                context={"video_file": str(video_file)}
            )
            generated_path = generate_thumbnail(video_file)
            if not generated_path:
                raise HTTPException(status_code=500, detail="Failed to generate thumbnail")
            thumbnail_path = generated_path
        
        if not thumbnail_path.exists():
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.videos",
            function="get_thumbnail",
            operation=operation,
            message="Successfully retrieved thumbnail",
            context={
                "video_id": video_id,
                "thumbnail_path": str(thumbnail_path),
                "duration_seconds": duration
            }
        )
        
        return FileResponse(
            thumbnail_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=31536000"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.videos",
            function="get_thumbnail",
            operation=operation,
            error=e,
            message="Error getting thumbnail",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise

