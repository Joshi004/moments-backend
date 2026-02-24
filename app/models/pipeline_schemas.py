"""
Pydantic models for Pipeline API request/response validation.
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Dict, Any, List
from enum import Enum
from urllib.parse import urlparse


class PipelineStage(str, Enum):
    """Pipeline stage enumeration."""
    VIDEO_DOWNLOAD = "download"
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
    """
    Request model for starting a pipeline.

    Accepts either an existing video_id or a video_url to download.
    All pipeline config fields have sensible defaults.
    """

    # Video source -- provide one of these
    video_id: Optional[str] = Field(default=None, description="Existing video ID")
    video_url: Optional[str] = Field(default=None, description="URL to download video from")

    # Download options -- only relevant when video_url is provided
    force_download: bool = Field(
        default=False,
        description="Force re-download even if URL was previously cached",
    )

    # Pipeline configuration
    generation_model: str = Field(default="qwen3_vl_fp8", pattern="^(qwen3_vl_fp8|minimax)$")
    refinement_model: str = Field(default="qwen3_vl_fp8", pattern="^(qwen3_vl_fp8|minimax)$")
    generation_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    refinement_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    min_moment_length: float = Field(default=60, ge=10, le=300)
    max_moment_length: float = Field(default=120, ge=30, le=600)
    min_moments: int = Field(default=3, ge=1, le=50)
    max_moments: int = Field(default=10, ge=1, le=100)
    refinement_parallel_workers: int = Field(default=2, ge=1, le=5)
    include_video_refinement: bool = Field(default=True)
    generation_prompt: Optional[str] = None
    override_existing_moments: bool = Field(default=True)
    override_existing_refinement: bool = Field(default=True)

    @model_validator(mode='after')
    def validate_video_source(self):
        """Validate that at least one video source is provided."""
        if not self.video_id and not self.video_url:
            raise ValueError("Either video_id or video_url must be provided")
        return self

    @field_validator('video_url')
    @classmethod
    def validate_url_format(cls, v: Optional[str]) -> Optional[str]:
        """Validate URL format if provided."""
        if v is None:
            return v
        try:
            parsed = urlparse(v)
        except Exception as e:
            raise ValueError(f"Invalid URL format: {e}")
        if parsed.scheme not in ('http', 'https', 'gs'):
            raise ValueError(
                f"Unsupported URL scheme: {parsed.scheme}. "
                "Supported: http, https, gs (Google Cloud Storage)"
            )
        if parsed.scheme == 'gs' and not parsed.netloc:
            raise ValueError("GCS URI must specify bucket: gs://bucket/path")
        if not parsed.path or parsed.path == '/':
            raise ValueError("URL must include a file path")
        return v


class MomentSummary(BaseModel):
    """Minimal moment info for status response."""
    id: str
    title: str
    start_time: float
    end_time: float


class StageStatusResponse(BaseModel):
    """Response model for a single stage status."""
    status: StageStatus
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    error: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None  # For download progress, refinement progress, etc.


class PipelineStatusResponse(BaseModel):
    """Response model for pipeline status."""
    request_id: str
    video_id: str
    status: str  # pending|processing|completed|failed|cancelled|not_running|never_run
    generation_model: str
    refinement_model: str
    started_at: Optional[str] = None  # ISO 8601 string, e.g. "2026-02-11T10:38:17Z"
    completed_at: Optional[str] = None  # ISO 8601 string or null
    total_duration_seconds: Optional[float] = None
    current_stage: Optional[str] = None
    stages: Dict[str, StageStatusResponse]
    error_stage: Optional[str] = None
    error_message: Optional[str] = None
    coarse_moments: List[MomentSummary] = []
    refined_moments: List[MomentSummary] = []


class PipelineStartResponse(BaseModel):
    """Response model for pipeline start request."""
    request_id: str = Field(description="Unique pipeline request ID")
    video_id: str = Field(description="Video identifier (generated or provided)")
    status: str = Field(description="Pipeline status (queued, processing, etc.)")
    message: str = Field(description="Human-readable status message")
    download_required: bool = Field(
        default=False,
        description="Whether video download is required",
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Source URL if download was requested",
    )
    is_cached: bool = Field(
        default=False,
        description="Whether video was found in cache (no download needed)",
    )





