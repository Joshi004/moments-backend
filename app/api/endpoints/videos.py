"""
Video-related API endpoints.
Handles video listing, retrieval, streaming, and thumbnails.
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import FileResponse, StreamingResponse
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
    
    log_operation_start(
        logger="app.api.endpoints.videos",
        function="list_videos",
        operation=operation,
        message="Listing all videos from database",
        context={"request_id": get_request_id()}
    )
    
    try:
        # Query database for all videos
        videos_from_db = await video_db_repository.list_all(db)
        
        log_event(
            level="INFO",
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
            
            # Still check filesystem for audio/transcript (until Phase 4)
            has_audio = check_audio_exists(video_filename)
            audio_filename = video.identifier + ".wav"
            has_transcript = check_transcript_exists(audio_filename) if has_audio else False
            
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
        log_operation_complete(
            logger="app.api.endpoints.videos",
            function="list_videos",
            operation=operation,
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
        
        # Still check filesystem for audio/transcript (until Phase 4)
        has_audio = check_audio_exists(video_filename)
        audio_filename = video.identifier + ".wav"
        has_transcript = check_transcript_exists(audio_filename) if has_audio else False
        
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
async def stream_video(video_id: str, request: Request):
    """Stream video file with range request support."""
    start_time = time.time()
    operation = "stream_video"
    
    log_operation_start(
        logger="app.api.endpoints.videos",
        function="stream_video",
        operation=operation,
        message=f"Streaming video {video_id}",
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
                function="stream_video",
                operation=operation,
                event="validation_error",
                message="Video file not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        file_path = video_file
        file_size = file_path.stat().st_size
        range_header = request.headers.get("range")
        
        if range_header:
            # Parse range header
            range_match = range_header.replace("bytes=", "").split("-")
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if range_match[1] and range_match[1] else file_size - 1
            
            if start >= file_size or end >= file_size:
                raise HTTPException(status_code=416, detail="Range not satisfiable")
            
            chunk_size = end - start + 1
            
            async def generate():
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = chunk_size
                    while remaining:
                        chunk = f.read(min(8192, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
                "Content-Type": "video/mp4",
            }
            
            duration = time.time() - start_time
            log_operation_complete(
                logger="app.api.endpoints.videos",
                function="stream_video",
                operation=operation,
                message="Streaming video range",
                context={
                    "video_id": video_id,
                    "range_start": start,
                    "range_end": end,
                    "chunk_size": chunk_size,
                    "duration_seconds": duration
                }
            )
            
            return StreamingResponse(
                generate(),
                status_code=206,
                headers=headers,
                media_type="video/mp4"
            )
        else:
            # Return full file
            duration = time.time() - start_time
            log_operation_complete(
                logger="app.api.endpoints.videos",
                function="stream_video",
                operation=operation,
                message="Streaming full video file",
                context={
                    "video_id": video_id,
                    "file_size": file_size,
                    "duration_seconds": duration
                }
            )
            
            return FileResponse(
                file_path,
                media_type="video/mp4",
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(file_size),
                }
            )
            
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.videos",
            function="stream_video",
            operation=operation,
            error=e,
            message="Error streaming video",
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

