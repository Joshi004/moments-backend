from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from app.models import Video, Moment
from app.utils.video_utils import get_video_files
from app.utils.thumbnail_service import generate_thumbnail, get_thumbnail_path, get_thumbnail_url
from app.utils.moments_service import load_moments, add_moment
from app.utils.audio_service import check_audio_exists, start_processing_job, is_processing, process_audio_async
from app.utils.transcript_service import check_transcript_exists, start_transcription_job, is_transcribing, process_transcription_async, load_transcript
from app.utils.moments_generation_service import (
    start_generation_job,
    is_generating,
    process_moments_generation_async,
    get_generation_status
)
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
import cv2

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
    try:
        from app.utils.video_utils import get_videos_directory, get_video_files
        videos_dir = get_videos_directory()
        
        # Verify directory before proceeding
        if not videos_dir.exists():
            raise HTTPException(
                status_code=500, 
                detail=f"Videos directory does not exist: {videos_dir}"
            )
        
        video_files = get_video_files()
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
        
        return videos
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        error_msg = f"Error listing videos: {str(e)}\nDirectory: {get_videos_directory() if 'get_videos_directory' in dir() else 'unknown'}\nTraceback:\n{error_details}"
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/videos/{video_id}")
async def get_video(video_id: str):
    """Get metadata for a specific video."""
    video_files = get_video_files()
    
    # Find video by matching stem (filename without extension)
    for video_file in video_files:
        if video_file.stem == video_id:
            thumbnail_url = get_thumbnail_url(video_file.name)
            has_audio = check_audio_exists(video_file.name)
            # Check if transcript exists (transcript is based on audio filename)
            audio_filename = video_file.stem + ".wav"
            has_transcript = check_transcript_exists(audio_filename) if has_audio else False
            return Video(
                id=video_id,
                filename=video_file.name,
                title=video_file.stem.replace("-", " ").replace("_", " ").title(),
                thumbnail_url=thumbnail_url,
                has_audio=has_audio,
                has_transcript=has_transcript
            )
    
    raise HTTPException(status_code=404, detail="Video not found")


@router.get("/videos/{video_id}/stream")
async def stream_video(video_id: str, request: Request):
    """Stream video file with range request support."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    file_path = video_file
    file_size = file_path.stat().st_size
    
    # Handle range requests for video seeking
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
        
        return StreamingResponse(
            generate(),
            status_code=206,
            headers=headers,
            media_type="video/mp4"
        )
    else:
        # Return full file
        return FileResponse(
            file_path,
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            }
        )


@router.get("/videos/{video_id}/thumbnail")
async def get_thumbnail(video_id: str):
    """Get video thumbnail. Generates thumbnail if it doesn't exist."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Get or generate thumbnail
    thumbnail_path = get_thumbnail_path(video_file.name)
    
    # Generate thumbnail if it doesn't exist
    if not thumbnail_path.exists():
        generated_path = generate_thumbnail(video_file)
        if not generated_path:
            raise HTTPException(status_code=500, detail="Failed to generate thumbnail")
        thumbnail_path = generated_path
    
    if not thumbnail_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    
    return FileResponse(
        thumbnail_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000",  # Cache for 1 year
        }
    )


@router.get("/videos/{video_id}/moments", response_model=list[Moment])
async def get_moments(video_id: str):
    """Get all moments for a video."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Load moments from JSON file
    moments = load_moments(video_file.name)
    
    # Convert to Moment models
    return [Moment(**moment) for moment in moments]


@router.post("/videos/{video_id}/moments", response_model=Moment, status_code=201)
async def create_moment(video_id: str, moment: Moment):
    """Add a new moment to a video."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Get video duration for validation
    video_duration = get_video_duration(video_file)
    if video_duration <= 0:
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
        raise HTTPException(status_code=400, detail=error_message)
    
    return Moment(**created_moment)


@router.post("/videos/{video_id}/process-audio")
async def process_audio(video_id: str):
    """Start audio extraction process for a video."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Check if already processing
    if is_processing(video_id):
        raise HTTPException(status_code=409, detail="Audio processing already in progress for this video")
    
    # Check if audio already exists
    if check_audio_exists(video_file.name):
        raise HTTPException(status_code=400, detail="Audio file already exists for this video")
    
    # Start processing job
    if not start_processing_job(video_id, video_file.name):
        raise HTTPException(status_code=409, detail="Audio processing already in progress for this video")
    
    # Start async processing
    process_audio_async(video_id, video_file)
    
    return {"message": "Audio processing started", "video_id": video_id}


@router.post("/videos/{video_id}/process-transcript")
async def process_transcript(video_id: str):
    """Start transcript generation process for a video."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Check if audio exists (required for transcript)
    audio_filename = video_file.stem + ".wav"
    if not check_audio_exists(video_file.name):
        raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")
    
    # Check if already processing
    if is_transcribing(video_id):
        raise HTTPException(status_code=409, detail="Transcript generation already in progress for this video")
    
    # Check if transcript already exists
    if check_transcript_exists(audio_filename):
        raise HTTPException(status_code=400, detail="Transcript already exists for this video")
    
    # Start transcription job
    if not start_transcription_job(video_id, audio_filename):
        raise HTTPException(status_code=409, detail="Transcript generation already in progress for this video")
    
    # Start async processing
    process_transcription_async(video_id, audio_filename)
    
    return {"message": "Transcript generation started", "video_id": video_id}


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str):
    """Get transcript for a video."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Check if audio exists (required for transcript)
    audio_filename = video_file.stem + ".wav"
    if not check_audio_exists(video_file.name):
        raise HTTPException(status_code=400, detail="Audio file not found for this video")
    
    # Load transcript
    transcript_data = load_transcript(audio_filename)
    
    if transcript_data is None:
        raise HTTPException(status_code=404, detail="Transcript not found for this video")
    
    return transcript_data


class GenerateMomentsRequest(BaseModel):
    """Request model for moment generation."""
    user_prompt: Optional[str] = None
    min_moment_length: float = 60.0
    max_moment_length: float = 600.0
    min_moments: int = 1
    max_moments: int = 10


@router.post("/videos/{video_id}/generate-moments")
async def generate_moments(video_id: str, request: GenerateMomentsRequest):
    """Start moment generation process for a video."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Check if transcript exists (required for generation)
    audio_filename = video_file.stem + ".wav"
    if not check_audio_exists(video_file.name):
        raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")
    
    if not check_transcript_exists(audio_filename):
        raise HTTPException(status_code=400, detail="Transcript not found. Please generate transcript first.")
    
    # Check if already processing
    if is_generating(video_id):
        raise HTTPException(status_code=409, detail="Moment generation already in progress for this video")
    
    # Validate parameters
    if request.min_moment_length <= 0 or request.max_moment_length <= 0:
        raise HTTPException(status_code=400, detail="Moment length must be greater than 0")
    
    if request.min_moment_length > request.max_moment_length:
        raise HTTPException(status_code=400, detail="min_moment_length must be <= max_moment_length")
    
    if request.min_moments <= 0 or request.max_moments <= 0:
        raise HTTPException(status_code=400, detail="Number of moments must be greater than 0")
    
    if request.min_moments > request.max_moments:
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
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    
    # Start generation job
    if not start_generation_job(video_id):
        raise HTTPException(status_code=409, detail="Moment generation already in progress for this video")
    
    # Start async processing
    process_moments_generation_async(
        video_id=video_id,
        video_filename=video_file.name,
        user_prompt=user_prompt,
        min_moment_length=request.min_moment_length,
        max_moment_length=request.max_moment_length,
        min_moments=request.min_moments,
        max_moments=request.max_moments
    )
    
    return {"message": "Moment generation started", "video_id": video_id}


@router.get("/videos/{video_id}/generation-status")
async def get_generation_status_endpoint(video_id: str):
    """Get moment generation status for a video."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Get generation status
    status = get_generation_status(video_id)
    
    if status is None:
        return {"status": "not_started", "started_at": None}
    
    return status

