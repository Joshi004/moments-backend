"""
Audio model - audio file extracted from a video, stored in cloud.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Integer, Float, BigInteger, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class Audio(Base):
    """
    Audios table - stores audio file metadata with cloud storage references.
    1:1 with Video; enforced by UNIQUE constraint on video_id.
    """
    __tablename__ = "audios"

    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("videos.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    cloud_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_kb: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    video: Mapped["Video"] = relationship("Video", back_populates="audio")
