import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)

# In-memory job tracking dictionary
# Structure: {video_id: {"status": "processing"|"completed"|"failed", "started_at": timestamp, "video_filename": str}}
_processing_jobs: Dict[str, Dict] = {}
_job_lock = threading.Lock()


def get_audio_directory() -> Path:
    """Get the path to the audio directory."""
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent
    audio_dir = backend_dir / "static" / "audio"
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
        return f"/static/audio/{audio_path.name}"
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
    try:
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # FFmpeg command to extract audio as WAV
        # -vn: disable video
        # -acodec pcm_s16le: PCM 16-bit little-endian (WAV format)
        # -ar 44100: sample rate 44.1 kHz
        # -ac 2: stereo (2 channels)
        cmd = [
            'ffmpeg',
            '-i', str(video_path),
            '-vn',  # No video
            '-acodec', 'pcm_s16le',  # PCM 16-bit little-endian
            '-ar', '44100',  # Sample rate
            '-ac', '2',  # Stereo
            '-y',  # Overwrite output file if it exists
            str(output_path)
        ]
        
        logger.info(f"Extracting audio from {video_path} to {output_path}")
        
        # Run FFmpeg command
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout for very long videos
        )
        
        if result.returncode != 0:
            logger.error(f"FFmpeg failed with return code {result.returncode}")
            logger.error(f"FFmpeg stderr: {result.stderr}")
            return False
        
        if not output_path.exists():
            logger.error(f"Audio file was not created at {output_path}")
            return False
        
        logger.info(f"Successfully extracted audio to {output_path}")
        return True
        
    except subprocess.TimeoutExpired:
        logger.error(f"Audio extraction timed out for {video_path}")
        return False
    except FileNotFoundError:
        logger.error("FFmpeg not found. Please install FFmpeg.")
        return False
    except Exception as e:
        logger.error(f"Error extracting audio from {video_path}: {str(e)}")
        return False


def start_processing_job(video_id: str, video_filename: str) -> bool:
    """
    Register a new processing job.
    
    Args:
        video_id: ID of the video (filename stem)
        video_filename: Full filename of the video
    
    Returns:
        True if job was registered, False if already processing
    """
    with _job_lock:
        if video_id in _processing_jobs:
            return False
        
        _processing_jobs[video_id] = {
            "status": "processing",
            "started_at": time.time(),
            "video_filename": video_filename
        }
        return True


def complete_processing_job(video_id: str, success: bool = True) -> None:
    """
    Mark a processing job as complete.
    
    Args:
        video_id: ID of the video
        success: True if processing succeeded, False otherwise
    """
    with _job_lock:
        if video_id in _processing_jobs:
            _processing_jobs[video_id]["status"] = "completed" if success else "failed"
            # Keep job in dict for a short time, then remove it
            # This allows frontend to detect completion
            # Jobs will be cleaned up after a delay


def is_processing(video_id: str) -> bool:
    """
    Check if a video is currently being processed.
    
    Args:
        video_id: ID of the video
    
    Returns:
        True if processing, False otherwise
    """
    with _job_lock:
        if video_id not in _processing_jobs:
            return False
        status = _processing_jobs[video_id].get("status", "")
        return status == "processing"


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
    def extract():
        try:
            output_path = get_audio_path(video_path.name)
            
            # Extract audio
            success = extract_audio_from_video(video_path, output_path)
            
            # Mark job as complete
            complete_processing_job(video_id, success)
            
            logger.info(f"Audio processing completed for {video_id}: {'success' if success else 'failed'}")
            
        except Exception as e:
            logger.error(f"Error in async audio processing for {video_id}: {str(e)}")
            complete_processing_job(video_id, success=False)
    
    # Start processing in background thread
    thread = threading.Thread(target=extract, daemon=True)
    thread.start()


