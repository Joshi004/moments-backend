from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from app.models import Video, Moment
from app.utils.video_utils import get_video_files
from app.utils.thumbnail_service import generate_thumbnail, get_thumbnail_path, get_thumbnail_url
from app.utils.moments_service import load_moments, add_moment, get_moment_by_id
from app.utils.audio_service import check_audio_exists, start_processing_job, is_processing, process_audio_async
from app.utils.transcript_service import check_transcript_exists, start_transcription_job, is_transcribing, process_transcription_async, load_transcript
from app.utils.moments_generation_service import (
    start_generation_job,
    is_generating,
    process_moments_generation_async,
    get_generation_status
)
from app.utils.refine_moment_service import (
    start_refinement_job,
    is_refining,
    process_moment_refinement_async,
    get_refinement_status
)
from app.utils.video_clipping_service import (
    start_clip_extraction_job,
    is_extracting_clips,
    get_clip_extraction_status,
    process_clip_extraction_async
)
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id,
    log_status_check
)
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import cv2
import time

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


@router.get("/videos", response_model=list[Video])
async def list_videos():
    """List all available videos."""
    start_time = time.time()
    operation = "list_videos"
    
    log_operation_start(
        logger="app.routes.videos",
        function="list_videos",
        operation=operation,
        message="Listing all videos",
        context={"request_id": get_request_id()}
    )
    
    try:
        from app.utils.video_utils import get_videos_directory, get_video_files
        videos_dir = get_videos_directory()
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="list_videos",
            operation=operation,
            event="validation_start",
            message="Validating videos directory",
            context={"videos_dir": str(videos_dir)}
        )
        
        # Verify directory before proceeding
        if not videos_dir.exists():
            log_event(
                level="ERROR",
                logger="app.routes.videos",
                function="list_videos",
                operation=operation,
                event="validation_error",
                message="Videos directory does not exist",
                context={"videos_dir": str(videos_dir)}
            )
            raise HTTPException(
                status_code=500, 
                detail=f"Videos directory does not exist: {videos_dir}"
            )
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="list_videos",
            operation=operation,
            event="validation_complete",
            message="Videos directory validated",
        )
        
        video_files = get_video_files()
        
        log_event(
            level="INFO",
            logger="app.routes.videos",
            function="list_videos",
            operation=operation,
            event="file_operation_start",
            message="Scanning video files",
            context={"video_count": len(video_files)}
        )
        
        videos = []
        
        for video_file in video_files:
            video_id = video_file.stem  # filename without extension
            thumbnail_url = get_thumbnail_url(video_file.name)
            has_audio = check_audio_exists(video_file.name)
            # Check if transcript exists (transcript is based on audio filename)
            audio_filename = video_file.stem + ".wav"
            has_transcript = check_transcript_exists(audio_filename) if has_audio else False
            videos.append(Video(
                id=video_id,
                filename=video_file.name,
                title=video_file.stem.replace("-", " ").replace("_", " ").title(),
                thumbnail_url=thumbnail_url,
                has_audio=has_audio,
                has_transcript=has_transcript
            ))
        
        duration = time.time() - start_time
        
        log_operation_complete(
            logger="app.routes.videos",
            function="list_videos",
            operation=operation,
            message="Successfully listed videos",
            context={
                "video_count": len(videos),
                "duration_seconds": duration
            }
        )
        
        return videos
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="list_videos",
            operation=operation,
            error=e,
            message="Error listing videos",
            context={"duration_seconds": duration}
        )
        import traceback
        error_details = traceback.format_exc()
        error_msg = f"Error listing videos: {str(e)}\nDirectory: {get_videos_directory() if 'get_videos_directory' in dir() else 'unknown'}\nTraceback:\n{error_details}"
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/videos/{video_id}")
async def get_video(video_id: str):
    """Get metadata for a specific video."""
    start_time = time.time()
    operation = "get_video"
    
    log_operation_start(
        logger="app.routes.videos",
        function="get_video",
        operation=operation,
        message=f"Getting video metadata for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )
    
    try:
        video_files = get_video_files()
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="get_video",
            operation=operation,
            event="file_operation_start",
            message="Searching for video file",
            context={"video_id": video_id, "total_videos": len(video_files)}
        )
        
        # Find video by matching stem (filename without extension)
        for video_file in video_files:
            if video_file.stem == video_id:
                thumbnail_url = get_thumbnail_url(video_file.name)
                has_audio = check_audio_exists(video_file.name)
                # Check if transcript exists (transcript is based on audio filename)
                audio_filename = video_file.stem + ".wav"
                has_transcript = check_transcript_exists(audio_filename) if has_audio else False
                
                duration = time.time() - start_time
                
                log_operation_complete(
                    logger="app.routes.videos",
                    function="get_video",
                    operation=operation,
                    message="Successfully retrieved video metadata",
                    context={
                        "video_id": video_id,
                        "filename": video_file.name,
                        "has_audio": has_audio,
                        "has_transcript": has_transcript,
                        "duration_seconds": duration
                    }
                )
                
                return Video(
                    id=video_id,
                    filename=video_file.name,
                    title=video_file.stem.replace("-", " ").replace("_", " ").title(),
                    thumbnail_url=thumbnail_url,
                    has_audio=has_audio,
                    has_transcript=has_transcript
                )
        
        duration = time.time() - start_time
        log_event(
            level="WARNING",
            logger="app.routes.videos",
            function="get_video",
            operation=operation,
            event="validation_error",
            message="Video not found",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise HTTPException(status_code=404, detail="Video not found")
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
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
        logger="app.routes.videos",
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
                logger="app.routes.videos",
                function="stream_video",
                operation=operation,
                event="validation_error",
                message="Video file not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        file_path = video_file
        file_size = file_path.stat().st_size
        
        # Handle range requests for video seeking
        range_header = request.headers.get("range")
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="stream_video",
            operation=operation,
            event="stream_start",
            message="Preparing video stream",
            context={
                "video_id": video_id,
                "file_size": file_size,
                "has_range_header": range_header is not None,
                "range_header": range_header
            }
        )
        
        if range_header:
            # Parse range header
            range_match = range_header.replace("bytes=", "").split("-")
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if range_match[1] and range_match[1] else file_size - 1
            
            log_event(
                level="DEBUG",
                logger="app.routes.videos",
                function="stream_video",
                operation=operation,
                event="range_request",
                message="Processing range request",
                context={
                    "start": start,
                    "end": end,
                    "chunk_size": end - start + 1
                }
            )
            
            if start >= file_size or end >= file_size:
                log_event(
                    level="WARNING",
                    logger="app.routes.videos",
                    function="stream_video",
                    operation=operation,
                    event="validation_error",
                    message="Range not satisfiable",
                    context={"start": start, "end": end, "file_size": file_size}
                )
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
                logger="app.routes.videos",
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
                logger="app.routes.videos",
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
            logger="app.routes.videos",
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
        logger="app.routes.videos",
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
                logger="app.routes.videos",
                function="get_thumbnail",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Get or generate thumbnail
        thumbnail_path = get_thumbnail_path(video_file.name)
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="get_thumbnail",
            operation=operation,
            event="file_operation_start",
            message="Checking thumbnail existence",
            context={"thumbnail_path": str(thumbnail_path), "exists": thumbnail_path.exists()}
        )
        
        # Generate thumbnail if it doesn't exist
        if not thumbnail_path.exists():
            log_event(
                level="INFO",
                logger="app.routes.videos",
                function="get_thumbnail",
                operation=operation,
                event="operation_start",
                message="Generating thumbnail",
                context={"video_file": str(video_file)}
            )
            generated_path = generate_thumbnail(video_file)
            if not generated_path:
                log_event(
                    level="ERROR",
                    logger="app.routes.videos",
                    function="get_thumbnail",
                    operation=operation,
                    event="operation_error",
                    message="Failed to generate thumbnail",
                    context={"video_file": str(video_file)}
                )
                raise HTTPException(status_code=500, detail="Failed to generate thumbnail")
            thumbnail_path = generated_path
        
        if not thumbnail_path.exists():
            log_event(
                level="ERROR",
                logger="app.routes.videos",
                function="get_thumbnail",
                operation=operation,
                event="validation_error",
                message="Thumbnail not found after generation",
                context={"thumbnail_path": str(thumbnail_path)}
            )
            raise HTTPException(status_code=404, detail="Thumbnail not found")
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
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
            headers={
                "Cache-Control": "public, max-age=31536000",  # Cache for 1 year
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="get_thumbnail",
            operation=operation,
            error=e,
            message="Error getting thumbnail",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/moments", response_model=list[Moment])
async def get_moments(video_id: str):
    """Get all moments for a video."""
    start_time = time.time()
    operation = "get_moments"
    
    log_operation_start(
        logger="app.routes.videos",
        function="get_moments",
        operation=operation,
        message=f"Getting moments for {video_id}",
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
                logger="app.routes.videos",
                function="get_moments",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Load moments from JSON file
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="get_moments",
            operation=operation,
            event="file_operation_start",
            message="Loading moments from file",
            context={"video_filename": video_file.name}
        )
        moments = load_moments(video_file.name)
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="get_moments",
            operation=operation,
            message="Successfully retrieved moments",
            context={
                "video_id": video_id,
                "moment_count": len(moments),
                "duration_seconds": duration
            }
        )
        
        # Convert to Moment models
        return [Moment(**moment) for moment in moments]
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="get_moments",
            operation=operation,
            error=e,
            message="Error getting moments",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.post("/videos/{video_id}/moments", response_model=Moment, status_code=201)
async def create_moment(video_id: str, moment: Moment):
    """Add a new moment to a video."""
    start_time = time.time()
    operation = "create_moment"
    
    log_operation_start(
        logger="app.routes.videos",
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
                logger="app.routes.videos",
                function="create_moment",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Get video duration for validation
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="create_moment",
            operation=operation,
            event="validation_start",
            message="Getting video duration",
            context={"video_file": str(video_file)}
        )
        video_duration = get_video_duration(video_file)
        if video_duration <= 0:
            log_event(
                level="ERROR",
                logger="app.routes.videos",
                function="create_moment",
                operation=operation,
                event="validation_error",
                message="Could not determine video duration",
                context={"video_file": str(video_file)}
            )
            raise HTTPException(status_code=500, detail="Could not determine video duration")
        
        # Convert Moment model to dict
        moment_dict = {
            "start_time": moment.start_time,
            "end_time": moment.end_time,
            "title": moment.title
        }
        
        # Add moment with validation
        success, error_message, created_moment = add_moment(video_file.name, moment_dict, video_duration)
        
        if not success:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="create_moment",
                operation=operation,
                event="validation_error",
                message="Moment validation failed",
                context={"error_message": error_message, "moment": moment_dict}
            )
            raise HTTPException(status_code=400, detail=error_message)
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="create_moment",
            operation=operation,
            message="Successfully created moment",
            context={
                "video_id": video_id,
                "moment_id": created_moment.get("id"),
                "duration_seconds": duration
            }
        )
        
        return Moment(**created_moment)
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="create_moment",
            operation=operation,
            error=e,
            message="Error creating moment",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.post("/videos/{video_id}/process-audio")
async def process_audio(video_id: str):
    """Start audio extraction process for a video."""
    start_time = time.time()
    operation = "process_audio"
    
    log_operation_start(
        logger="app.routes.videos",
        function="process_audio",
        operation=operation,
        message=f"Starting audio processing for {video_id}",
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
                logger="app.routes.videos",
                function="process_audio",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if already processing
        if is_processing(video_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="process_audio",
                operation=operation,
                event="validation_error",
                message="Audio processing already in progress",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Audio processing already in progress for this video")
        
        # Check if audio already exists
        if check_audio_exists(video_file.name):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="process_audio",
                operation=operation,
                event="validation_error",
                message="Audio file already exists",
                context={"video_id": video_id, "video_filename": video_file.name}
            )
            raise HTTPException(status_code=400, detail="Audio file already exists for this video")
        
        # Start processing job
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="process_audio",
            operation=operation,
            event="operation_start",
            message="Registering audio processing job",
            context={"video_id": video_id, "video_filename": video_file.name}
        )
        if not start_processing_job(video_id, video_file.name):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="process_audio",
                operation=operation,
                event="validation_error",
                message="Failed to register processing job",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Audio processing already in progress for this video")
        
        # Start async processing
        log_event(
            level="INFO",
            logger="app.routes.videos",
            function="process_audio",
            operation=operation,
            event="operation_start",
            message="Starting async audio processing",
            context={"video_id": video_id, "video_path": str(video_file)}
        )
        process_audio_async(video_id, video_file)
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="process_audio",
            operation=operation,
            message="Audio processing job started",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        
        return {"message": "Audio processing started", "video_id": video_id}
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="process_audio",
            operation=operation,
            error=e,
            message="Error starting audio processing",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.post("/videos/{video_id}/process-transcript")
async def process_transcript(video_id: str):
    """Start transcript generation process for a video."""
    start_time = time.time()
    operation = "process_transcript"
    
    log_operation_start(
        logger="app.routes.videos",
        function="process_transcript",
        operation=operation,
        message=f"Starting transcript generation for {video_id}",
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
                logger="app.routes.videos",
                function="process_transcript",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if audio exists (required for transcript)
        audio_filename = video_file.stem + ".wav"
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="process_transcript",
            operation=operation,
            event="validation_start",
            message="Checking audio file existence",
            context={"audio_filename": audio_filename}
        )
        if not check_audio_exists(video_file.name):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="process_transcript",
                operation=operation,
                event="validation_error",
                message="Audio file not found",
                context={"video_id": video_id, "audio_filename": audio_filename}
            )
            raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")
        
        # Check if already processing
        if is_transcribing(video_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="process_transcript",
                operation=operation,
                event="validation_error",
                message="Transcript generation already in progress",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Transcript generation already in progress for this video")
        
        # Check if transcript already exists
        if check_transcript_exists(audio_filename):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="process_transcript",
                operation=operation,
                event="validation_error",
                message="Transcript already exists",
                context={"video_id": video_id, "audio_filename": audio_filename}
            )
            raise HTTPException(status_code=400, detail="Transcript already exists for this video")
        
        # Start transcription job
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="process_transcript",
            operation=operation,
            event="operation_start",
            message="Registering transcription job",
            context={"video_id": video_id, "audio_filename": audio_filename}
        )
        if not start_transcription_job(video_id, audio_filename):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="process_transcript",
                operation=operation,
                event="validation_error",
                message="Failed to register transcription job",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Transcript generation already in progress for this video")
        
        # Start async processing
        log_event(
            level="INFO",
            logger="app.routes.videos",
            function="process_transcript",
            operation=operation,
            event="operation_start",
            message="Starting async transcript generation",
            context={"video_id": video_id, "audio_filename": audio_filename}
        )
        process_transcription_async(video_id, audio_filename)
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="process_transcript",
            operation=operation,
            message="Transcript generation job started",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        
        return {"message": "Transcript generation started", "video_id": video_id}
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="process_transcript",
            operation=operation,
            error=e,
            message="Error starting transcript generation",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str):
    """Get transcript for a video."""
    start_time = time.time()
    operation = "get_transcript"
    
    log_operation_start(
        logger="app.routes.videos",
        function="get_transcript",
        operation=operation,
        message=f"Getting transcript for {video_id}",
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
                logger="app.routes.videos",
                function="get_transcript",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if audio exists (required for transcript)
        audio_filename = video_file.stem + ".wav"
        if not check_audio_exists(video_file.name):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="get_transcript",
                operation=operation,
                event="validation_error",
                message="Audio file not found",
                context={"video_id": video_id, "audio_filename": audio_filename}
            )
            raise HTTPException(status_code=400, detail="Audio file not found for this video")
        
        # Load transcript
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="get_transcript",
            operation=operation,
            event="file_operation_start",
            message="Loading transcript from file",
            context={"audio_filename": audio_filename}
        )
        transcript_data = load_transcript(audio_filename)
        
        if transcript_data is None:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="get_transcript",
                operation=operation,
                event="validation_error",
                message="Transcript not found",
                context={"video_id": video_id, "audio_filename": audio_filename}
            )
            raise HTTPException(status_code=404, detail="Transcript not found for this video")
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="get_transcript",
            operation=operation,
            message="Successfully retrieved transcript",
            context={
                "video_id": video_id,
                "has_segments": "segment_timestamps" in transcript_data if transcript_data else False,
                "duration_seconds": duration
            }
        )
        
        return transcript_data
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="get_transcript",
            operation=operation,
            error=e,
            message="Error getting transcript",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


class GenerateMomentsRequest(BaseModel):
    """Request model for moment generation."""
    user_prompt: Optional[str] = None
    min_moment_length: float = 60.0
    max_moment_length: float = 600.0
    min_moments: int = 1
    max_moments: int = 10
    model: str = "minimax"
    temperature: float = 0.7


@router.post("/videos/{video_id}/generate-moments")
async def generate_moments(video_id: str, request: GenerateMomentsRequest):
    """Start moment generation process for a video."""
    start_time = time.time()
    operation = "generate_moments"
    
    log_operation_start(
        logger="app.routes.videos",
        function="generate_moments",
        operation=operation,
        message=f"Starting moment generation for {video_id}",
        context={
            "video_id": video_id,
            "request_params": {
                "model": request.model,
                "temperature": request.temperature,
                "min_moment_length": request.min_moment_length,
                "max_moment_length": request.max_moment_length,
                "min_moments": request.min_moments,
                "max_moments": request.max_moments,
                "has_user_prompt": request.user_prompt is not None
            },
            "request_id": get_request_id()
        }
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
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if transcript exists (required for generation)
        audio_filename = video_file.stem + ".wav"
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="generate_moments",
            operation=operation,
            event="validation_start",
            message="Validating prerequisites",
            context={"audio_filename": audio_filename}
        )
        
        if not check_audio_exists(video_file.name):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Audio file not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")
        
        if not check_transcript_exists(audio_filename):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Transcript not found",
                context={"video_id": video_id, "audio_filename": audio_filename}
            )
            raise HTTPException(status_code=400, detail="Transcript not found. Please generate transcript first.")
        
        # Check if already processing
        if is_generating(video_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Moment generation already in progress",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Moment generation already in progress for this video")
        
        # Validate parameters
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="generate_moments",
            operation=operation,
            event="validation_start",
            message="Validating request parameters",
        )
        
        if request.min_moment_length <= 0 or request.max_moment_length <= 0:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Invalid moment length parameters",
                context={"min_moment_length": request.min_moment_length, "max_moment_length": request.max_moment_length}
            )
            raise HTTPException(status_code=400, detail="Moment length must be greater than 0")
        
        if request.min_moment_length > request.max_moment_length:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="min_moment_length > max_moment_length",
                context={"min_moment_length": request.min_moment_length, "max_moment_length": request.max_moment_length}
            )
            raise HTTPException(status_code=400, detail="min_moment_length must be <= max_moment_length")
        
        if request.min_moments <= 0 or request.max_moments <= 0:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Invalid moment count parameters",
                context={"min_moments": request.min_moments, "max_moments": request.max_moments}
            )
            raise HTTPException(status_code=400, detail="Number of moments must be greater than 0")
        
        if request.min_moments > request.max_moments:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="min_moments > max_moments",
                context={"min_moments": request.min_moments, "max_moments": request.max_moments}
            )
            raise HTTPException(status_code=400, detail="min_moments must be <= max_moments")
        
        # Default prompt if not provided
        default_prompt = """Analyze the following video transcript and identify the most important, engaging, or valuable moments. Each moment should represent a distinct topic, insight, or highlight that would be meaningful to viewers.

Generate moments that:
- Capture key insights, turning points, or memorable segments
- Have clear, descriptive titles (5-15 words)
- Represent complete thoughts or concepts
- Are non-overlapping and well-spaced throughout the video"""
        
        user_prompt = request.user_prompt if request.user_prompt else default_prompt
        
        if not user_prompt.strip():
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Prompt is empty",
            )
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="generate_moments",
            operation=operation,
            event="validation_complete",
            message="All validations passed",
            context={"prompt_length": len(user_prompt), "using_default_prompt": request.user_prompt is None}
        )
        
        # Start generation job
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="generate_moments",
            operation=operation,
            event="operation_start",
            message="Registering generation job",
            context={"video_id": video_id}
        )
        if not start_generation_job(video_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Failed to register generation job",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Moment generation already in progress for this video")
        
        # Validate model
        if request.model not in ["minimax", "qwen", "qwen3_omni", "qwen3_vl_fp8"]:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Invalid model",
                context={"model": request.model}
            )
            raise HTTPException(status_code=400, detail="Invalid model. Must be 'minimax', 'qwen', 'qwen3_omni', or 'qwen3_vl_fp8'")
        
        # Validate temperature
        if request.temperature < 0.0 or request.temperature > 2.0:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="generate_moments",
                operation=operation,
                event="validation_error",
                message="Invalid temperature",
                context={"temperature": request.temperature}
            )
            raise HTTPException(status_code=400, detail="Temperature must be between 0.0 and 2.0")
        
        # Start async processing
        log_event(
            level="INFO",
            logger="app.routes.videos",
            function="generate_moments",
            operation=operation,
            event="operation_start",
            message="Starting async moment generation",
            context={
                "video_id": video_id,
                "video_filename": video_file.name,
                "model": request.model,
                "temperature": request.temperature
            }
        )
        process_moments_generation_async(
            video_id=video_id,
            video_filename=video_file.name,
            user_prompt=user_prompt,
            min_moment_length=request.min_moment_length,
            max_moment_length=request.max_moment_length,
            min_moments=request.min_moments,
            max_moments=request.max_moments,
            model=request.model,
            temperature=request.temperature
        )
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="generate_moments",
            operation=operation,
            message="Moment generation job started",
            context={
                "video_id": video_id,
                "model": request.model,
                "duration_seconds": duration
            }
        )
        
        return {"message": "Moment generation started", "video_id": video_id}
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="generate_moments",
            operation=operation,
            error=e,
            message="Error starting moment generation",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/generation-status")
async def get_generation_status_endpoint(video_id: str):
    """Get moment generation status for a video."""
    start_time = time.time()
    
    try:
        video_files = get_video_files()
        
        # Find video by matching stem
        video_file = None
        for vf in video_files:
            if vf.stem == video_id:
                video_file = vf
                break
        
        if not video_file or not video_file.exists():
            duration = time.time() - start_time
            log_status_check(
                endpoint_type="generation",
                video_id=video_id,
                moment_id=None,
                status="error",
                status_code=404,
                duration=duration
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Get generation status
        status = get_generation_status(video_id)
        
        duration = time.time() - start_time
        status_value = status.get("status") if status else "not_started"
        
        log_status_check(
            endpoint_type="generation",
            video_id=video_id,
            moment_id=None,
            status=status_value,
            status_code=200,
            duration=duration
        )
        
        if status is None:
            return {"status": "not_started", "started_at": None}
        
        return status
    except HTTPException as e:
        duration = time.time() - start_time
        log_status_check(
            endpoint_type="generation",
            video_id=video_id,
            moment_id=None,
            status="error",
            status_code=e.status_code,
            duration=duration
        )
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_status_check(
            endpoint_type="generation",
            video_id=video_id,
            moment_id=None,
            status="error",
            status_code=500,
            duration=duration
        )
        raise


class RefineMomentRequest(BaseModel):
    """Request model for moment refinement."""
    user_prompt: Optional[str] = None
    model: str = "minimax"
    temperature: float = 0.7


@router.post("/videos/{video_id}/moments/{moment_id}/refine")
async def refine_moment(video_id: str, moment_id: str, request: RefineMomentRequest):
    """Start moment refinement process."""
    start_time = time.time()
    operation = "refine_moment"
    
    log_operation_start(
        logger="app.routes.videos",
        function="refine_moment",
        operation=operation,
        message=f"Starting moment refinement for {video_id}/{moment_id}",
        context={
            "video_id": video_id,
            "moment_id": moment_id,
            "request_params": {
                "model": request.model,
                "temperature": request.temperature,
                "has_user_prompt": request.user_prompt is not None
            },
            "request_id": get_request_id()
        }
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
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if moment exists
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="refine_moment",
            operation=operation,
            event="validation_start",
            message="Looking up moment",
            context={"moment_id": moment_id}
        )
        moment = get_moment_by_id(video_file.name, moment_id)
        if moment is None:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Moment not found",
                context={"video_id": video_id, "moment_id": moment_id}
            )
            raise HTTPException(status_code=404, detail="Moment not found")
        
        # Check if transcript exists (required for refinement)
        audio_filename = video_file.stem + ".wav"
        if not check_audio_exists(video_file.name):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Audio file not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")
        
        if not check_transcript_exists(audio_filename):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Transcript not found",
                context={"video_id": video_id, "audio_filename": audio_filename}
            )
            raise HTTPException(status_code=400, detail="Transcript not found. Please generate transcript first.")
        
        # Check if already processing
        if is_refining(video_id, moment_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Moment refinement already in progress",
                context={"video_id": video_id, "moment_id": moment_id}
            )
            raise HTTPException(status_code=409, detail="Moment refinement already in progress")
        
        # Validate parameters (padding is now backend config, removed from validation)
        
        # Default prompt if not provided
        default_prompt = """Before refining the timestamps, let's define what a moment is: A moment is a segment of a video (with its corresponding transcript) that represents something engaging, meaningful, or valuable to the viewer. A moment should be a complete, coherent thought or concept that makes sense on its own.

Now, analyze the word-level transcript and identify the precise start and end timestamps for this moment. The current timestamps may be slightly off. Find the exact point where this topic/segment naturally begins and ends.

Guidelines:
- Start the moment at the first word that introduces the topic or begins the engaging segment
- End the moment at the last word that concludes the thought or completes the concept
- Be precise with word boundaries
- Ensure the moment captures complete sentences or phrases
- The refined moment should represent a coherent, engaging segment that makes complete sense on its own"""
        
        user_prompt = request.user_prompt if request.user_prompt else default_prompt
        
        if not user_prompt.strip():
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Prompt is empty",
            )
            raise HTTPException(status_code=400, detail="Prompt cannot be empty")
        
        # Validate model
        if request.model not in ["minimax", "qwen", "qwen3_omni", "qwen3_vl_fp8"]:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Invalid model",
                context={"model": request.model}
            )
            raise HTTPException(status_code=400, detail="Invalid model. Must be 'minimax', 'qwen', 'qwen3_omni', or 'qwen3_vl_fp8'")
        
        # Validate temperature
        if request.temperature < 0.0 or request.temperature > 2.0:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Invalid temperature",
                context={"temperature": request.temperature}
            )
            raise HTTPException(status_code=400, detail="Temperature must be between 0.0 and 2.0")
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="refine_moment",
            operation=operation,
            event="validation_complete",
            message="All validations passed",
            context={
                "moment_title": moment.get("title"),
                "moment_start": moment.get("start_time"),
                "moment_end": moment.get("end_time"),
                "prompt_length": len(user_prompt),
                "using_default_prompt": request.user_prompt is None
            }
        )
        
        # Start refinement job
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="refine_moment",
            operation=operation,
            event="operation_start",
            message="Registering refinement job",
            context={"video_id": video_id, "moment_id": moment_id}
        )
        if not start_refinement_job(video_id, moment_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="refine_moment",
                operation=operation,
                event="validation_error",
                message="Failed to register refinement job",
                context={"video_id": video_id, "moment_id": moment_id}
            )
            raise HTTPException(status_code=409, detail="Moment refinement already in progress")
        
        # Start async processing
        log_event(
            level="INFO",
            logger="app.routes.videos",
            function="refine_moment",
            operation=operation,
            event="operation_start",
            message="Starting async moment refinement",
            context={
                "video_id": video_id,
                "moment_id": moment_id,
                "video_filename": video_file.name,
                "model": request.model,
                "temperature": request.temperature
            }
        )
        process_moment_refinement_async(
            video_id=video_id,
            moment_id=moment_id,
            video_filename=video_file.name,
            user_prompt=user_prompt,
            model=request.model,
            temperature=request.temperature
        )
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="refine_moment",
            operation=operation,
            message="Moment refinement job started",
            context={
                "video_id": video_id,
                "moment_id": moment_id,
                "model": request.model,
                "duration_seconds": duration
            }
        )
        
        return {"message": "Moment refinement started", "video_id": video_id, "moment_id": moment_id}
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="refine_moment",
            operation=operation,
            error=e,
            message="Error starting moment refinement",
            context={"video_id": video_id, "moment_id": moment_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/refinement-status/{moment_id}")
async def get_refinement_status_endpoint(video_id: str, moment_id: str):
    """Get moment refinement status."""
    start_time = time.time()
    
    try:
        video_files = get_video_files()
        
        # Find video by matching stem
        video_file = None
        for vf in video_files:
            if vf.stem == video_id:
                video_file = vf
                break
        
        if not video_file or not video_file.exists():
            duration = time.time() - start_time
            log_status_check(
                endpoint_type="refinement",
                video_id=video_id,
                moment_id=moment_id,
                status="error",
                status_code=404,
                duration=duration
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Get refinement status
        status = get_refinement_status(video_id, moment_id)
        
        duration = time.time() - start_time
        status_value = status.get("status") if status else "not_started"
        
        log_status_check(
            endpoint_type="refinement",
            video_id=video_id,
            moment_id=moment_id,
            status=status_value,
            status_code=200,
            duration=duration
        )
        
        if status is None:
            return {"status": "not_started", "started_at": None}
        
        return status
    except HTTPException as e:
        duration = time.time() - start_time
        log_status_check(
            endpoint_type="refinement",
            video_id=video_id,
            moment_id=moment_id,
            status="error",
            status_code=e.status_code,
            duration=duration
        )
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_status_check(
            endpoint_type="refinement",
            video_id=video_id,
            moment_id=moment_id,
            status="error",
            status_code=500,
            duration=duration
        )
        raise


class ExtractClipsRequest(BaseModel):
    """Request model for clip extraction."""
    override_existing: bool = True


@router.post("/videos/{video_id}/extract-clips")
async def extract_clips(video_id: str, request: ExtractClipsRequest):
    """Start clip extraction process for a video."""
    start_time = time.time()
    operation = "extract_clips"
    
    log_operation_start(
        logger="app.routes.videos",
        function="extract_clips",
        operation=operation,
        message=f"Starting clip extraction for {video_id}",
        context={
            "video_id": video_id,
            "request_params": {
                "override_existing": request.override_existing
            },
            "request_id": get_request_id()
        }
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
                logger="app.routes.videos",
                function="extract_clips",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if moments exist
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="extract_clips",
            operation=operation,
            event="validation_start",
            message="Loading moments",
            context={"video_filename": video_file.name}
        )
        moments = load_moments(video_file.name)
        
        if not moments or len(moments) == 0:
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="extract_clips",
                operation=operation,
                event="validation_error",
                message="No moments found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=400, detail="No moments found for this video")
        
        # Check if already processing
        if is_extracting_clips(video_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="extract_clips",
                operation=operation,
                event="validation_error",
                message="Clip extraction already in progress",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Clip extraction already in progress for this video")
        
        # Padding configuration is now backend-only, no validation needed from request
        
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="extract_clips",
            operation=operation,
            event="validation_complete",
            message="All validations passed",
            context={
                "num_moments": len(moments)
            }
        )
        
        # Start extraction job
        log_event(
            level="DEBUG",
            logger="app.routes.videos",
            function="extract_clips",
            operation=operation,
            event="operation_start",
            message="Registering clip extraction job",
            context={"video_id": video_id}
        )
        if not start_clip_extraction_job(video_id):
            log_event(
                level="WARNING",
                logger="app.routes.videos",
                function="extract_clips",
                operation=operation,
                event="validation_error",
                message="Failed to register extraction job",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=409, detail="Clip extraction already in progress for this video")
        
        # Start async processing
        log_event(
            level="INFO",
            logger="app.routes.videos",
            function="extract_clips",
            operation=operation,
            event="operation_start",
            message="Starting async clip extraction",
            context={
                "video_id": video_id,
                "video_filename": video_file.name,
                "num_moments": len(moments)
            }
        )
        process_clip_extraction_async(
            video_id=video_id,
            video_path=video_file,
            video_filename=video_file.name,
            moments=moments,
            override_existing=request.override_existing
        )
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.routes.videos",
            function="extract_clips",
            operation=operation,
            message="Clip extraction job started",
            context={
                "video_id": video_id,
                "duration_seconds": duration
            }
        )
        
        return {"message": "Clip extraction started", "video_id": video_id}
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.routes.videos",
            function="extract_clips",
            operation=operation,
            error=e,
            message="Error starting clip extraction",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/clip-extraction-status")
async def get_clip_extraction_status_endpoint(video_id: str):
    """Get clip extraction status for a video."""
    start_time = time.time()
    
    try:
        video_files = get_video_files()
        
        # Find video by matching stem
        video_file = None
        for vf in video_files:
            if vf.stem == video_id:
                video_file = vf
                break
        
        if not video_file or not video_file.exists():
            duration = time.time() - start_time
            log_status_check(
                endpoint_type="clip_extraction",
                video_id=video_id,
                moment_id=None,
                status="error",
                status_code=404,
                duration=duration
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Get extraction status
        status = get_clip_extraction_status(video_id)
        
        duration = time.time() - start_time
        status_value = status.get("status") if status else "not_started"
        
        log_status_check(
            endpoint_type="clip_extraction",
            video_id=video_id,
            moment_id=None,
            status=status_value,
            status_code=200,
            duration=duration
        )
        
        if status is None:
            return {"status": "not_started", "started_at": None}
        
        return status
    except HTTPException as e:
        duration = time.time() - start_time
        log_status_check(
            endpoint_type="clip_extraction",
            video_id=video_id,
            moment_id=None,
            status="error",
            status_code=e.status_code,
            duration=duration
        )
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_status_check(
            endpoint_type="clip_extraction",
            video_id=video_id,
            moment_id=None,
            status="error",
            status_code=500,
            duration=duration
        )
        raise
