"""
Video model - central entity for all video content.
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, BigInteger, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class Video(Base):
    """
    Videos table - stores video metadata with cloud storage references.
    """
    __tablename__ = "videos"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identifier: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    cloud_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size_kb: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(50), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    frame_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        nullable=False, 
        server_default=func.now(),
        index=True
    )
    
    # Relationships
    transcript: Mapped["Transcript"] = relationship(
        "Transcript", 
        back_populates="video", 
        uselist=False,
        cascade="all, delete-orphan"
    )
    moments: Mapped[list["Moment"]] = relationship(
        "Moment", 
        back_populates="video",
        cascade="all, delete-orphan"
    )
    clips: Mapped[list["Clip"]] = relationship(
        "Clip", 
        back_populates="video",
        cascade="all, delete-orphan"
    )
    thumbnails: Mapped[list["Thumbnail"]] = relationship(
        "Thumbnail",
        foreign_keys="[Thumbnail.video_id]",
        back_populates="video",
        cascade="all, delete-orphan"
    )
    pipeline_runs: Mapped[list["PipelineHistory"]] = relationship(
        "PipelineHistory", 
        back_populates="video",
        cascade="all, delete-orphan"
    )
