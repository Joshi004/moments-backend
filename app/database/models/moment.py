"""
Moment model - AI-identified video segments with timestamps and metadata.
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class Moment(Base):
    """
    Moments table - stores AI-identified segments within videos.
    """
    __tablename__ = "moments"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identifier: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    video_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    is_refined: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("moments.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    generation_config_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("generation_configs.id", ondelete="SET NULL"),
        nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now()
    )
    
    # Relationships
    video: Mapped["Video"] = relationship("Video", back_populates="moments")
    parent: Mapped["Moment"] = relationship(
        "Moment",
        remote_side=[id],
        back_populates="children"
    )
    children: Mapped[list["Moment"]] = relationship(
        "Moment",
        back_populates="parent",
        cascade="all"
    )
    clip: Mapped["Clip"] = relationship(
        "Clip",
        back_populates="moment",
        uselist=False,
        cascade="all, delete-orphan"
    )
    generation_config: Mapped["GenerationConfig"] = relationship(
        "GenerationConfig",
        back_populates="moments"
    )
    
    # Constraints and Indexes
    __table_args__ = (
        CheckConstraint('end_time > start_time', name='check_moment_end_after_start'),
        Index('idx_moments_timestamps', 'start_time', 'end_time'),
    )
