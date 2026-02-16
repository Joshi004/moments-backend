"""
Generation Config model - AI generation configuration parameters.
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class GenerationConfig(Base):
    """
    Generation Configs table - stores AI generation parameters with hash for deduplication.
    """
    __tablename__ = "generation_configs"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("prompts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    transcript_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("transcripts.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_p: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_moment_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_moment_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_moments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_moments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )
    
    # Relationships
    prompt: Mapped["Prompt"] = relationship("Prompt", back_populates="generation_configs")
    transcript: Mapped["Transcript"] = relationship("Transcript")
    moments: Mapped[list["Moment"]] = relationship(
        "Moment",
        back_populates="generation_config",
        cascade="all, delete-orphan"
    )
    pipeline_runs: Mapped[list["PipelineHistory"]] = relationship(
        "PipelineHistory",
        back_populates="generation_config",
        cascade="all, delete-orphan"
    )
