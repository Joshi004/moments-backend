"""
Transcript-related API endpoints.
Handles audio extraction and transcript generation.
"""
from fastapi import APIRouter, HTTPException
import time

from app.models.schemas import MessageResponse
from app.utils.video import get_video_files
from app.services.audio_service import (
    check_audio_exists,
    process_audio_async
)
from app.services.transcript_service import (
    check_transcript_exists,
    process_transcription_async,
    load_transcript
)
from app.repositories.job_repository import JobRepository, JobType, JobStatus

# Initialize job repository
job_repo = JobRepository()
from app.core.logging import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()


@router.post("/videos/{video_id}/process-audio")
async def process_audio(video_id: str):
    """Start audio extraction process for a video."""
    start_time = time.time()
    operation = "process_audio"
    
    log_operation_start(
        logger="app.api.endpoints.transcripts",
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
                logger="app.api.endpoints.transcripts",
                function="process_audio",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if already processing
        if job_repo.is_processing(JobType.AUDIO_EXTRACTION, video_id):
            log_event(
                level="WARNING",
                logger="app.api.endpoints.transcripts",
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
                logger="app.api.endpoints.transcripts",
                function="process_audio",
                operation=operation,
                event="validation_error",
                message="Audio file already exists",
                context={"video_id": video_id, "video_filename": video_file.name}
            )
            raise HTTPException(status_code=400, detail="Audio file already exists for this video")
        
        # Start processing job
        job = job_repo.create(JobType.AUDIO_EXTRACTION, video_id, video_filename=video_file.name)
        if job is None:
            raise HTTPException(status_code=409, detail="Audio processing already in progress for this video")
        
        # Start async processing
        process_audio_async(video_id, video_file)
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.transcripts",
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
            logger="app.api.endpoints.transcripts",
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
        logger="app.api.endpoints.transcripts",
        function="process_transcript",
        operation=operation,
        message=f"Starting transcript generation for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
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
            log_event(
                level="WARNING",
                logger="app.api.endpoints.transcripts",
                function="process_transcript",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Check if audio exists
        audio_filename = video_file.stem + ".wav"
        if not check_audio_exists(video_file.name):
            raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")
        
        # Check if already processing
        if job_repo.is_processing(JobType.TRANSCRIPTION, video_id):
            raise HTTPException(status_code=409, detail="Transcript generation already in progress for this video")
        
        # Check if transcript already exists
        if check_transcript_exists(audio_filename):
            raise HTTPException(status_code=400, detail="Transcript already exists for this video")
        
        # Start transcription job
        job = job_repo.create(JobType.TRANSCRIPTION, video_id, audio_filename=audio_filename)
        if job is None:
            raise HTTPException(status_code=409, detail="Transcript generation already in progress for this video")
        
        # Start async processing
        process_transcription_async(video_id, audio_filename)
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.transcripts",
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
            logger="app.api.endpoints.transcripts",
            function="process_transcript",
            operation=operation,
            error=e,
            message="Error starting transcript generation",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/audio-extraction-status")
async def get_audio_extraction_status(video_id: str):
    """Get audio extraction status for a video."""
    start_time = time.time()
    
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
        
        # Get status and update last poll
        job = job_repo.get(JobType.AUDIO_EXTRACTION, video_id)
        if job:
            job_repo.update_last_poll(JobType.AUDIO_EXTRACTION, video_id)
        
        duration = time.time() - start_time
        
        if job is None:
            return {"status": "not_started", "started_at": None}
        
        return {
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "elapsed_seconds": time.time() - job.get("started_at", time.time()),
            "error": job.get("error")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.transcripts",
            function="get_audio_extraction_status",
            operation="get_audio_extraction_status",
            error=e,
            message="Error getting audio extraction status",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/transcription-status")
async def get_transcription_status(video_id: str):
    """Get transcription status for a video."""
    start_time = time.time()
    
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
        
        # Get status and update last poll
        job = job_repo.get(JobType.TRANSCRIPTION, video_id)
        if job:
            job_repo.update_last_poll(JobType.TRANSCRIPTION, video_id)
        
        duration = time.time() - start_time
        
        if job is None:
            return {"status": "not_started", "started_at": None}
        
        return {
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "elapsed_seconds": time.time() - job.get("started_at", time.time()),
            "error": job.get("error")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.transcripts",
            function="get_transcription_status",
            operation="get_transcription_status",
            error=e,
            message="Error getting transcription status",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str):
    """Get transcript for a video."""
    start_time = time.time()
    operation = "get_transcript"
    
    log_operation_start(
        logger="app.api.endpoints.transcripts",
        function="get_transcript",
        operation=operation,
        message=f"Getting transcript for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
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
        
        # Check if audio exists
        audio_filename = video_file.stem + ".wav"
        if not check_audio_exists(video_file.name):
            raise HTTPException(status_code=400, detail="Audio file not found for this video")
        
        # Load transcript
        transcript_data = load_transcript(audio_filename)
        
        if transcript_data is None:
            raise HTTPException(status_code=404, detail="Transcript not found for this video")
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.transcripts",
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
            logger="app.api.endpoints.transcripts",
            function="get_transcript",
            operation=operation,
            error=e,
            message="Error getting transcript",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise

