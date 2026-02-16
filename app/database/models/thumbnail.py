"""
Thumbnail model - thumbnail images for videos and clips.
"""
from datetime import datetime
from sqlalchemy import Integer, BigInteger, Text, DateTime, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class Thumbnail(Base):
    """
    Thumbnails table - stores thumbnail images for videos or clips (mutually exclusive).
    """
    __tablename__ = "thumbnails"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    clip_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clips.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    cloud_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_kb: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )
    
    # Relationships
    video: Mapped["Video"] = relationship(
        "Video",
        foreign_keys=[video_id],
        back_populates="thumbnails"
    )
    clip: Mapped["Clip"] = relationship(
        "Clip",
        foreign_keys=[clip_id],
        back_populates="thumbnails"
    )
    
    # Constraints and Indexes
    __table_args__ = (
        # XOR constraint: exactly one of video_id or clip_id must be set
        CheckConstraint(
            '(video_id IS NOT NULL AND clip_id IS NULL) OR (video_id IS NULL AND clip_id IS NOT NULL)',
            name='check_thumbnail_video_or_clip'
        ),
        # Partial unique indexes for 1:1 relationship
        Index('idx_thumbnails_video_id_unique', 'video_id', unique=True, postgresql_where='video_id IS NOT NULL'),
        Index('idx_thumbnails_clip_id_unique', 'clip_id', unique=True, postgresql_where='clip_id IS NOT NULL'),
    )
