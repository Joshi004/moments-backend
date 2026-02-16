"""
Clip model - video clips extracted from moments with padding.
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, BigInteger, Text, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class Clip(Base):
    """
    Clips table - stores video clips extracted from moments.
    """
    __tablename__ = "clips"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    moment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("moments.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )
    video_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    cloud_url: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    padding_left: Mapped[float] = mapped_column(Float, nullable=False)
    padding_right: Mapped[float] = mapped_column(Float, nullable=False)
    file_size_kb: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(50), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )
    
    # Relationships
    moment: Mapped["Moment"] = relationship("Moment", back_populates="clip")
    video: Mapped["Video"] = relationship("Video", back_populates="clips")
    thumbnails: Mapped[list["Thumbnail"]] = relationship(
        "Thumbnail",
        foreign_keys="[Thumbnail.clip_id]",
        back_populates="clip",
        cascade="all, delete-orphan"
    )
    
    # Constraints
    __table_args__ = (
        CheckConstraint('end_time > start_time', name='check_clip_end_after_start'),
    )
