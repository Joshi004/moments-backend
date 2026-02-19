"""
Clip extraction and availability API endpoints.
Handles video clip extraction for moments.
"""
from fastapi import APIRouter, HTTPException
import time

from app.models.schemas import ExtractClipsRequest, VideoAvailabilityResponse
from app.utils.video import get_video_files
from app.services.moments_service import load_moments, get_moment_by_id
from app.services.video_clipping_service import (
    process_clip_extraction_async,
    check_clip_exists,
    get_clip_duration
)
from app.services import job_tracker
from app.services.transcript_service import load_transcript
from app.utils.model_config import model_supports_video, get_video_clip_url, get_duration_tolerance, get_clipping_config
from app.utils.timestamp import calculate_padded_boundaries, extract_words_in_range
from app.core.logging import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()


@router.post("/videos/{video_id}/extract-clips")
async def extract_clips(video_id: str, request: ExtractClipsRequest):
    """Start clip extraction process for a video."""
    start_time = time.time()
    operation = "extract_clips"
    
    log_operation_start(
        logger="app.api.endpoints.clips",
        function="extract_clips",
        operation=operation,
        message=f"Starting clip extraction for {video_id}",
        context={
            "video_id": video_id,
            "request_params": {"override_existing": request.override_existing},
            "request_id": get_request_id()
        }
    )
    
    try:
        video_files = get_video_files()
        
        # Find video
        video_file = None
        for vf in video_files:
            if vf.stem == video_id:
                video_file = vf
                break
        
        if not video_file or not video_file.exists():
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if moments exist
        moments = await load_moments(video_file.name)
        
        if not moments or len(moments) == 0:
            raise HTTPException(status_code=400, detail="No moments found for this video")
        
        # Check if already processing
        if await job_tracker.is_running("clip_extraction", video_id):
            raise HTTPException(status_code=409, detail="Clip extraction already in progress for this video")
        
        # Start extraction job
        job_created = await job_tracker.create_job("clip_extraction", video_id)
        if not job_created:
            raise HTTPException(status_code=409, detail="Clip extraction already in progress for this video")
        
        # Start async processing
        process_clip_extraction_async(
            video_id=video_id,
            video_path=video_file,
            video_filename=video_file.name,
            moments=moments,
            override_existing=request.override_existing
        )
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.clips",
            function="extract_clips",
            operation=operation,
            message="Clip extraction job started",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        
        return {"message": "Clip extraction started", "video_id": video_id}
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.clips",
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
    try:
        video_files = get_video_files()
        
        # Find video
        video_file = None
        for vf in video_files:
            if vf.stem == video_id:
                video_file = vf
                break
        
        if not video_file or not video_file.exists():
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Get status
        job = await job_tracker.get_job("clip_extraction", video_id)
        
        if job is None:
            return {"status": "not_started", "started_at": None}
        
        # Build response with progress fields if available
        response = {
            "status": job.get("status"),
            "started_at": job.get("started_at")
        }
        
        # Add progress fields if they exist
        if "total_moments" in job:
            response["total_moments"] = int(job.get("total_moments", 0))
        if "processed_moments" in job:
            response["processed_moments"] = int(job.get("processed_moments", 0))
        if "failed_moments" in job:
            response["failed_moments"] = int(job.get("failed_moments", 0))
        
        return response
        
    except HTTPException:
        raise


@router.get("/videos/{video_id}/moments/{moment_id}/video-availability", response_model=VideoAvailabilityResponse)
async def check_video_availability(video_id: str, moment_id: str):
    """
    Check if video clip is available for a moment and validate alignment with transcript.
    """
    start_time = time.time()
    operation = "check_video_availability"
    
    log_operation_start(
        logger="app.api.endpoints.clips",
        function="check_video_availability",
        operation=operation,
        message=f"Checking video availability for {video_id}/{moment_id}",
        context={
            "video_id": video_id,
            "moment_id": moment_id,
            "request_id": get_request_id()
        }
    )
    
    try:
        video_files = get_video_files()
        
        # Find video
        video_file = None
        for vf in video_files:
            if vf.stem == video_id:
                video_file = vf
                break
        
        if not video_file or not video_file.exists():
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if moment exists
        moment = await get_moment_by_id(video_file.name, moment_id)
        if moment is None:
            raise HTTPException(status_code=404, detail="Moment not found")
        
        # Check if clip exists
        clip_exists = check_clip_exists(moment_id, video_file.name)
        
        result = VideoAvailabilityResponse(
            available=False,
            clip_url=None,
            clip_duration=None,
            transcript_duration=None,
            duration_match=False,
            warning=None,
            model_supports_video=model_supports_video("qwen3_vl_fp8")
        )
        
        if not clip_exists:
            result.warning = "Video clip not available. Extract clips first to enable video refinement."
            return result
        
        # Get clip duration
        clip_duration = get_clip_duration(moment_id, video_file.name)
        if clip_duration is None or clip_duration <= 0:
            result.warning = "Could not determine video clip duration."
            return result
        
        result.clip_duration = clip_duration
        result.clip_url = get_video_clip_url(moment_id, video_file.name)
        
        # Load transcript for validation
        audio_filename = video_file.stem + ".wav"
        transcript_data = await load_transcript(audio_filename)
        
        if transcript_data is None or 'word_timestamps' not in transcript_data:
            result.warning = "Transcript not available. Cannot validate alignment."
            result.available = True
            return result
        
        # Calculate transcript duration
        clipping_config = get_clipping_config()
        padding = clipping_config['padding']
        margin = clipping_config.get('margin', 2.0)
        
        word_timestamps = transcript_data['word_timestamps']
        
        try:
            padded_start, padded_end = calculate_padded_boundaries(
                word_timestamps,
                moment['start_time'],
                moment['end_time'],
                padding,
                margin
            )
            
            words_in_range = extract_words_in_range(word_timestamps, padded_start, padded_end)
            
            if words_in_range:
                first_word_start = words_in_range[0]['start']
                last_word_end = words_in_range[-1]['end']
                transcript_duration = last_word_end - first_word_start
                
                result.transcript_duration = transcript_duration
                
                # Check if durations match
                duration_diff = abs(clip_duration - transcript_duration)
                tolerance = get_duration_tolerance()
                
                result.duration_match = duration_diff <= tolerance
                result.available = True
                
                if not result.duration_match:
                    result.warning = f"Duration mismatch: clip={clip_duration:.2f}s, transcript={transcript_duration:.2f}s (diff={duration_diff:.2f}s)"
            else:
                result.warning = "No words found in transcript range"
                result.available = True
                
        except Exception as e:
            result.warning = f"Could not validate alignment: {str(e)}"
            result.available = True
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.clips",
            function="check_video_availability",
            operation=operation,
            message="Video availability check complete",
            context={
                "video_id": video_id,
                "moment_id": moment_id,
                "available": result.available,
                "duration_match": result.duration_match,
                "duration_seconds": duration
            }
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.clips",
            function="check_video_availability",
            operation=operation,
            error=e,
            message="Error checking video availability",
            context={"video_id": video_id, "moment_id": moment_id, "duration_seconds": duration}
        )
        raise

