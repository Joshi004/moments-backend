import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List
import logging
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

logger = logging.getLogger(__name__)


def get_audio_directory() -> Path:
    """Get the path to the audio directory."""
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent
    audio_dir = backend_dir / "static" / "audios"
    audio_dir = audio_dir.resolve()
    
    # Create directory if it doesn't exist
    audio_dir.mkdir(parents=True, exist_ok=True)
    
    return audio_dir


def get_audio_path(video_filename: str) -> Path:
    """Get the path for an audio file based on video filename."""
    audio_dir = get_audio_directory()
    # Replace video extension with .wav
    audio_filename = Path(video_filename).stem + ".wav"
    return audio_dir / audio_filename


def check_audio_exists(video_filename: str) -> bool:
    """Check if audio file exists for a given video filename."""
    audio_path = get_audio_path(video_filename)
    return audio_path.exists() and audio_path.is_file()


def get_audio_url(video_filename: str) -> Optional[str]:
    """
    Get the URL path for an audio file if it exists.
    
    Args:
        video_filename: Name of the video file
    
    Returns:
        URL path to audio file or None if it doesn't exist
    """
    audio_path = get_audio_path(video_filename)
    if audio_path.exists():
        # Return relative URL path
        return f"/static/audios/{audio_path.name}"
    return None


def extract_audio_from_video(video_path: Path, output_path: Path) -> bool:
    """
    Extract audio from a video file and save it as WAV.
    
    Args:
        video_path: Path to the video file
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
            output_path = get_audio_path(video_path.name)
            
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


