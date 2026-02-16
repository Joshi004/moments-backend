"""
Transcript database repository - CRUD operations for the transcripts table.
This is a database-backed repository (unlike the file-based repositories).
"""
from typing import Optional
from sqlalchemy import select, delete, exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.transcript import Transcript
from app.database.models.video import Video


async def create(
    session: AsyncSession,
    video_id: int,
    full_text: str,
    word_timestamps: list,
    segment_timestamps: list,
    language: str = "en",
    number_of_words: Optional[int] = None,
    number_of_segments: Optional[int] = None,
    transcription_service: Optional[str] = None,
    processing_time_seconds: Optional[float] = None,
) -> Transcript:
    """
    Create a new transcript record in the database.
    
    Args:
        session: Async database session
        video_id: Foreign key to videos table
        full_text: Complete transcript text
        word_timestamps: Array of word-level timestamps (JSONB)
        segment_timestamps: Array of segment-level timestamps (JSONB)
        language: Language code (default: "en")
        number_of_words: Count of words (optional, computed from word_timestamps if None)
        number_of_segments: Count of segments (optional, computed from segment_timestamps if None)
        transcription_service: Service used (e.g., "parakeet")
        processing_time_seconds: Time taken to transcribe
    
    Returns:
        Created Transcript instance
    
    Raises:
        IntegrityError: If video_id doesn't exist or transcript already exists for this video
    """
    # Compute word/segment counts if not provided
    if number_of_words is None:
        number_of_words = len(word_timestamps) if word_timestamps else 0
    if number_of_segments is None:
        number_of_segments = len(segment_timestamps) if segment_timestamps else 0
    
    transcript = Transcript(
        video_id=video_id,
        full_text=full_text,
        word_timestamps=word_timestamps,
        segment_timestamps=segment_timestamps,
        language=language,
        number_of_words=number_of_words,
        number_of_segments=number_of_segments,
        transcription_service=transcription_service,
        processing_time_seconds=processing_time_seconds,
    )
    session.add(transcript)
    await session.flush()  # Flush to get the ID
    await session.refresh(transcript)  # Refresh to get server defaults
    return transcript


async def get_by_video_id(session: AsyncSession, video_id: int) -> Optional[Transcript]:
    """
    Get a transcript by its video_id (numeric FK).
    
    Args:
        session: Async database session
        video_id: Video ID (foreign key)
    
    Returns:
        Transcript instance or None if not found
    """
    stmt = select(Transcript).where(Transcript.video_id == video_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_video_identifier(session: AsyncSession, identifier: str) -> Optional[Transcript]:
    """
    Get a transcript by video identifier (string).
    Joins with videos table to resolve identifier to video_id.
    
    Args:
        session: Async database session
        identifier: Video identifier (e.g., "motivation")
    
    Returns:
        Transcript instance or None if not found
    """
    stmt = (
        select(Transcript)
        .join(Video, Transcript.video_id == Video.id)
        .where(Video.identifier == identifier)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def exists_for_video(session: AsyncSession, video_id: int) -> bool:
    """
    Check if a transcript exists for a video (by numeric video_id).
    
    Args:
        session: Async database session
        video_id: Video ID (foreign key)
    
    Returns:
        True if transcript exists, False otherwise
    """
    stmt = select(exists().where(Transcript.video_id == video_id))
    result = await session.execute(stmt)
    return result.scalar()


async def exists_by_identifier(session: AsyncSession, identifier: str) -> bool:
    """
    Check if a transcript exists for a video (by string identifier).
    Joins with videos table to resolve identifier.
    
    Args:
        session: Async database session
        identifier: Video identifier (e.g., "motivation")
    
    Returns:
        True if transcript exists, False otherwise
    """
    stmt = (
        select(exists().where(Transcript.video_id == Video.id))
        .select_from(Video)
        .where(Video.identifier == identifier)
    )
    result = await session.execute(stmt)
    return result.scalar()


async def delete_by_video_id(session: AsyncSession, video_id: int) -> bool:
    """
    Delete a transcript by video_id.
    
    Args:
        session: Async database session
        video_id: Video ID (foreign key)
    
    Returns:
        True if deleted, False if not found
    """
    stmt = delete(Transcript).where(Transcript.video_id == video_id)
    result = await session.execute(stmt)
    return result.rowcount > 0
