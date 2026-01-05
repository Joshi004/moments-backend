"""
Pydantic models for Pipeline API request/response validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from enum import Enum


class PipelineStage(str, Enum):
    """Pipeline stage enumeration."""
    AUDIO_EXTRACTION = "audio"
    AUDIO_UPLOAD = "audio_upload"
    TRANSCRIPTION = "transcript"
    MOMENT_GENERATION = "generation"
    CLIP_EXTRACTION = "clips"
    CLIP_UPLOAD = "clip_upload"
    MOMENT_REFINEMENT = "refinement"


class StageStatus(str, Enum):
    """Stage status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class PipelineStartRequest(BaseModel):
    """Request model for starting a pipeline."""
    model: str = Field(default="qwen3_vl_fp8", pattern="^(qwen3_vl_fp8|minimax)$")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    min_moment_length: float = Field(default=60, ge=10, le=300)
    max_moment_length: float = Field(default=120, ge=30, le=600)
    min_moments: int = Field(default=3, ge=1, le=50)
    max_moments: int = Field(default=10, ge=1, le=100)
    refinement_parallel_workers: int = Field(default=2, ge=1, le=5)
    include_video_refinement: bool = Field(default=True)
    generation_prompt: Optional[str] = None
    refinement_prompt: Optional[str] = None


class StageStatusResponse(BaseModel):
    """Response model for a single stage status."""
    status: StageStatus
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    error: Optional[str] = None


class PipelineStatusResponse(BaseModel):
    """Response model for pipeline status."""
    request_id: str
    video_id: str
    status: str  # pending|processing|completed|failed|cancelled|not_running|never_run
    model: str
    started_at: float
    completed_at: Optional[float] = None
    total_duration_seconds: Optional[float] = None
    current_stage: Optional[str] = None
    stages: Dict[str, StageStatusResponse]
    error_stage: Optional[str] = None
    error_message: Optional[str] = None


class PipelineStartResponse(BaseModel):
    """Response model for pipeline start request."""
    request_id: str
    video_id: str
    status: str
    message: str





