"""
Transcript model - one-to-one transcript storage for videos.
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB

from app.database.base import Base


class Transcript(Base):
    """
    Transcripts table - stores complete transcripts with word and segment timestamps.
    """
    __tablename__ = "transcripts"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    word_timestamps: Mapped[dict] = mapped_column(JSONB, nullable=False)
    segment_timestamps: Mapped[dict] = mapped_column(JSONB, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    number_of_words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    number_of_segments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transcription_service: Mapped[str | None] = mapped_column(String(50), nullable=True)
    processing_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        nullable=False, 
        server_default=func.now()
    )
    
    # Relationships
    video: Mapped["Video"] = relationship("Video", back_populates="transcript")
    
    # Indexes
    __table_args__ = (
        # GIN index for full-text search using text() to avoid literal rendering issues
        Index(
            'idx_transcripts_full_text_gin',
            text("to_tsvector('english', full_text)"),
            postgresql_using='gin'
        ),
    )
