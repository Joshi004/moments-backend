import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List
import logging
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

logger = logging.getLogger(__name__)


def get_audio_path(video_identifier: str) -> Path:
    """
    Get the temp path for an audio file based on the video identifier (stem).

    Args:
        video_identifier: Video identifier stem (e.g. "motivation") or full
                          filename (e.g. "motivation.mp4") -- stem is extracted
                          automatically so callers can pass either form.

    Returns:
        Path to temp/audio/{identifier}/{identifier}.wav
    """
    from app.services.temp_file_manager import get_temp_file_path
    identifier = Path(video_identifier).stem
    return get_temp_file_path("audio", identifier, f"{identifier}.wav")


def check_audio_exists(video_identifier: str) -> bool:
    """Check if a temp audio file exists for the given video identifier."""
    audio_path = get_audio_path(video_identifier)
    return audio_path.exists() and audio_path.is_file()


def extract_audio_from_video(video_path: Path, output_path: Path) -> bool:
    """
    Extract audio from a video file and save it as WAV.

    The video must already exist locally before calling this function.
    Pre-downloading is the orchestrator's responsibility via ensure_local_video_async().

    Args:
        video_path: Path to the local video file
        output_path: Path where audio file should be saved

    Returns:
        True if successful, False otherwise
    """
    operation = "audio_extraction"
    start_time = time.time()
    
    log_operation_start(
        logger="app.services.audio_service",
        function="extract_audio_from_video",
        operation=operation,
        message="Starting audio extraction",
        context={
            "video_path": str(video_path),
            "output_path": str(output_path),
            "request_id": get_request_id()
        }
    )
    
    try:
        # The orchestrator pre-downloads the video before calling this function.
        # If the file is missing here, something went wrong upstream.
        if not video_path.exists():
            raise FileNotFoundError(
                f"Video file not found: {video_path}. "
                f"The orchestrator must pre-download the video before calling audio extraction."
            )

        # Ensure output directory exists
        log_event(
            level="DEBUG",
            logger="app.services.audio_service",
            function="extract_audio_from_video",
            operation=operation,
            event="file_operation_start",
            message="Ensuring output directory exists",
            context={"output_dir": str(output_path.parent)}
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # FFmpeg command to extract audio as WAV
        # -vn: disable video
        # -acodec pcm_s16le: PCM 16-bit little-endian (WAV format)
        # -ar 16000: sample rate 16 kHz (optimized for speech transcription)
        # -ac 1: mono (1 channel, optimized for speech)
        cmd = [
            'ffmpeg',
            '-i', str(video_path),
            '-vn',  # No video
            '-acodec', 'pcm_s16le',  # PCM 16-bit little-endian
            '-ar', '16000',  # Sample rate 16 kHz
            '-ac', '1',  # Mono
            '-y',  # Overwrite output file if it exists
            str(output_path)
        ]
        
        log_event(
            level="INFO",
            logger="app.services.audio_service",
            function="extract_audio_from_video",
            operation=operation,
            event="external_call_start",
            message="Executing FFmpeg command",
            context={
                "command": " ".join(cmd),
                "video_path": str(video_path),
                "output_path": str(output_path)
            }
        )
        
        # Run FFmpeg command
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout for very long videos
        )
        
        duration = time.time() - start_time
        
        if result.returncode != 0:
            log_event(
                level="ERROR",
                logger="app.services.audio_service",
                function="extract_audio_from_video",
                operation=operation,
                event="external_call_error",
                message="FFmpeg command failed",
                context={
                    "return_code": result.returncode,
                    "stderr": result.stderr[:1000] if result.stderr else None,
                    "duration_seconds": duration
                }
            )
            return False
        
        log_event(
            level="DEBUG",
            logger="app.services.audio_service",
            function="extract_audio_from_video",
            operation=operation,
            event="external_call_complete",
            message="FFmpeg command completed",
            context={
                "return_code": result.returncode,
                "stdout_length": len(result.stdout) if result.stdout else 0,
                "duration_seconds": duration
            }
        )
        
        if not output_path.exists():
            log_event(
                level="ERROR",
                logger="app.services.audio_service",
                function="extract_audio_from_video",
                operation=operation,
                event="file_operation_error",
                message="Audio file was not created",
                context={"output_path": str(output_path)}
            )
            return False
        
        # Get file size
        file_size = output_path.stat().st_size
        
        log_operation_complete(
            logger="app.services.audio_service",
            function="extract_audio_from_video",
            operation=operation,
            message="Successfully extracted audio",
            context={
                "video_path": str(video_path),
                "output_path": str(output_path),
                "file_size_bytes": file_size,
                "duration_seconds": duration
            }
        )
        
        return True
        
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.audio_service",
            function="extract_audio_from_video",
            operation=operation,
            error=Exception("Audio extraction timed out"),
            message="Audio extraction timed out",
            context={
                "video_path": str(video_path),
                "timeout_seconds": 3600,
                "duration_seconds": duration
            }
        )
        return False
    except FileNotFoundError:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.audio_service",
            function="extract_audio_from_video",
            operation=operation,
            error=FileNotFoundError("FFmpeg not found"),
            message="FFmpeg not found",
            context={"duration_seconds": duration}
        )
        return False
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.audio_service",
            function="extract_audio_from_video",
            operation=operation,
            error=e,
            message="Error extracting audio",
            context={
                "video_path": str(video_path),
                "output_path": str(output_path),
                "duration_seconds": duration
            }
        )
        return False


# Job management functions now handled by JobRepository


def get_processing_jobs() -> Dict[str, List[Dict]]:
    """
    Get all active processing jobs.
    
    Returns:
        Dictionary with 'active_jobs' count and 'jobs' list
    """
    with _job_lock:
        # Clean up completed/failed jobs older than 30 seconds
        current_time = time.time()
        jobs_to_remove = []
        
        for video_id, job_info in _processing_jobs.items():
            if job_info["status"] != "processing":
                # Remove completed/failed jobs after 30 seconds
                if current_time - job_info["started_at"] > 30:
                    jobs_to_remove.append(video_id)
        
        for video_id in jobs_to_remove:
            del _processing_jobs[video_id]
        
        # Get active processing jobs
        active_jobs = [
            {
                "video_id": video_id,
                "status": job_info["status"],
                "video_filename": job_info.get("video_filename", "")
            }
            for video_id, job_info in _processing_jobs.items()
            if job_info["status"] == "processing"
        ]
        
        return {
            "active_jobs": len(active_jobs),
            "jobs": active_jobs
        }


def process_audio_async(video_id: str, video_path: Path) -> None:
    """
    Process audio extraction asynchronously in a background thread.
    
    Args:
        video_id: ID of the video (filename stem)
        video_path: Path to the video file
    """
    operation = "audio_processing_async"
    
    log_event(
        level="INFO",
        logger="app.services.audio_service",
        function="process_audio_async",
        operation=operation,
        event="operation_start",
        message="Starting async audio processing thread",
        context={
            "video_id": video_id,
            "video_path": str(video_path),
            "request_id": get_request_id()
        }
    )
    
    import asyncio
    from app.services import job_tracker
    
    def extract():
        try:
            output_path = get_audio_path(video_path.stem)
            
            log_event(
                level="DEBUG",
                logger="app.services.audio_service",
                function="process_audio_async",
                operation=operation,
                event="operation_start",
                message="Starting audio extraction in background thread",
                context={
                    "video_id": video_id,
                    "output_path": str(output_path)
                }
            )
            
            # Extract audio
            success = extract_audio_from_video(video_path, output_path)
            
            # Mark job as complete
            if success:
                asyncio.run(job_tracker.complete_job("audio_extraction", video_id))
            else:
                asyncio.run(job_tracker.fail_job("audio_extraction", video_id, "Audio extraction failed"))
            
            log_event(
                level="INFO",
                logger="app.services.audio_service",
                function="process_audio_async",
                operation=operation,
                event="operation_complete",
                message=f"Audio processing {'completed successfully' if success else 'failed'}",
                context={"video_id": video_id, "success": success}
            )
            
        except Exception as e:
            log_operation_error(
                logger="app.services.audio_service",
                function="process_audio_async",
                operation=operation,
                error=e,
                message="Error in async audio processing",
                context={"video_id": video_id}
            )
            asyncio.run(job_tracker.fail_job("audio_extraction", video_id, str(e)))
    
    # Start processing in background thread
    thread = threading.Thread(target=extract, daemon=True)
    thread.start()
    
    log_event(
        level="DEBUG",
        logger="app.services.audio_service",
        function="process_audio_async",
        operation=operation,
        event="operation_complete",
        message="Background thread started",
        context={"video_id": video_id, "thread_name": thread.name}
    )


