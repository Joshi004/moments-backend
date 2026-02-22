import asyncio
import logging
from pathlib import Path


def get_temp_video_path(identifier: str) -> Path:
    """
    Get the path to a video in the managed temp directory.

    Args:
        identifier: Video identifier (e.g., 'motivation')

    Returns:
        Path object for temp/videos/{identifier}/{identifier}.mp4

    Note:
        This function ensures the parent directory exists but does not
        verify the file itself exists. Use for constructing paths before
        downloading or when you want to check if a temp file exists.
    """
    from app.services.temp_file_manager import get_temp_file_path
    return get_temp_file_path("videos", identifier, f"{identifier}.mp4")


def ensure_local_video(identifier: str, cloud_url: str) -> Path:
    """
    Guarantee that a local copy of the video exists and return its path.

    Fallback chain:
    1. Check temp/videos/{identifier}/{identifier}.mp4 (managed temp directory)
    2. Download from GCS to temp/videos/{identifier}/{identifier}.mp4

    Args:
        identifier: Video identifier (e.g., 'motivation')
        cloud_url: GCS path (e.g., 'gs://bucket/videos/motivation/motivation.mp4')

    Returns:
        Path to a local copy of the video

    Raises:
        Exception: If download from GCS fails

    Note:
        This function is sync and uses asyncio.run() internally to call
        the async GCSDownloader. It's designed to be called from sync
        contexts like FFmpeg processing functions.
    """
    logger = logging.getLogger(__name__)

    # Priority 1: Check managed temp directory
    temp_path = get_temp_video_path(identifier)
    if temp_path.exists():
        logger.info(f"Using cached temp video: {temp_path}")
        return temp_path

    # Priority 2: Download from GCS to temp directory
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
    1. temp/videos/{identifier}/{identifier}.mp4 (managed temp directory)
    2. Download from GCS to temp/videos/{identifier}/{identifier}.mp4

    Args:
        identifier: Video identifier (e.g., 'motivation')
        cloud_url: GCS path stored in the videos table

    Returns:
        Path to a local copy of the video

    Raises:
        Exception: If download from GCS fails
    """
    logger = logging.getLogger(__name__)

    # Priority 1: Check managed temp directory
    temp_path = get_temp_video_path(identifier)
    if temp_path.exists():
        logger.info(f"Using cached temp video: {temp_path}")
        return temp_path

    # Priority 2: Download from GCS to temp directory (async, no asyncio.run())
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
