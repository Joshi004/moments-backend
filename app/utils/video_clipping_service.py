import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging
import cv2
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)
from app.utils.timestamp_utils import calculate_padded_boundaries

logger = logging.getLogger(__name__)

# In-memory job tracking dictionary
# Structure: {video_id: {"status": "processing"|"completed"|"failed", "started_at": timestamp, "total_moments": int, "processed_moments": int, "failed_moments": int}}
_clip_extraction_jobs: Dict[str, Dict] = {}
_job_lock = threading.Lock()


def get_moment_clips_directory() -> Path:
    """Get the path to the moment clips directory."""
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent
    clips_dir = backend_dir / "static" / "moment_clips"
    clips_dir = clips_dir.resolve()
    
    # Create directory if it doesn't exist
    clips_dir.mkdir(parents=True, exist_ok=True)
    
    return clips_dir


def get_clip_path(moment_id: str, video_filename: str) -> Path:
    """
    Get the path for a clip file based on moment ID and video filename.
    
    Args:
        moment_id: Unique identifier for the moment
        video_filename: Original video filename (e.g., "ProjectUpdateVideo.mp4")
    
    Returns:
        Path object for the clip file
    """
    clips_dir = get_moment_clips_directory()
    # Format: {video_stem}_{moment_id}_clip.mp4
    video_stem = Path(video_filename).stem
    clip_filename = f"{video_stem}_{moment_id}_clip.mp4"
    return clips_dir / clip_filename


def check_clip_exists(moment_id: str, video_filename: str) -> bool:
    """Check if a clip file exists for a given moment."""
    clip_path = get_clip_path(moment_id, video_filename)
    return clip_path.exists() and clip_path.is_file()


def get_clip_url(moment_id: str, video_filename: str) -> Optional[str]:
    """
    Get the URL for accessing a clip file.
    
    Returns:
        URL string if clip exists, None otherwise
    """
    if check_clip_exists(moment_id, video_filename):
        video_stem = Path(video_filename).stem
        clip_filename = f"{video_stem}_{moment_id}_clip.mp4"
        return f"/static/moment_clips/{clip_filename}"
    return None


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using OpenCV."""
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0.0
        cap.release()
        return duration
    except Exception as e:
        logger.error(f"Error getting video duration: {e}")
        return 0.0


def extract_video_clip(
    video_path: Path,
    moment_id: str,
    start_time: float,
    end_time: float,
    video_filename: str
) -> Optional[Path]:
    """
    Extract a video clip from a larger video file using FFmpeg.
    
    Args:
        video_path: Path to the source video file
        moment_id: Unique identifier for the moment
        start_time: Start timestamp in seconds
        end_time: End timestamp in seconds
        video_filename: Original video filename (for naming)
    
    Returns:
        Path to the extracted clip if successful, None otherwise
    """
    operation = log_operation_start(
        logger="app.utils.video_clipping_service",
        function="extract_video_clip",
        operation="video_clip_extraction",
        context={
            "video_path": str(video_path),
            "moment_id": moment_id,
            "start_time": start_time,
            "end_time": end_time,
            "video_filename": video_filename
        }
    )
    
    try:
        # Validate inputs
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        if start_time < 0:
            raise ValueError(f"Start time cannot be negative: {start_time}")
        
        if end_time <= start_time:
            raise ValueError(f"End time ({end_time}) must be greater than start time ({start_time})")
        
        # Get output path
        output_path = get_clip_path(moment_id, video_filename)
        
        log_event(
            level="DEBUG",
            logger="app.utils.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            event="file_operation_start",
            message="Ensuring output directory exists",
            context={"output_dir": str(output_path.parent)}
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Calculate duration
        duration = end_time - start_time
        
        # FFmpeg command to extract video clip
        # -ss: start time (before input for faster seeking)
        # -i: input file
        # -t: duration
        # -c copy: copy codec (fast, no re-encoding)
        # -avoid_negative_ts make_zero: handle timestamp issues
        # -y: overwrite output file if exists
        cmd = [
            'ffmpeg',
            '-ss', str(start_time),
            '-i', str(video_path),
            '-t', str(duration),
            '-c', 'copy',
            '-avoid_negative_ts', 'make_zero',
            '-y',
            str(output_path)
        ]
        
        log_event(
            level="INFO",
            logger="app.utils.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            event="external_call_start",
            message="Executing FFmpeg command",
            context={
                "command": " ".join(cmd),
                "video_path": str(video_path),
                "output_path": str(output_path),
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration
            }
        )
        
        # Run FFmpeg command
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes timeout
        )
        
        if result.returncode != 0:
            log_operation_error(
                logger="app.utils.video_clipping_service",
                function="extract_video_clip",
                operation=operation,
                error=RuntimeError(f"FFmpeg command failed with return code {result.returncode}"),
                message="FFmpeg command failed",
                context={
                    "return_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr
                }
            )
            return None
        
        # Verify output file was created
        if not output_path.exists():
            log_operation_error(
                logger="app.utils.video_clipping_service",
                function="extract_video_clip",
                operation=operation,
                error=FileNotFoundError(f"Output file was not created: {output_path}"),
                message="Output file not created",
                context={"expected_path": str(output_path)}
            )
            return None
        
        log_event(
            level="INFO",
            logger="app.utils.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            event="external_call_complete",
            message="FFmpeg command completed",
            context={
                "output_path": str(output_path),
                "output_size_bytes": output_path.stat().st_size
            }
        )
        
        log_operation_complete(
            logger="app.utils.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            message="Video clip extracted successfully",
            context={
                "output_path": str(output_path),
                "moment_id": moment_id
            }
        )
        
        return output_path
        
    except FileNotFoundError as e:
        log_operation_error(
            logger="app.utils.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            error=FileNotFoundError("FFmpeg not found"),
            message="FFmpeg not found",
            context={"error": str(e)}
        )
        return None
        
    except Exception as e:
        log_operation_error(
            logger="app.utils.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            error=e,
            message="Unexpected error during video clip extraction",
            context={"error_type": type(e).__name__}
        )
        return None


def start_clip_extraction_job(video_id: str) -> bool:
    """
    Register a clip extraction job for a video.
    
    Args:
        video_id: ID of the video
        
    Returns:
        True if job was registered, False if already processing
    """
    with _job_lock:
        if video_id in _clip_extraction_jobs:
            return False
        _clip_extraction_jobs[video_id] = {
            "status": "processing",
            "started_at": time.time(),
            "total_moments": 0,
            "processed_moments": 0,
            "failed_moments": 0
        }
        return True


def is_extracting_clips(video_id: str) -> bool:
    """Check if clip extraction is in progress for a video."""
    with _job_lock:
        if video_id not in _clip_extraction_jobs:
            return False
        status = _clip_extraction_jobs[video_id]["status"]
        return status == "processing"


def get_clip_extraction_status(video_id: str) -> Optional[Dict]:
    """Get clip extraction status for a video."""
    with _job_lock:
        if video_id not in _clip_extraction_jobs:
            return None
        return _clip_extraction_jobs[video_id].copy()


def update_clip_extraction_progress(video_id: str, total: int, processed: int, failed: int):
    """Update clip extraction progress."""
    with _job_lock:
        if video_id in _clip_extraction_jobs:
            _clip_extraction_jobs[video_id]["total_moments"] = total
            _clip_extraction_jobs[video_id]["processed_moments"] = processed
            _clip_extraction_jobs[video_id]["failed_moments"] = failed


def complete_clip_extraction_job(video_id: str, success: bool):
    """Mark clip extraction job as completed or failed."""
    with _job_lock:
        if video_id in _clip_extraction_jobs:
            _clip_extraction_jobs[video_id]["status"] = "completed" if success else "failed"
            _clip_extraction_jobs[video_id]["completed_at"] = time.time()


def extract_clips_for_video(
    video_id: str,
    video_path: Path,
    video_filename: str,
    moments: List[Dict],
    override_existing: bool = True
) -> Dict:
    """
    Extract video clips for all original moments in a video.
    
    Args:
        video_id: Video ID
        video_path: Path to the source video file
        video_filename: Original video filename
        moments: List of moment objects
        override_existing: Whether to override existing clips
    
    Returns:
        Dictionary with extraction results
    """
    operation = log_operation_start(
        logger="app.utils.video_clipping_service",
        function="extract_clips_for_video",
        operation="batch_clip_extraction",
        context={
            "video_id": video_id,
            "video_filename": video_filename,
            "num_moments": len(moments),
            "override_existing": override_existing
        }
    )
    
    try:
        # Get clipping configuration from backend
        from app.utils.model_config import get_clipping_config
        clipping_config = get_clipping_config()
        padding = clipping_config['padding']
        margin = clipping_config['margin']
        
        # Load transcript for word-level timestamp alignment
        from app.utils.transcript_service import load_transcript
        audio_filename = video_filename.rsplit('.', 1)[0] + ".wav"
        transcript_data = load_transcript(audio_filename)
        
        if transcript_data is None or 'word_timestamps' not in transcript_data:
            log_event(
                level="WARNING",
                logger="app.utils.video_clipping_service",
                function="extract_clips_for_video",
                operation=operation,
                event="validation_warning",
                message="Transcript not available, using simple padding without word alignment",
                context={"audio_filename": audio_filename}
            )
            word_timestamps = None
        else:
            word_timestamps = transcript_data['word_timestamps']
            logger.info(f"Loaded transcript with {len(word_timestamps)} words for precise clipping")
        
        # Get video duration for boundary checks
        video_duration = get_video_duration(video_path)
        if video_duration <= 0:
            raise ValueError(f"Could not determine video duration for {video_filename}")
        
        log_event(
            level="INFO",
            logger="app.utils.video_clipping_service",
            function="extract_clips_for_video",
            operation=operation,
            event="operation_start",
            message="Starting batch clip extraction",
            context={
                "video_duration": video_duration,
                "total_moments": len(moments),
                "padding": padding,
                "has_transcript": word_timestamps is not None
            }
        )
        
        # Filter to only original moments (not refined)
        original_moments = [m for m in moments if not m.get('is_refined', False)]
        
        log_event(
            level="INFO",
            logger="app.utils.video_clipping_service",
            function="extract_clips_for_video",
            operation=operation,
            event="validation_complete",
            message="Filtered to original moments only",
            context={
                "total_moments": len(moments),
                "original_moments": len(original_moments)
            }
        )
        
        results = {
            "total": len(original_moments),
            "successful": 0,
            "skipped": 0,
            "failed": 0,
            "clips": []
        }
        
        # Update job with total count
        update_clip_extraction_progress(video_id, len(original_moments), 0, 0)
        
        for idx, moment in enumerate(original_moments):
            moment_id = moment.get('id')
            original_start = moment.get('start_time')
            original_end = moment.get('end_time')
            
            if not all([moment_id, original_start is not None, original_end is not None]):
                log_event(
                    level="WARNING",
                    logger="app.utils.video_clipping_service",
                    function="extract_clips_for_video",
                    operation=operation,
                    event="validation_failed",
                    message="Skipping moment due to missing data",
                    context={"moment": moment}
                )
                results["failed"] += 1
                continue
            
            # Check if clip already exists
            if not override_existing and check_clip_exists(moment_id, video_filename):
                log_event(
                    level="INFO",
                    logger="app.utils.video_clipping_service",
                    function="extract_clips_for_video",
                    operation=operation,
                    event="clip_skipped",
                    message="Skipping existing clip",
                    context={"moment_id": moment_id}
                )
                results["skipped"] += 1
                results["clips"].append({
                    "moment_id": moment_id,
                    "status": "skipped",
                    "clip_url": get_clip_url(moment_id, video_filename)
                })
                continue
            
            # Calculate precise clip boundaries using word timestamps
            if word_timestamps:
                try:
                    clip_start, clip_end = calculate_padded_boundaries(
                        word_timestamps=word_timestamps,
                        moment_start=original_start,
                        moment_end=original_end,
                        padding=padding,
                        margin=margin
                    )
                except Exception as e:
                    logger.warning(f"Error calculating word-aligned boundaries for moment {moment_id}: {e}. Falling back to simple padding.")
                    clip_start = max(0, original_start - padding)
                    clip_end = min(video_duration, original_end + padding)
            else:
                # Fallback to simple padding if no transcript
                clip_start = max(0, original_start - padding)
                clip_end = min(video_duration, original_end + padding)
            
            # Ensure boundaries are within video duration
            clip_start = max(0, clip_start)
            clip_end = min(video_duration, clip_end)
            
            log_event(
                level="DEBUG",
                logger="app.utils.video_clipping_service",
                function="extract_clips_for_video",
                operation=operation,
                event="clip_extraction_start",
                message=f"Extracting clip {idx + 1}/{len(original_moments)}",
                context={
                    "moment_id": moment_id,
                    "original_start": original_start,
                    "original_end": original_end,
                    "clip_start": clip_start,
                    "clip_end": clip_end,
                    "word_aligned": word_timestamps is not None
                }
            )
            
            # Extract clip
            clip_path = extract_video_clip(
                video_path=video_path,
                moment_id=moment_id,
                start_time=clip_start,
                end_time=clip_end,
                video_filename=video_filename
            )
            
            if clip_path:
                results["successful"] += 1
                results["clips"].append({
                    "moment_id": moment_id,
                    "status": "success",
                    "clip_url": get_clip_url(moment_id, video_filename),
                    "clip_path": str(clip_path),
                    "clip_start": clip_start,
                    "clip_end": clip_end
                })
            else:
                results["failed"] += 1
                results["clips"].append({
                    "moment_id": moment_id,
                    "status": "failed"
                })
            
            # Update progress
            processed = results["successful"] + results["skipped"] + results["failed"]
            update_clip_extraction_progress(video_id, len(original_moments), processed, results["failed"])
        
        log_operation_complete(
            logger="app.utils.video_clipping_service",
            function="extract_clips_for_video",
            operation=operation,
            message="Batch clip extraction completed",
            context={
                "total_moments": results["total"],
                "successful_clips": results["successful"],
                "skipped_clips": results["skipped"],
                "failed_clips": results["failed"]
            }
        )
        
        return results
        
    except Exception as e:
        log_operation_error(
            logger="app.utils.video_clipping_service",
            function="extract_clips_for_video",
            operation=operation,
            error=e,
            message="Batch clip extraction failed"
        )
        return {
            "total": 0,
            "successful": 0,
            "skipped": 0,
            "failed": 0,
            "clips": [],
            "error": str(e)
        }


def process_clip_extraction_async(
    video_id: str,
    video_path: Path,
    video_filename: str,
    moments: List[Dict],
    override_existing: bool = True
):
    """
    Process clip extraction asynchronously in a background thread.
    
    Args:
        video_id: Video ID
        video_path: Path to the source video file
        video_filename: Original video filename
        moments: List of moment objects
        override_existing: Whether to override existing clips
    """
    def extraction_worker():
        try:
            results = extract_clips_for_video(
                video_id=video_id,
                video_path=video_path,
                video_filename=video_filename,
                moments=moments,
                override_existing=override_existing
            )
            
            # Mark as completed
            success = results.get("failed", 0) < results.get("total", 1)
            complete_clip_extraction_job(video_id, success)
            
        except Exception as e:
            logger.error(f"Error in clip extraction worker: {e}")
            complete_clip_extraction_job(video_id, False)
    
    # Start extraction in background thread
    thread = threading.Thread(target=extraction_worker, daemon=True)
    thread.start()

