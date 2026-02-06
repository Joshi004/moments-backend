import subprocess
import threading
import time
import platform
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import cv2
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)
from app.utils.timestamp import calculate_padded_boundaries

logger = logging.getLogger(__name__)


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


def delete_all_clips_for_video(video_id: str) -> int:
    """
    Delete all local clip files for a video.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Number of clip files deleted
    """
    clips_dir = get_moment_clips_directory()
    pattern = f"{video_id}_*_clip.mp4"
    clips = list(clips_dir.glob(pattern))
    
    deleted = 0
    for clip_path in clips:
        try:
            clip_path.unlink()
            deleted += 1
            logger.debug(f"Deleted clip: {clip_path.name}")
        except Exception as e:
            logger.error(f"Failed to delete clip {clip_path.name}: {e}")
    
    if deleted > 0:
        logger.info(f"Deleted {deleted} existing clips for {video_id}")
    
    return deleted


def get_clip_url(moment_id: str, video_filename: str) -> Optional[str]:
    """
    Get the URL for accessing a clip file from local backend.
    
    Returns:
        URL string if clip exists, None otherwise
    """
    if check_clip_exists(moment_id, video_filename):
        video_stem = Path(video_filename).stem
        clip_filename = f"{video_stem}_{moment_id}_clip.mp4"
        return f"/static/moment_clips/{clip_filename}"
    return None


def get_clip_gcs_signed_url(moment_id: str, video_filename: str) -> Optional[str]:
    """
    Sync wrapper: Upload clip to GCS if not already uploaded, and return signed URL.
    
    DEPRECATED: Use get_clip_gcs_signed_url_async() in async contexts instead.
    This sync version should only be called from synchronous code.
    
    Args:
        moment_id: Moment identifier
        video_filename: Video filename (e.g., "video123.mp4")
    
    Returns:
        GCS signed URL if clip exists and upload succeeds, None otherwise
    
    Raises:
        RuntimeError: If called from an async context (event loop already running)
    """
    import asyncio
    
    # Check if already in an event loop
    try:
        loop = asyncio.get_running_loop()
        # If we get here, we're in an async context - this is an error
        error_msg = (
            f"Called sync get_clip_gcs_signed_url() from async context for moment {moment_id}. "
            "Use get_clip_gcs_signed_url_async() instead."
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    except RuntimeError as e:
        # Check if it's our error or the "no running loop" error
        if "async context" in str(e):
            raise
        # No running loop - safe to proceed with sync version
        pass
    
    # Safe to call async version using asyncio.run()
    try:
        return asyncio.run(get_clip_gcs_signed_url_async(moment_id, video_filename))
    except Exception as e:
        logger.error(f"Failed to get GCS signed URL for moment {moment_id}: {type(e).__name__}: {e}")
        return None


async def get_clip_gcs_signed_url_async(moment_id: str, video_filename: str) -> Optional[str]:
    """
    Async version: Upload clip to GCS if not already uploaded, and return signed URL.
    Used for AI model refinement with video from async contexts.
    
    This is the preferred version when calling from async contexts (orchestrator, async endpoints).
    It directly awaits the upload instead of creating a new event loop.
    
    Args:
        moment_id: Moment identifier
        video_filename: Video filename (e.g., "video123.mp4")
    
    Returns:
        GCS signed URL if clip exists and upload succeeds, None otherwise
    """
    clip_path = get_clip_path(moment_id, video_filename)
    if not clip_path.exists():
        logger.warning(f"Clip not found for GCS upload: {clip_path}")
        return None
    
    try:
        from app.services.pipeline.upload_service import GCSUploader
        
        video_id = Path(video_filename).stem
        uploader = GCSUploader()
        
        # Directly await since we're in async context
        gcs_path, signed_url = await uploader.upload_clip(clip_path, video_id, moment_id)
        logger.info(f"Generated GCS signed URL for clip: {moment_id}")
        return signed_url
    
    except Exception as e:
        logger.error(f"Failed to upload clip to GCS for moment {moment_id}: {type(e).__name__}: {e}")
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


def get_clip_duration(moment_id: str, video_filename: str) -> Optional[float]:
    """
    Get the duration of a clip file in seconds.
    
    Args:
        moment_id: Unique identifier for the moment
        video_filename: Original video filename (e.g., "ProjectUpdateVideo.mp4")
    
    Returns:
        Duration in seconds if clip exists, None otherwise
    """
    clip_path = get_clip_path(moment_id, video_filename)
    if not clip_path.exists() or not clip_path.is_file():
        logger.debug(f"Clip file not found: {clip_path}")
        return None
    
    try:
        cap = cv2.VideoCapture(str(clip_path))
        if not cap.isOpened():
            logger.error(f"Could not open clip file: {clip_path}")
            return None
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0.0
        cap.release()
        
        logger.debug(f"Clip duration for {moment_id}: {duration:.2f}s (fps={fps}, frames={frame_count})")
        return duration
    except Exception as e:
        logger.error(f"Error getting clip duration for {moment_id}: {e}")
        return None


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
        logger="app.services.video_clipping_service",
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
            logger="app.services.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            event="file_operation_start",
            message="Ensuring output directory exists",
            context={"output_dir": str(output_path.parent)}
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Calculate duration
        duration = end_time - start_time
        
        # Get encoding configuration
        from app.utils.model_config import get_encoding_config
        encoding_config = get_encoding_config()
        
        # Detect platform for encoder selection
        is_macos = platform.system() == "Darwin"
        
        # Build FFmpeg command with re-encoding for frame-accurate clipping
        # -ss: start time (before input for faster seeking)
        # -i: input file
        # -t: duration
        # -c:v: video codec (platform-specific hardware/software encoding)
        # -c:a: audio codec (re-encode audio)
        # -avoid_negative_ts make_zero: handle timestamp issues
        # -y: overwrite output file if exists
        
        if is_macos:
            # Use VideoToolbox hardware encoder on macOS
            cmd = [
                'ffmpeg',
                '-ss', str(start_time),
                '-i', str(video_path),
                '-t', str(duration),
                '-c:v', encoding_config['macos_encoder'],
                '-q:v', str(encoding_config['macos_quality']),
                '-c:a', encoding_config['audio_codec'],
                '-b:a', encoding_config['audio_bitrate'],
                '-avoid_negative_ts', 'make_zero',
                '-y',
                str(output_path)
            ]
        else:
            # Use libx264 software encoder on Linux
            cmd = [
                'ffmpeg',
                '-ss', str(start_time),
                '-i', str(video_path),
                '-t', str(duration),
                '-c:v', encoding_config['linux_encoder'],
                '-preset', encoding_config['linux_preset'],
                '-c:a', encoding_config['audio_codec'],
                '-b:a', encoding_config['audio_bitrate'],
                '-avoid_negative_ts', 'make_zero',
                '-y',
                str(output_path)
            ]
        
        log_event(
            level="INFO",
            logger="app.services.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            event="external_call_start",
            message="Executing FFmpeg command with re-encoding",
            context={
                "command": " ".join(cmd),
                "video_path": str(video_path),
                "output_path": str(output_path),
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "platform": "macOS" if is_macos else "Linux",
                "video_encoder": encoding_config['macos_encoder'] if is_macos else encoding_config['linux_encoder'],
                "encoding_mode": "hardware" if is_macos else "software"
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
                logger="app.services.video_clipping_service",
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
                logger="app.services.video_clipping_service",
                function="extract_video_clip",
                operation=operation,
                error=FileNotFoundError(f"Output file was not created: {output_path}"),
                message="Output file not created",
                context={"expected_path": str(output_path)}
            )
            return None
        
        log_event(
            level="INFO",
            logger="app.services.video_clipping_service",
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
            logger="app.services.video_clipping_service",
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
            logger="app.services.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            error=FileNotFoundError("FFmpeg not found"),
            message="FFmpeg not found",
            context={"error": str(e)}
        )
        return None
        
    except Exception as e:
        log_operation_error(
            logger="app.services.video_clipping_service",
            function="extract_video_clip",
            operation=operation,
            error=e,
            message="Unexpected error during video clip extraction",
            context={"error_type": type(e).__name__}
        )
        return None


# Job management functions now handled by JobRepository

def extract_clips_for_video(
    video_id: str,
    video_path: Path,
    video_filename: str,
    moments: List[Dict],
    override_existing: bool = True,
    progress_callback: Optional[callable] = None
) -> Dict:
    """
    Extract video clips for all original moments in a video.
    
    Args:
        video_id: Video ID
        video_path: Path to the source video file
        video_filename: Original video filename
        moments: List of moment objects
        override_existing: Whether to override existing clips
        progress_callback: Optional callback function(total, processed, failed) for progress updates
    
    Returns:
        Dictionary with extraction results
    """
    operation = log_operation_start(
        logger="app.services.video_clipping_service",
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
        from app.services.transcript_service import load_transcript
        audio_filename = video_filename.rsplit('.', 1)[0] + ".wav"
        transcript_data = load_transcript(audio_filename)
        
        if transcript_data is None or 'word_timestamps' not in transcript_data:
            log_event(
                level="WARNING",
                logger="app.services.video_clipping_service",
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
            logger="app.services.video_clipping_service",
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
            logger="app.services.video_clipping_service",
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
        
        # Get number of parallel workers from configuration
        from app.utils.model_config import get_parallel_workers
        max_workers = get_parallel_workers()
        
        log_event(
            level="INFO",
            logger="app.services.video_clipping_service",
            function="extract_clips_for_video",
            operation=operation,
            event="parallel_processing_start",
            message=f"Starting parallel clip extraction with {max_workers} workers",
            context={"max_workers": max_workers, "total_clips": len(original_moments)}
        )
        
        # Helper function to process a single moment
        def process_moment(moment, idx):
            moment_id = moment.get('id')
            original_start = moment.get('start_time')
            original_end = moment.get('end_time')
            
            if not all([moment_id, original_start is not None, original_end is not None]):
                log_event(
                    level="WARNING",
                    logger="app.services.video_clipping_service",
                    function="extract_clips_for_video",
                    operation=operation,
                    event="validation_failed",
                    message="Skipping moment due to missing data",
                    context={"moment": moment}
                )
                return {
                    "moment_id": moment_id,
                    "status": "failed",
                    "reason": "missing_data"
                }
            
            # Check if clip already exists
            if not override_existing and check_clip_exists(moment_id, video_filename):
                log_event(
                    level="INFO",
                    logger="app.services.video_clipping_service",
                    function="extract_clips_for_video",
                    operation=operation,
                    event="clip_skipped",
                    message="Skipping existing clip",
                    context={"moment_id": moment_id}
                )
                return {
                    "moment_id": moment_id,
                    "status": "skipped",
                    "clip_url": get_clip_url(moment_id, video_filename)
                }
            
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
                logger="app.services.video_clipping_service",
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
                return {
                    "moment_id": moment_id,
                    "status": "success",
                    "clip_url": get_clip_url(moment_id, video_filename),
                    "clip_path": str(clip_path),
                    "clip_start": clip_start,
                    "clip_end": clip_end
                }
            else:
                return {
                    "moment_id": moment_id,
                    "status": "failed"
                }
        
        # Process clips in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_moment = {
                executor.submit(process_moment, moment, idx): moment 
                for idx, moment in enumerate(original_moments)
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_moment):
                result = future.result()
                
                # Update results based on status
                if result["status"] == "success":
                    results["successful"] += 1
                elif result["status"] == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
                
                results["clips"].append(result)
                
                # Update progress
                processed = results["successful"] + results["skipped"] + results["failed"]
                if progress_callback:
                    progress_callback(len(original_moments), processed, results["failed"])
        
        log_operation_complete(
            logger="app.services.video_clipping_service",
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
            logger="app.services.video_clipping_service",
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


async def extract_clips_parallel(
    video_path: Path,
    video_filename: str,
    moments: List[Dict],
    override_existing: bool = False,
    progress_callback: Optional[callable] = None
) -> bool:
    """
    Extract clips in parallel (async wrapper for pipeline).
    
    Args:
        video_path: Path to the source video file
        video_filename: Original video filename
        moments: List of moment objects
        override_existing: Whether to override existing clips
        progress_callback: Optional callback function(total, processed, failed) for progress updates
    
    Returns:
        True if successful, False otherwise
    """
    import asyncio
    
    try:
        # Extract video_id from filename
        video_id = Path(video_filename).stem
        
        # Call the synchronous function in a thread pool to avoid blocking the event loop
        results = await asyncio.to_thread(
            extract_clips_for_video,
            video_id=video_id,
            video_path=video_path,
            video_filename=video_filename,
            moments=moments,
            override_existing=override_existing,
            progress_callback=progress_callback
        )
        
        # Return success based on whether we had more successes than failures
        if "error" in results:
            return False
        
        return results.get("failed", 0) == 0 or results.get("successful", 0) > 0
        
    except Exception as e:
        logger.error(f"Error in extract_clips_parallel: {e}")
        return False


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
    import asyncio
    from app.services import job_tracker
    
    def extraction_worker():
        try:
            # Create progress callback that updates job_tracker
            def progress_callback(total: int, processed: int, failed: int):
                asyncio.run(job_tracker.update_progress(
                    "clip_extraction",
                    video_id,
                    total_moments=total,
                    processed_moments=processed,
                    failed_moments=failed
                ))
            
            results = extract_clips_for_video(
                video_id=video_id,
                video_path=video_path,
                video_filename=video_filename,
                moments=moments,
                override_existing=override_existing,
                progress_callback=progress_callback
            )
            
            # Mark as completed
            success = results.get("failed", 0) < results.get("total", 1)
            if success:
                asyncio.run(job_tracker.complete_job(
                    "clip_extraction",
                    video_id,
                    total_moments=results.get("total", 0),
                    processed_moments=results.get("successful", 0) + results.get("skipped", 0),
                    failed_moments=results.get("failed", 0)
                ))
            else:
                asyncio.run(job_tracker.fail_job(
                    "clip_extraction",
                    video_id,
                    "Clip extraction completed with failures"
                ))
            
        except Exception as e:
            logger.error(f"Error in clip extraction worker: {e}")
            asyncio.run(job_tracker.fail_job(
                "clip_extraction",
                video_id,
                str(e)
            ))
    
    # Start extraction in background thread
    thread = threading.Thread(target=extraction_worker, daemon=True)
    thread.start()

