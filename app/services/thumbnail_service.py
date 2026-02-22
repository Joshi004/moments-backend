"""
Thumbnail service - generates, uploads, and serves video thumbnails.

After Phase 8, thumbnails are:
- Generated on-demand into a temp directory (temp/processing/thumbnails/)
- Uploaded to GCS at thumbnails/video/{identifier}.jpg
- Tracked in the PostgreSQL thumbnails table
- Served via GCS signed URL redirects through the API endpoint

Local static/thumbnails/ files are kept as backup but are no longer written to
or served by the application.
"""
import asyncio
import os
import cv2
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def get_thumbnails_temp_directory(video_identifier: str = "") -> Path:
    """
    Get (and create) the temp directory used for thumbnail generation.

    Args:
        video_identifier: When provided, returns the per-video subdirectory
                          temp/thumbnails/{identifier}/. When empty, returns
                          the thumbnails root temp directory.

    Returns:
        Path to temp/thumbnails/{video_identifier}/ (created if absent)
    """
    from app.services.temp_file_manager import get_temp_dir, _get_temp_base
    if video_identifier:
        return get_temp_dir("thumbnails", video_identifier)
    base = _get_temp_base() / "thumbnails"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_thumbnail_temp_path(video_identifier: str) -> Path:
    """Return the transient temp path for a video's thumbnail JPEG."""
    from app.services.temp_file_manager import get_temp_file_path
    return get_temp_file_path("thumbnails", video_identifier, f"{video_identifier}.jpg")


# ---------------------------------------------------------------------------
# Frame extraction (unchanged OpenCV logic)
# ---------------------------------------------------------------------------

def extract_frame_from_video(
    video_path: Path,
    output_path: Path,
    frame_time_seconds: Optional[float] = None,
) -> bool:
    """
    Extract a single frame from a video and save it as a JPEG thumbnail.

    This function is synchronous (OpenCV is CPU-bound). When called from
    async code, wrap it with asyncio.to_thread().

    Args:
        video_path: Path to the video file (must exist locally)
        output_path: Path where the JPEG thumbnail should be saved
        frame_time_seconds: Time offset to seek to. If None, uses 10% of
                            duration or 1 second, whichever is smaller.

    Returns:
        True if successful, False otherwise
    """
    try:
        cap = cv2.VideoCapture(str(video_path))

        if not cap.isOpened():
            logger.error(f"Could not open video file: {video_path}")
            return False

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0

        if frame_time_seconds is None:
            frame_time = min(duration * 0.1, 1.0) if duration > 0 else 1.0
        else:
            frame_time = frame_time_seconds

        frame_number = int(frame_time * fps) if fps > 0 else 0
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            logger.error(f"Could not read frame from video: {video_path}")
            return False

        frame_resized = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
        success = cv2.imwrite(str(output_path), frame_resized, [cv2.IMWRITE_JPEG_QUALITY, 85])

        if not success:
            logger.error(f"Could not save thumbnail to: {output_path}")
            return False

        logger.info(f"Extracted frame to thumbnail: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error generating thumbnail for {video_path}: {e}")
        return False


# ---------------------------------------------------------------------------
# Async thumbnail URL (DB-backed)
# ---------------------------------------------------------------------------

async def get_thumbnail_url_async(
    video_identifier: str,
    session: AsyncSession,
) -> Optional[str]:
    """
    Return the API endpoint URL for a video's thumbnail if one exists in the DB.

    The returned URL is the API endpoint (/api/videos/{identifier}/thumbnail),
    not a direct GCS signed URL. The endpoint generates a fresh signed URL
    and issues a 302 redirect on each access.

    Returns None if no thumbnail record exists for the video.
    """
    from app.repositories import thumbnail_db_repository

    thumbnail = await thumbnail_db_repository.get_by_video_identifier(session, video_identifier)
    if thumbnail:
        return f"/api/videos/{video_identifier}/thumbnail"
    return None


# ---------------------------------------------------------------------------
# Async thumbnail generation (GCS + DB)
# ---------------------------------------------------------------------------

async def generate_thumbnail_async(
    video_identifier: str,
    session: AsyncSession,
    frame_time_seconds: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate a thumbnail for a video, upload it to GCS, and record it in the DB.

    Flow:
    1. Check DB — skip if thumbnail already exists (return existing record info)
    2. Look up video in DB to get cloud_url
    3. Ensure local video copy (downloads from GCS if needed)
    4. Extract frame with OpenCV (run in thread to avoid blocking the event loop)
    5. Upload temp JPEG to GCS
    6. Insert DB record
    7. Delete temp JPEG
    8. Return dict with cloud_url and signed_url

    Args:
        video_identifier: Video identifier (e.g., "motivation")
        session: Async database session
        frame_time_seconds: Optional specific frame time; uses default strategy if None

    Returns:
        Dict with keys: cloud_url, signed_url, video_identifier, already_existed
        Returns None if the video is not found in the database.
    """
    from app.repositories import thumbnail_db_repository, video_db_repository
    from app.services.pipeline.upload_service import GCSUploader
    from app.utils.video import ensure_local_video_async

    # Step 1: Check if thumbnail already exists
    existing = await thumbnail_db_repository.get_by_video_identifier(session, video_identifier)
    if existing:
        logger.info(f"Thumbnail already exists in DB for video: {video_identifier}")
        uploader = GCSUploader()
        signed_url = uploader.get_thumbnail_signed_url(existing.cloud_url)
        return {
            "cloud_url": existing.cloud_url,
            "signed_url": signed_url,
            "video_identifier": video_identifier,
            "already_existed": True,
        }

    # Step 2: Look up the video
    video = await video_db_repository.get_by_identifier(session, video_identifier)
    if not video:
        logger.warning(f"Video not found in DB: {video_identifier}")
        return None

    if not video.cloud_url:
        logger.error(f"Video has no cloud_url: {video_identifier}")
        return None

    # Step 3: Ensure local video copy
    logger.info(f"Ensuring local video for thumbnail generation: {video_identifier}")
    local_video_path = await ensure_local_video_async(video_identifier, video.cloud_url)

    # Step 4: Extract frame (CPU-bound, run in thread)
    temp_thumbnail_path = get_thumbnail_temp_path(video_identifier)
    success = await asyncio.to_thread(
        extract_frame_from_video,
        local_video_path,
        temp_thumbnail_path,
        frame_time_seconds,
    )

    if not success:
        logger.error(f"Frame extraction failed for video: {video_identifier}")
        return None

    try:
        # Step 5: Upload to GCS
        file_size_bytes = os.path.getsize(temp_thumbnail_path)
        file_size_kb = file_size_bytes // 1024

        uploader = GCSUploader()
        gcs_path, signed_url = await uploader.upload_thumbnail(
            temp_thumbnail_path, "video", video_identifier
        )

        # Step 6: Insert DB record
        thumbnail = await thumbnail_db_repository.create_for_video(
            session,
            video_id=video.id,
            cloud_url=gcs_path,
            file_size_kb=file_size_kb,
        )
        await session.commit()

        logger.info(
            f"Thumbnail generated and uploaded for {video_identifier}: "
            f"gcs_path={gcs_path}, size={file_size_kb}KB"
        )

        return {
            "cloud_url": gcs_path,
            "signed_url": signed_url,
            "video_identifier": video_identifier,
            "already_existed": False,
        }

    finally:
        # Step 7: Always delete temp file, even on error
        if temp_thumbnail_path.exists():
            try:
                temp_thumbnail_path.unlink()
                logger.debug(f"Deleted temp thumbnail: {temp_thumbnail_path}")
            except Exception as e:
                logger.warning(f"Could not delete temp thumbnail {temp_thumbnail_path}: {e}")
