"""
Pipeline History model - tracks complete pipeline executions and outcomes.
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class PipelineHistory(Base):
    """
    Pipeline History table - tracks complete pipeline executions.
    """
    __tablename__ = "pipeline_history"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identifier: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    video_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    generation_config_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("generation_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    pipeline_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_moments_generated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_clips_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )
    
    # Relationships
    video: Mapped["Video"] = relationship("Video", back_populates="pipeline_runs")
    generation_config: Mapped["GenerationConfig | None"] = relationship(
        "GenerationConfig",
        back_populates="pipeline_runs"
    )
