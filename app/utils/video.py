import os
import warnings
import asyncio
import logging
from pathlib import Path
from typing import Optional


def get_videos_directory() -> Path:
    """Get the path to the videos directory."""
    # Use __file__ to find the backend directory - this is the most reliable method
    # __file__ is app/utils/video_utils.py when imported
    current_file = Path(__file__).resolve()
    
    # Go up 3 levels: app/utils/video_utils.py -> app/utils -> app -> moments-backend
    backend_dir = current_file.parent.parent.parent
    videos_dir = backend_dir / "static" / "videos"
    
    # Resolve to absolute path and verify it's the correct one
    videos_dir = videos_dir.resolve()
    
    # Ensure we're not accidentally pointing to the root videos directory
    # The correct path should contain 'moments-backend/static/videos'
    videos_str = str(videos_dir)
    if 'moments-backend/static/videos' not in videos_str:
        # Fallback: use absolute path
        videos_dir = Path("/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/static/videos").resolve()
    
    # Final verification
    if not videos_dir.exists():
        raise FileNotFoundError(f"Videos directory not found at: {videos_dir}")
    
    return videos_dir


def get_video_files():
    """
    Get list of video files from the videos directory.
    
    .. deprecated::
        Use video_db_repository.list_all() for database-backed video listing.
        This function will be removed after all phases are complete.
    """
    warnings.warn(
        "get_video_files() is deprecated. Use video_db_repository.list_all() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    videos_dir = get_videos_directory()
    
    # Verify we have the correct directory
    expected_path_ending = 'moments-backend/static/videos'
    actual_path = str(videos_dir)
    if expected_path_ending not in actual_path:
        raise ValueError(f"Videos directory path seems incorrect. Expected path containing '{expected_path_ending}', got '{actual_path}'")
    
    if not videos_dir.exists():
        raise FileNotFoundError(f"Videos directory does not exist: {videos_dir}")
    
    if not videos_dir.is_dir():
        raise NotADirectoryError(f"Videos path is not a directory: {videos_dir}")
    
    video_extensions = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.ogg'}
    video_files = []
    
    try:
        # Use Path.iterdir() - wrap in try-except to catch specific errors
        dir_iterator = videos_dir.iterdir()
        for file_path in dir_iterator:
            try:
                if file_path.is_file() and file_path.suffix.lower() in video_extensions:
                    video_files.append(file_path)
            except (PermissionError, OSError) as e:
                # Skip individual files we can't access
                continue
    except PermissionError as e:
        raise PermissionError(f"Cannot access videos directory: {videos_dir}. Error: {e}. Path: {videos_dir.resolve()}")
    except OSError as e:
        raise OSError(f"OS error accessing videos directory: {videos_dir}. Error: {e}. Path: {videos_dir.resolve()}")
    except Exception as e:
        raise Exception(f"Error reading videos directory {videos_dir}: {type(e).__name__}: {e}. Path: {videos_dir.resolve()}")
    
    return sorted(video_files)


def get_video_by_filename(filename: str):
    """Get a video file by its filename."""
    videos_dir = get_videos_directory()
    video_path = videos_dir / filename
    
    if video_path.exists() and video_path.is_file():
        return video_path
    return None


def get_video_by_id(video_id: str):
    """
    Get a video file by its ID (filename without extension).
    
    Args:
        video_id: Video ID (e.g., 'motivation', 'ProjectUpdateVideo')
    
    Returns:
        Path object if video exists, None otherwise
    
    .. deprecated::
        Use video_db_repository.get_by_identifier() for database-backed video lookup.
        This function will be removed after all phases are complete.
    """
    warnings.warn(
        "get_video_by_id() is deprecated. Use video_db_repository.get_by_identifier() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    # Assume .mp4 extension
    filename = f"{video_id}.mp4"
    return get_video_by_filename(filename)


def get_temp_video_path(identifier: str) -> Path:
    """
    Get the path to a video in the temp processing directory.
    
    Args:
        identifier: Video identifier (e.g., 'motivation')
    
    Returns:
        Path object for temp/processing/{identifier}.mp4
        
    Note:
        This function ensures the parent directory exists but does not
        verify the file itself exists. Use for constructing paths before
        downloading or when you want to check if a temp file exists.
    """
    from app.core.config import get_settings
    
    settings = get_settings()
    temp_dir = settings.temp_processing_dir
    
    # Ensure the temp processing directory exists
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    return temp_dir / f"{identifier}.mp4"


def ensure_local_video(identifier: str, cloud_url: str) -> Path:
    """
    Guarantee that a local copy of the video exists and return its path.
    
    This function implements a fallback chain:
    1. Check static/videos/{identifier}.mp4 (original local file)
    2. Check temp/processing/{identifier}.mp4 (previously downloaded temp copy)
    3. Download from GCS to temp/processing/{identifier}.mp4
    
    Args:
        identifier: Video identifier (e.g., 'motivation')
        cloud_url: GCS path (e.g., 'gs://bucket/videos/motivation/motivation.mp4')
    
    Returns:
        Path to a local copy of the video (either original or temp)
        
    Raises:
        Exception: If download from GCS fails
        
    Note:
        This function is sync and uses asyncio.run() internally to call
        the async GCSDownloader. It's designed to be called from sync
        contexts like FFmpeg processing functions.
    """
    logger = logging.getLogger(__name__)
    
    # Priority 1: Check original local file in static/videos/
    original_path = get_video_by_filename(f"{identifier}.mp4")
    if original_path and original_path.exists():
        logger.info(f"Using original local video: {original_path}")
        return original_path
    
    # Priority 2: Check temp processing directory
    temp_path = get_temp_video_path(identifier)
    if temp_path.exists():
        logger.info(f"Using cached temp video: {temp_path}")
        return temp_path
    
    # Priority 3: Download from GCS to temp directory
    logger.info(f"Video not found locally, downloading from GCS: {cloud_url}")
    
    async def download_video():
        """Async helper to download video from GCS."""
        from app.services.gcs_downloader import GCSDownloader
        
        downloader = GCSDownloader()
        success = await downloader.download(
            url=cloud_url,
            dest_path=temp_path,
            video_id=identifier,
            progress_callback=None
        )
        
        if not success:
            raise Exception(f"Failed to download video from GCS: {cloud_url}")
        
        return temp_path
    
    # Run async download in sync context
    try:
        result_path = asyncio.run(download_video())
        logger.info(f"Successfully downloaded video to temp: {result_path}")
        return result_path
    except Exception as e:
        logger.error(f"Failed to download video {identifier} from GCS: {e}")
        raise


async def ensure_local_video_async(identifier: str, cloud_url: str) -> Path:
    """
    Async version of ensure_local_video().

    Guarantees that a local copy of the video exists and returns its path.
    Safe to call from async FastAPI endpoints (does not use asyncio.run()).

    Fallback chain:
    1. static/videos/{identifier}.mp4 (original local file)
    2. temp/processing/{identifier}.mp4 (previously downloaded temp copy)
    3. Download from GCS to temp/processing/{identifier}.mp4

    Args:
        identifier: Video identifier (e.g., 'motivation')
        cloud_url: GCS path stored in the videos table

    Returns:
        Path to a local copy of the video

    Raises:
        Exception: If download from GCS fails
    """
    logger = logging.getLogger(__name__)

    # Priority 1: Check original local file in static/videos/
    try:
        original_path = get_video_by_filename(f"{identifier}.mp4")
        if original_path and original_path.exists():
            logger.info(f"Using original local video: {original_path}")
            return original_path
    except Exception:
        pass

    # Priority 2: Check temp processing directory
    temp_path = get_temp_video_path(identifier)
    if temp_path.exists():
        logger.info(f"Using cached temp video: {temp_path}")
        return temp_path

    # Priority 3: Download from GCS to temp directory (async, no asyncio.run())
    logger.info(f"Video not found locally, downloading from GCS: {cloud_url}")
    from app.services.gcs_downloader import GCSDownloader

    downloader = GCSDownloader()
    success = await downloader.download(
        url=cloud_url,
        dest_path=temp_path,
        video_id=identifier,
        progress_callback=None,
    )

    if not success:
        raise Exception(f"Failed to download video from GCS: {cloud_url}")

    logger.info(f"Successfully downloaded video to temp: {temp_path}")
    return temp_path

