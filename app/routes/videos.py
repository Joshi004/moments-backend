from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from app.models import Video, Moment, GenerateMomentsRequest
from app.utils.video_utils import get_video_files
from app.utils.thumbnail_service import generate_thumbnail, get_thumbnail_path, get_thumbnail_url
from app.utils.moments_service import load_moments, add_moment
from app.utils.audio_service import (
    check_audio_exists,
    start_processing_job,
    is_processing,
    process_audio_async,
    get_processing_jobs,
    get_audio_path
)
from app.utils.transcript_service import (
    check_transcript_exists,
    start_transcription_job,
    is_transcribing,
    process_transcription_async,
    get_transcription_jobs,
    load_transcript
)
from app.utils.moments_generation_service import (
    extract_segment_data,
    build_prompt,
    call_ai_model,
    parse_moments_response,
    ssh_tunnel,
    verify_tunnel_active,
    start_generation_job,
    complete_generation_job,
    is_generating,
    get_generation_jobs,
    SSH_REMOTE_PORT
)
from pathlib import Path
import cv2
import logging

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
            # Check if transcript exists (need audio filename)
            audio_filename = get_audio_path(video_file.name).name if has_audio else None
            has_transcript = check_transcript_exists(audio_filename) if audio_filename else False
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


@router.get("/videos/processing-status")
async def get_processing_status():
    """Get status of all active audio processing jobs."""
    return get_processing_jobs()


@router.get("/videos/transcription-status")
async def get_transcription_status():
    """Get status of all active transcription jobs."""
    return get_transcription_jobs()


@router.get("/videos/generation-status")
async def get_generation_status():
    """Get status of all active moment generation jobs."""
    return get_generation_jobs()


@router.get("/videos/{video_id}")
async def get_video(video_id: str):
    """Get metadata for a specific video."""
    video_files = get_video_files()
    
    # Find video by matching stem (filename without extension)
    for video_file in video_files:
        if video_file.stem == video_id:
            thumbnail_url = get_thumbnail_url(video_file.name)
            has_audio = check_audio_exists(video_file.name)
            # Check if transcript exists (need audio filename)
            audio_filename = get_audio_path(video_file.name).name if has_audio else None
            has_transcript = check_transcript_exists(audio_filename) if audio_filename else False
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
    import logging
    logger = logging.getLogger(__name__)
    
    video_files = get_video_files()
    
    # Debug logging
    logger.info(f"Processing audio request for video_id: {video_id}")
    logger.info(f"Available video stems: {[vf.stem for vf in video_files]}")
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        available_ids = [vf.stem for vf in video_files]
        error_msg = f"Video not found. Requested ID: '{video_id}'. Available IDs: {available_ids}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    # Check if audio already exists
    if check_audio_exists(video_file.name):
        return {"message": "Audio file already exists", "video_id": video_id}
    
    # Check if already processing
    if is_processing(video_id):
        return {"message": "Audio processing already in progress", "video_id": video_id}
    
    # Start processing job
    if not start_processing_job(video_id, video_file.name):
        return {"message": "Failed to start processing job", "video_id": video_id}
    
    # Start async processing
    process_audio_async(video_id, video_file)
    
    return {
        "message": "Audio processing started",
        "video_id": video_id,
        "status": "processing"
    }


@router.post("/videos/{video_id}/process-transcript")
async def process_transcript(video_id: str):
    """Start transcription process for a video's audio file."""
    import logging
    logger = logging.getLogger(__name__)
    
    video_files = get_video_files()
    
    # Debug logging
    logger.info(f"Processing transcript request for video_id: {video_id}")
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        available_ids = [vf.stem for vf in video_files]
        error_msg = f"Video not found. Requested ID: '{video_id}'. Available IDs: {available_ids}"
        logger.error(error_msg)
        raise HTTPException(status_code=404, detail=error_msg)
    
    # Check if audio exists (prerequisite)
    if not check_audio_exists(video_file.name):
        raise HTTPException(
            status_code=400, 
            detail="Audio file does not exist. Please extract audio first."
        )
    
    # Get audio filename
    audio_path = get_audio_path(video_file.name)
    audio_filename = audio_path.name
    
    # Check if transcript already exists
    if check_transcript_exists(audio_filename):
        return {"message": "Transcript file already exists", "video_id": video_id}
    
    # Check if already processing
    if is_transcribing(video_id):
        return {"message": "Transcription already in progress", "video_id": video_id}
    
    # Start transcription job
    if not start_transcription_job(video_id, audio_filename):
        return {"message": "Failed to start transcription job", "video_id": video_id}
    
    # Start async processing
    process_transcription_async(video_id, audio_filename)
    
    return {
        "message": "Transcription processing started",
        "video_id": video_id,
        "status": "processing"
    }


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str):
    """Get transcript data for a video."""
    import logging
    logger = logging.getLogger(__name__)
    
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Check if audio exists (prerequisite for transcript)
    if not check_audio_exists(video_file.name):
        raise HTTPException(
            status_code=404,
            detail="Audio file does not exist. Transcript requires audio file."
        )
    
    # Get audio filename
    audio_path = get_audio_path(video_file.name)
    audio_filename = audio_path.name
    
    # Load transcript
    transcript_data = load_transcript(audio_filename)
    
    if transcript_data is None:
        raise HTTPException(
            status_code=404,
            detail="Transcript not found for this video"
        )
    
    return transcript_data


@router.post("/videos/{video_id}/generate-moments")
async def generate_moments(video_id: str, request: GenerateMomentsRequest):
    """Generate moments for a video using AI model."""
    logger = logging.getLogger(__name__)
    
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Check if audio exists (prerequisite for transcript)
    if not check_audio_exists(video_file.name):
        raise HTTPException(
            status_code=400,
            detail="Audio file does not exist. Please extract audio first."
        )
    
    # Get audio filename
    audio_path = get_audio_path(video_file.name)
    audio_filename = audio_path.name
    
    # Check if transcript exists
    if not check_transcript_exists(audio_filename):
        raise HTTPException(
            status_code=400,
            detail="Transcript does not exist. Please generate transcript first."
        )
    
    # Check if already generating
    if is_generating(video_id):
        raise HTTPException(
            status_code=400,
            detail="Moment generation already in progress for this video"
        )
    
    # Validate num_moments
    if request.num_moments < 1 or request.num_moments > 20:
        raise HTTPException(
            status_code=400,
            detail="num_moments must be between 1 and 20"
        )
    
    # Start generation job
    if not start_generation_job(video_id):
        raise HTTPException(
            status_code=400,
            detail="Failed to start generation job"
        )
    
    try:
        # Load transcript
        transcript_data = load_transcript(audio_filename)
        if transcript_data is None:
            raise HTTPException(
                status_code=404,
                detail="Transcript not found"
            )
        
        # Extract segment data
        segments = extract_segment_data(transcript_data)
        if not segments:
            raise HTTPException(
                status_code=400,
                detail="No segments found in transcript"
            )
        
        # Build prompt (user prompt is fully editable, segments will be inserted)
        complete_prompt = build_prompt(request.prompt, segments)
        
        # Get video duration for validation
        video_duration = get_video_duration(video_file)
        if video_duration <= 0:
            raise HTTPException(status_code=500, detail="Could not determine video duration")
        
        # Call AI model via SSH tunnel
        messages = [
            {
                "role": "user",
                "content": complete_prompt
            }
        ]
        
        try:
            # Test SSH connection first
            from app.utils.moments_generation_service import test_ssh_connection, check_remote_service
            logger.info(f"Testing SSH connection before creating tunnel for video {video_id}")
            if not test_ssh_connection():
                raise HTTPException(
                    status_code=500,
                    detail="Cannot establish SSH connection to remote host. Please check SSH configuration and network connectivity."
                )
            
            # Check if remote service is accessible
            logger.info(f"Checking remote AI model service for video {video_id}")
            if not check_remote_service():
                logger.warning("Remote service check failed, but proceeding with tunnel creation...")
            
            with ssh_tunnel():
                # Wait a bit for tunnel to establish
                import time
                time.sleep(2)
                
                # Verify tunnel is active before making API call
                logger.info(f"Verifying SSH tunnel connectivity for video {video_id}")
                tunnel_verified = verify_tunnel_active(max_retries=3, retry_delay=1.0)
                if not tunnel_verified:
                    logger.warning("Tunnel verification failed, but port is accessible - proceeding with API call")
                    # Don't fail immediately - port might be accessible even if test request fails
                    # The actual API call might work
                
                # Call AI model
                logger.info(f"Calling AI model for video {video_id}")
                ai_response = call_ai_model(messages)
                
                if ai_response is None:
                    logger.error(f"AI model returned None for video {video_id}")
                    # Provide more specific error message
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to get response from AI model. The tunnel appears to be working, but the service on remote port {SSH_REMOTE_PORT} is not responding. Please verify: 1) The model service is running on the remote server, 2) It's listening on port {SSH_REMOTE_PORT}, 3) The service is accessible from the remote server."
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error calling AI model via SSH tunnel for video {video_id}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=500,
                detail=f"Error connecting to AI model: {str(e)}"
            )
        
        # Parse response
        try:
            moments = parse_moments_response(ai_response)
        except ValueError as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to parse AI response: {str(e)}"
            )
        
        if not moments:
            raise HTTPException(
                status_code=500,
                detail="No valid moments found in AI response"
            )
        
        # Add each moment to the video
        added_count = 0
        errors = []
        
        for moment in moments:
            moment_dict = {
                "start_time": moment["start_time"],
                "end_time": moment["end_time"],
                "title": moment["title"]
            }
            
            success, error_message, created_moment = add_moment(
                video_file.name,
                moment_dict,
                video_duration
            )
            
            if success:
                added_count += 1
            else:
                errors.append(f"Moment '{moment.get('title', 'Unknown')}': {error_message}")
        
        # Mark job as complete
        complete_generation_job(video_id, success=True)
        
        result = {
            "message": f"Successfully generated {added_count} moment(s)",
            "video_id": video_id,
            "moments_added": added_count,
            "moments_requested": request.num_moments
        }
        
        if errors:
            result["errors"] = errors
        
        return result
        
    except HTTPException:
        complete_generation_job(video_id, success=False)
        raise
    except Exception as e:
        logger.error(f"Error generating moments for {video_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        complete_generation_job(video_id, success=False)
        raise HTTPException(
            status_code=500,
            detail=f"Error generating moments: {str(e)}"
        )

