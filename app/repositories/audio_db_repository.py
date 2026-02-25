"""
Audio database repository - CRUD operations for the audios table.
Follows the same module-level async function pattern as other db repositories.
"""
import logging
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.audio import Audio
from app.database.models.video import Video

logger = logging.getLogger(__name__)


async def create(
    session: AsyncSession,
    video_id: int,
    cloud_url: str,
    file_size_kb: Optional[int] = None,
    format: Optional[str] = None,
    sample_rate: Optional[int] = None,
    duration_seconds: Optional[float] = None,
) -> Audio:
    """
    Insert a single audio row.

    Returns the Audio instance with its auto-generated id populated.
    """
    audio = Audio(
        video_id=video_id,
        cloud_url=cloud_url,
        file_size_kb=file_size_kb,
        format=format,
        sample_rate=sample_rate,
        duration_seconds=duration_seconds,
    )
    session.add(audio)
    await session.flush()
    await session.refresh(audio)
    return audio


async def get_by_video_id(session: AsyncSession, video_id: int) -> Optional[Audio]:
    """Look up the audio record for a specific video by its numeric DB id. Returns Audio or None."""
    stmt = select(Audio).where(Audio.video_id == video_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_video_identifier(session: AsyncSession, video_identifier: str) -> Optional[Audio]:
    """
    Look up the audio record for a video by the video's string identifier.
    Joins with the videos table internally.
    """
    stmt = (
        select(Audio)
        .join(Video, Audio.video_id == Video.id)
        .where(Video.identifier == video_identifier)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_cloud_url(
    session: AsyncSession, audio_id: int, cloud_url: Optional[str]
) -> Optional[Audio]:
    """
    Update the cloud_url for a specific audio record by its numeric id.
    Returns the updated Audio instance, or None if not found.
    """
    stmt = select(Audio).where(Audio.id == audio_id)
    result = await session.execute(stmt)
    audio = result.scalar_one_or_none()
    if audio is None:
        return None
    audio.cloud_url = cloud_url
    await session.flush()
    await session.refresh(audio)
    return audio


async def delete_by_video_id(session: AsyncSession, video_id: int) -> bool:
    """Delete the audio record for a specific video. Returns True if deleted, False if not found."""
    stmt = delete(Audio).where(Audio.video_id == video_id)
    result = await session.execute(stmt)
    return result.rowcount > 0
