"""
Thumbnail database repository - CRUD operations for the thumbnails table.
Follows the same module-level async function pattern as other db repositories.
"""
import logging
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.thumbnail import Thumbnail
from app.database.models.video import Video

logger = logging.getLogger(__name__)


async def create_for_video(
    session: AsyncSession,
    video_id: int,
    cloud_url: str,
    file_size_kb: Optional[int] = None,
) -> Thumbnail:
    """
    Insert a thumbnail row for a video (clip_id is NULL).

    Returns the Thumbnail instance with its auto-generated id populated.
    """
    thumbnail = Thumbnail(
        video_id=video_id,
        clip_id=None,
        cloud_url=cloud_url,
        file_size_kb=file_size_kb,
    )
    session.add(thumbnail)
    await session.flush()
    await session.refresh(thumbnail)
    logger.info(f"Created thumbnail DB record for video_id={video_id}: {cloud_url}")
    return thumbnail


async def create_for_clip(
    session: AsyncSession,
    clip_id: int,
    cloud_url: str,
    file_size_kb: Optional[int] = None,
) -> Thumbnail:
    """
    Insert a thumbnail row for a clip (video_id is NULL).

    Returns the Thumbnail instance with its auto-generated id populated.
    """
    thumbnail = Thumbnail(
        video_id=None,
        clip_id=clip_id,
        cloud_url=cloud_url,
        file_size_kb=file_size_kb,
    )
    session.add(thumbnail)
    await session.flush()
    await session.refresh(thumbnail)
    logger.info(f"Created thumbnail DB record for clip_id={clip_id}: {cloud_url}")
    return thumbnail


async def get_by_video_id(session: AsyncSession, video_id: int) -> Optional[Thumbnail]:
    """Look up the thumbnail for a video by its numeric database ID."""
    stmt = select(Thumbnail).where(Thumbnail.video_id == video_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_video_identifier(
    session: AsyncSession, video_identifier: str
) -> Optional[Thumbnail]:
    """
    Look up the thumbnail for a video by the video's string identifier.
    Joins with the videos table internally.

    Returns Thumbnail or None. The partial unique index guarantees at most one result.
    """
    stmt = (
        select(Thumbnail)
        .join(Video, Thumbnail.video_id == Video.id)
        .where(Video.identifier == video_identifier)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_clip_id(session: AsyncSession, clip_id: int) -> Optional[Thumbnail]:
    """Look up the thumbnail for a clip by its numeric database ID."""
    stmt = select(Thumbnail).where(Thumbnail.clip_id == clip_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def exists_for_video(session: AsyncSession, video_id: int) -> bool:
    """Return True if a thumbnail record exists for this video_id, False otherwise."""
    stmt = select(Thumbnail.id).where(Thumbnail.video_id == video_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def exists_for_clip(session: AsyncSession, clip_id: int) -> bool:
    """Return True if a thumbnail record exists for this clip_id, False otherwise."""
    stmt = select(Thumbnail.id).where(Thumbnail.clip_id == clip_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def delete_by_video_id(session: AsyncSession, video_id: int) -> bool:
    """Delete the thumbnail for a specific video. Returns True if deleted, False if not found."""
    stmt = delete(Thumbnail).where(Thumbnail.video_id == video_id)
    result = await session.execute(stmt)
    deleted = result.rowcount > 0
    if deleted:
        logger.info(f"Deleted thumbnail DB record for video_id={video_id}")
    return deleted


async def delete_by_clip_id(session: AsyncSession, clip_id: int) -> bool:
    """Delete the thumbnail for a specific clip. Returns True if deleted, False if not found."""
    stmt = delete(Thumbnail).where(Thumbnail.clip_id == clip_id)
    result = await session.execute(stmt)
    deleted = result.rowcount > 0
    if deleted:
        logger.info(f"Deleted thumbnail DB record for clip_id={clip_id}")
    return deleted
