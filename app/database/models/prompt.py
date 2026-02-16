"""
Prompt model - reusable prompt templates for AI generation.
"""
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.base import Base


class Prompt(Base):
    """
    Prompts table - stores reusable prompt templates with hash for deduplication.
    """
    __tablename__ = "prompts"
    
    # Columns
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now()
    )
    
    # Relationships
    generation_configs: Mapped[list["GenerationConfig"]] = relationship(
        "GenerationConfig",
        back_populates="prompt",
        cascade="all, delete-orphan"
    )
