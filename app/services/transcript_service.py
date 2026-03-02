import time
from pathlib import Path
from typing import Optional
import logging
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)
from app.utils.model_config import get_model_config
from app.services.model_connector import get_service_url
from app.database.session import get_session_factory
from app.repositories import transcript_db_repository, video_db_repository

logger = logging.getLogger(__name__)


async def check_transcript_exists(audio_filename: str) -> bool:
    """
    Check if transcript exists in the database for a given audio filename.
    
    Args:
        audio_filename: Name of the audio file (e.g., "motivation.wav")
    
    Returns:
        True if transcript exists in database, False otherwise
    """
    if not audio_filename:
        return False
    
    # Extract identifier from audio filename
    identifier = Path(audio_filename).stem
    
    # Query database
    session_factory = get_session_factory()
    async with session_factory() as session:
        exists = await transcript_db_repository.exists_by_identifier(session, identifier)
        return exists


async def load_transcript(audio_filename: str) -> Optional[dict]:
    """
    Load transcript data from the database.
    
    Args:
        audio_filename: Name of the audio file (e.g., "motivation.wav")
    
    Returns:
        Dictionary containing transcript data or None if not found
    """
    if not audio_filename:
        return None
    
    try:
        # Extract identifier from audio filename
        identifier = Path(audio_filename).stem
        
        # Query database
        session_factory = get_session_factory()
        async with session_factory() as session:
            transcript = await transcript_db_repository.get_by_video_identifier(session, identifier)
            
            if transcript is None:
                return None
            
            # Map database columns back to expected JSON format
            # Note: DB uses "full_text", JSON expects "transcription"
            transcript_data = {
                "transcription": transcript.full_text,
                "word_timestamps": transcript.word_timestamps,
                "segment_timestamps": transcript.segment_timestamps,
                "processing_time": transcript.processing_time_seconds,
            }
            
            logger.info(f"Successfully loaded transcript from database for {identifier}")
            return transcript_data
        
    except Exception as e:
        logger.error(f"Error loading transcript for {audio_filename}: {str(e)}")
        return None


async def save_transcript(audio_filename: str, transcription_data: dict) -> bool:
    """
    Save transcription data to the database.
    
    Args:
        audio_filename: Name of the audio file
        transcription_data: Dictionary containing transcription response
    
    Returns:
        True if successful, False otherwise
    """
    operation = "save_transcript"
    start_time = time.time()
    
    log_operation_start(
        logger="app.services.transcript_service",
        function="save_transcript",
        operation=operation,
        message="Saving transcript to database",
        context={
            "audio_filename": audio_filename,
            "has_segments": "segment_timestamps" in transcription_data if transcription_data else False,
            "has_words": "word_timestamps" in transcription_data if transcription_data else False,
            "request_id": get_request_id()
        }
    )
    
    try:
        # Extract identifier from audio filename
        identifier = Path(audio_filename).stem
        
        # Extract data from transcription response
        # Note: JSON uses "transcription" key, DB expects "full_text"
        full_text = transcription_data.get("transcription", "")
        word_timestamps = transcription_data.get("word_timestamps", [])
        segment_timestamps = transcription_data.get("segment_timestamps", [])
        processing_time = transcription_data.get("processing_time")
        
        # Compute counts
        number_of_words = len(word_timestamps) if word_timestamps else 0
        number_of_segments = len(segment_timestamps) if segment_timestamps else 0
        
        # Save to database
        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                # Look up video by identifier
                video = await video_db_repository.get_by_identifier(session, identifier)
                if not video:
                    logger.error(f"Video '{identifier}' not found in database")
                    return False
                
                # Check if transcript already exists
                exists = await transcript_db_repository.exists_for_video(session, video.id)
                if exists:
                    logger.warning(f"Transcript already exists for video '{identifier}' - skipping database insert")
                else:
                    # Insert transcript
                    transcript = await transcript_db_repository.create(
                        session=session,
                        video_id=video.id,
                        full_text=full_text,
                        word_timestamps=word_timestamps,
                        segment_timestamps=segment_timestamps,
                        language="en",
                        number_of_words=number_of_words,
                        number_of_segments=number_of_segments,
                        transcription_service="parakeet",
                        processing_time_seconds=processing_time,
                    )
                    await session.commit()
                    logger.info(f"Saved transcript to database (id={transcript.id})")
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Database error saving transcript: {str(e)}")
                return False
        
        duration = time.time() - start_time
        
        log_operation_complete(
            logger="app.services.transcript_service",
            function="save_transcript",
            operation=operation,
            message="Successfully saved transcript to database",
            context={
                "audio_filename": audio_filename,
                "duration_seconds": duration
            }
        )
        return True
        
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="save_transcript",
            operation=operation,
            error=e,
            message="Error saving transcript",
            context={
                "audio_filename": audio_filename,
                "duration_seconds": duration
            }
        )
        return False


async def call_transcription_service_async(audio_url: str) -> Optional[dict]:
    """
    Call the remote transcription service via tunnel asynchronously using httpx.
    
    This is the async version of call_transcription_service() for use in async contexts.
    The SSH tunnel must already be established before calling this function.
    
    Args:
        audio_url: URL to the audio file (e.g., GCS signed URL)
    
    Returns:
        Dictionary with transcription response or None if failed
    """
    import httpx
    
    operation = "transcription_api_call_async"
    start_time = time.time()
    
    # Get service URL from config
    service_url = await get_service_url("parakeet")
    
    log_operation_start(
        logger="app.services.transcript_service",
        function="call_transcription_service_async",
        operation=operation,
        message="Calling transcription service (async)",
        context={
            "audio_url": audio_url,
            "service_url": service_url,
            "request_id": get_request_id()
        }
    )
    
    try:
        payload = {"audio_url": audio_url}
        
        log_event(
            level="DEBUG",
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            event="external_call_start",
            message="Sending request to transcription service",
            context={"payload": payload}
        )
        
        # Use httpx AsyncClient for async HTTP requests
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                service_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
        
        duration = time.time() - start_time
        
        log_event(
            level="DEBUG",
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            event="external_call_complete",
            message="Received response from transcription service",
            context={
                "status_code": response.status_code,
                "response_size_bytes": len(response.content) if response.content else 0,
                "duration_seconds": duration
            }
        )
        
        response.raise_for_status()
        
        result = response.json()
        processing_time = result.get('processing_time', 0)
        
        log_operation_complete(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            message="Transcription service call completed (async)",
            context={
                "audio_url": audio_url,
                "processing_time_seconds": processing_time,
                "has_segments": "segment_timestamps" in result if result else False,
                "has_words": "word_timestamps" in result if result else False,
                "duration_seconds": duration
            }
        )
        
        return result
        
    except httpx.HTTPStatusError as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="HTTP error calling transcription service (async)",
            context={
                "audio_url": audio_url,
                "status_code": e.response.status_code if e.response else None,
                "response_preview": e.response.text[:500] if e.response and hasattr(e.response, 'text') else None,
                "duration_seconds": duration
            }
        )
        return None
    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="Timeout calling transcription service (async)",
            context={
                "audio_url": audio_url,
                "timeout_seconds": 300,
                "duration_seconds": duration
            }
        )
        return None
    except httpx.ConnectError as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="Connection error calling transcription service (async)",
            context={
                "audio_url": audio_url,
                "duration_seconds": duration
            }
        )
        return None
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="Unexpected error in transcription service call (async)",
            context={
                "audio_url": audio_url,
                "duration_seconds": duration
            }
        )
        return None


async def process_transcription(
    video_id: str,
    audio_signed_url: str
) -> dict:
    """
    Process transcription as an async coroutine using GCS signed URL.
    
    This is the recommended async version that integrates with the pipeline orchestrator.
    Unlike the deprecated thread-based version, this function:
    - Returns result directly (no Redis polling needed)
    - Raises exceptions on errors (native exception handling)
    - Can be used with asyncio.wait_for() for timeout handling
    
    Args:
        video_id: ID of the video (filename stem)
        audio_signed_url: GCS signed URL for the audio file
    
    Returns:
        Dictionary with transcription result
    
    Raises:
        Exception: If transcription fails with an error that should stop processing
    """
    operation = "transcription_processing"
    start_time = time.time()
    
    try:
        # Extract audio filename from video_id for saving transcript
        audio_filename = f"{video_id}.wav"
        
        log_operation_start(
            logger="app.services.transcript_service",
            function="process_transcription",
            operation=operation,
            message="Starting transcription processing (async)",
            context={
                "video_id": video_id,
                "audio_url_type": "gcs_signed_url",
                "request_id": get_request_id()
            }
        )
        
        # Call transcription service asynchronously
        transcription_result = await call_transcription_service_async(audio_signed_url)
        
        if transcription_result is None:
            raise Exception("Transcription service returned no result")
        
        # Save transcript to database
        success = await save_transcript(audio_filename, transcription_result)
        
        if not success:
            raise Exception("Failed to save transcript")
        
        duration = time.time() - start_time
        log_operation_complete(
            logger="app.services.transcript_service",
            function="process_transcription",
            operation=operation,
            message="Transcription processing completed successfully (async)",
            context={
                "video_id": video_id,
                "duration_seconds": duration
            }
        )
        
        return transcription_result
    
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="process_transcription",
            operation=operation,
            error=e,
            message="Error in transcription processing (async)",
            context={
                "video_id": video_id,
                "duration_seconds": duration
            }
        )
        raise
