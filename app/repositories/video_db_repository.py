"""
Video database repository - CRUD operations for the videos table.
This is a database-backed repository (unlike the file-based repositories).
"""
from typing import Optional
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.video import Video


async def create(
    session: AsyncSession,
    identifier: str,
    cloud_url: str,
    source_url: Optional[str] = None,
    title: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    file_size_kb: Optional[int] = None,
    video_codec: Optional[str] = None,
    audio_codec: Optional[str] = None,
    resolution: Optional[str] = None,
    frame_rate: Optional[float] = None,
) -> Video:
    """
    Create a new video record in the database.
    
    Args:
        session: Async database session
        identifier: Video identifier (e.g., "motivation")
        cloud_url: GCS path (e.g., "gs://bucket/videos/motivation/motivation.mp4")
        source_url: Original download URL (optional)
        title: Human-readable title (optional)
        duration_seconds: Video duration (optional)
        file_size_kb: File size in KB (optional)
        video_codec: Video codec (e.g., "h264") (optional)
        audio_codec: Audio codec (e.g., "aac") (optional)
        resolution: Video resolution (e.g., "1920x1080") (optional)
        frame_rate: Frame rate (e.g., 30.0) (optional)
    
    Returns:
        Created Video instance
    
    Raises:
        IntegrityError: If identifier already exists
    """
    video = Video(
        identifier=identifier,
        cloud_url=cloud_url,
        source_url=source_url,
        title=title,
        duration_seconds=duration_seconds,
        file_size_kb=file_size_kb,
        video_codec=video_codec,
        audio_codec=audio_codec,
        resolution=resolution,
        frame_rate=frame_rate,
    )
    session.add(video)
    await session.flush()  # Flush to get the ID
    await session.refresh(video)  # Refresh to get server defaults
    return video


async def get_by_identifier(session: AsyncSession, identifier: str) -> Optional[Video]:
    """
    Get a video by its identifier.
    
    Args:
        session: Async database session
        identifier: Video identifier
    
    Returns:
        Video instance or None if not found
    """
    stmt = select(Video).where(Video.identifier == identifier)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, id: int) -> Optional[Video]:
    """
    Get a video by its numeric database ID.
    
    Args:
        session: Async database session
        id: Video ID
    
    Returns:
        Video instance or None if not found
    """
    stmt = select(Video).where(Video.id == id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_source_url(session: AsyncSession, source_url: str) -> Optional[Video]:
    """
    Get a video by its source URL.
    
    Args:
        session: Async database session
        source_url: Original download URL
    
    Returns:
        Video instance or None if not found
    """
    stmt = select(Video).where(Video.source_url == source_url)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_all(session: AsyncSession) -> list[Video]:
    """
    List all videos ordered by creation date (newest first).
    
    Args:
        session: Async database session
    
    Returns:
        List of Video instances
    """
    stmt = select(Video).order_by(Video.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update(session: AsyncSession, id: int, **fields) -> Optional[Video]:
    """
    Update a video record.
    
    Args:
        session: Async database session
        id: Video ID
        **fields: Fields to update (e.g., title="New Title")
    
    Returns:
        Updated Video instance or None if not found
    """
    stmt = update(Video).where(Video.id == id).values(**fields).returning(Video)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def delete_by_id(session: AsyncSession, id: int) -> bool:
    """
    Delete a video by its numeric database ID.

    Args:
        session: Async database session
        id: Video database ID

    Returns:
        True if deleted, False if not found
    """
    stmt = delete(Video).where(Video.id == id)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def delete_by_identifier(session: AsyncSession, identifier: str) -> bool:
    """
    Delete a video by its identifier.
    
    Args:
        session: Async database session
        identifier: Video identifier
    
    Returns:
        True if deleted, False if not found
    """
    stmt = delete(Video).where(Video.identifier == identifier)
    result = await session.execute(stmt)
    return result.rowcount > 0
