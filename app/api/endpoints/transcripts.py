"""
Transcript-related API endpoints.
Handles audio extraction and transcript generation.
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import time
import asyncio

from app.models.schemas import MessageResponse
from app.utils.video import get_video_files
from app.database.dependencies import get_db
from app.repositories import video_db_repository
from app.services.audio_service import (
    check_audio_exists,
    process_audio_async,
    get_audio_path
)
from app.services.transcript_service import (
    check_transcript_exists,
    process_transcription,
    load_transcript
)
from app.services.pipeline.upload_service import GCSUploader
from app.services import job_tracker
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
        if await job_tracker.is_running("audio_extraction", video_id):
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
        job_created = await job_tracker.create_job("audio_extraction", video_id, video_filename=video_file.name)
        if not job_created:
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
        if await job_tracker.is_running("transcription", video_id):
            raise HTTPException(status_code=409, detail="Transcript generation already in progress for this video")
        
        # Check if transcript already exists
        if await check_transcript_exists(audio_filename):
            raise HTTPException(status_code=400, detail="Transcript already exists for this video")
        
        # Upload audio to GCS
        uploader = GCSUploader()
        audio_path = get_audio_path(video_file.name)
        
        log_event(
            level="INFO",
            logger="app.api.endpoints.transcripts",
            function="process_transcript",
            operation=operation,
            event="gcs_upload_start",
            message="Uploading audio to GCS",
            context={"video_id": video_id, "audio_path": str(audio_path)}
        )
        
        _, audio_signed_url = await uploader.upload_audio(audio_path, video_id)
        
        # Call async transcription with timeout
        try:
            result = await asyncio.wait_for(
                process_transcription(video_id, audio_signed_url),
                timeout=600  # 10 minutes
            )
            
            duration = time.time() - start_time
            log_operation_complete(
                logger="app.api.endpoints.transcripts",
                function="process_transcript",
                operation=operation,
                message="Transcription completed successfully",
                context={"video_id": video_id, "duration_seconds": duration}
            )
            
            return {"message": "Transcription completed", "video_id": video_id}
            
        except asyncio.TimeoutError:
            duration = time.time() - start_time
            log_operation_error(
                logger="app.api.endpoints.transcripts",
                function="process_transcript",
                operation=operation,
                error=Exception("Timeout"),
                message="Transcription timed out",
                context={"video_id": video_id, "duration_seconds": duration}
            )
            raise HTTPException(status_code=504, detail="Transcription timed out after 600 seconds")
        
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
        
        # Get status
        job = await job_tracker.get_job("audio_extraction", video_id)
        
        duration = time.time() - start_time
        
        if job is None:
            return {"status": "not_started", "started_at": None}
        
        started_at = float(job.get("started_at", time.time()))
        return {
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "elapsed_seconds": time.time() - started_at,
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
        
        # Get status
        job = await job_tracker.get_job("transcription", video_id)
        
        duration = time.time() - start_time
        
        if job is None:
            return {"status": "not_started", "started_at": None}
        
        started_at = float(job.get("started_at", time.time()))
        return {
            "status": job.get("status"),
            "started_at": job.get("started_at"),
            "elapsed_seconds": time.time() - started_at,
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
async def get_transcript(video_id: str, db: AsyncSession = Depends(get_db)):
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
        # Validate video exists in database
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")
        
        # Load transcript from database
        audio_filename = f"{video_id}.wav"
        transcript_data = await load_transcript(audio_filename)
        
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

