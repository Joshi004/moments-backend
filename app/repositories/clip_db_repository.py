"""
Clip database repository - CRUD operations for the clips table.
Follows the same module-level async function pattern as other db repositories.
"""
import logging
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.clip import Clip
from app.database.models.moment import Moment
from app.database.models.video import Video

logger = logging.getLogger(__name__)


async def create(
    session: AsyncSession,
    moment_id: int,
    video_id: int,
    cloud_url: str,
    start_time: float,
    end_time: float,
    padding_left: float,
    padding_right: float,
    file_size_kb: Optional[int] = None,
    format: Optional[str] = None,
    video_codec: Optional[str] = None,
    audio_codec: Optional[str] = None,
    resolution: Optional[str] = None,
) -> Clip:
    """
    Insert a single clip row.

    Returns the Clip instance with its auto-generated id populated.
    """
    clip = Clip(
        moment_id=moment_id,
        video_id=video_id,
        cloud_url=cloud_url,
        start_time=start_time,
        end_time=end_time,
        padding_left=padding_left,
        padding_right=padding_right,
        file_size_kb=file_size_kb,
        format=format,
        video_codec=video_codec,
        audio_codec=audio_codec,
        resolution=resolution,
    )
    session.add(clip)
    await session.flush()
    await session.refresh(clip)
    return clip


async def get_by_id(session: AsyncSession, clip_id: int) -> Optional[Clip]:
    """Look up a clip by its numeric database ID."""
    stmt = select(Clip).where(Clip.id == clip_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_moment_id(session: AsyncSession, moment_id: int) -> Optional[Clip]:
    """Look up the clip for a specific moment by its numeric DB id. Returns Clip or None."""
    stmt = select(Clip).where(Clip.moment_id == moment_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_moment_identifier(session: AsyncSession, moment_identifier: str) -> Optional[Clip]:
    """
    Look up the clip for a moment by the moment's string identifier.
    Joins with the moments table internally.
    """
    stmt = (
        select(Clip)
        .join(Moment, Clip.moment_id == Moment.id)
        .where(Moment.identifier == moment_identifier)
        .options(selectinload(Clip.moment))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_video_id(session: AsyncSession, video_id: int) -> list[Clip]:
    """Return all clips for a video (by numeric video id), ordered by start_time."""
    stmt = (
        select(Clip)
        .where(Clip.video_id == video_id)
        .options(selectinload(Clip.moment))
        .order_by(Clip.start_time.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_by_video_identifier(session: AsyncSession, video_identifier: str) -> list[Clip]:
    """
    Return all clips for a video (by string identifier), ordered by start_time.
    Joins with the videos table internally.
    """
    stmt = (
        select(Clip)
        .join(Video, Clip.video_id == Video.id)
        .where(Video.identifier == video_identifier)
        .options(selectinload(Clip.moment))
        .order_by(Clip.start_time.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def exists_for_moment(session: AsyncSession, moment_id: int) -> bool:
    """
    Return True if a clip record exists for this moment_id, False otherwise.
    Used as the skip condition during pipeline clip extraction.
    """
    stmt = select(Clip.id).where(Clip.moment_id == moment_id).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def delete_by_moment_id(session: AsyncSession, moment_id: int) -> bool:
    """Delete the clip for a specific moment. Returns True if deleted, False if not found."""
    stmt = delete(Clip).where(Clip.moment_id == moment_id)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def delete_all_for_video(session: AsyncSession, video_id: int) -> int:
    """Delete all clips for a video. Returns the count of deleted rows."""
    stmt = delete(Clip).where(Clip.video_id == video_id)
    result = await session.execute(stmt)
    return result.rowcount


async def bulk_create(session: AsyncSession, clips_data: list[dict]) -> list[Clip]:
    """
    Insert multiple clip rows in a single operation.

    Each dict in clips_data must contain: moment_id, video_id, cloud_url,
    start_time, end_time, padding_left, padding_right.
    Optional: file_size_kb, format, video_codec, audio_codec, resolution.
    """
    instances = []
    for data in clips_data:
        clip = Clip(
            moment_id=data["moment_id"],
            video_id=data["video_id"],
            cloud_url=data["cloud_url"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            padding_left=data["padding_left"],
            padding_right=data["padding_right"],
            file_size_kb=data.get("file_size_kb"),
            format=data.get("format"),
            video_codec=data.get("video_codec"),
            audio_codec=data.get("audio_codec"),
            resolution=data.get("resolution"),
        )
        instances.append(clip)

    session.add_all(instances)
    await session.flush()
    for clip in instances:
        await session.refresh(clip)
    return instances
