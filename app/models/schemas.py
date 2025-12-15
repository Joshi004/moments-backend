"""
Pydantic models for API request/response validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


# Response Models

class VideoResponse(BaseModel):
    """Response model for video data."""
    id: str
    filename: str
    title: str
    thumbnail_url: Optional[str] = None
    has_audio: Optional[bool] = None
    has_transcript: Optional[bool] = None


class MomentResponse(BaseModel):
    """Response model for moment data."""
    id: Optional[str] = None
    start_time: float
    end_time: float
    title: str
    is_refined: bool = False
    parent_id: Optional[str] = None
    model_name: Optional[str] = None
    prompt: Optional[str] = None
    generation_config: Optional[Dict[str, Any]] = None


class JobStatusResponse(BaseModel):
    """Response model for job status."""
    status: str
    started_at: float
    completed_at: Optional[float] = None
    error: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    """Response model for errors."""
    error: str
    detail: Optional[str] = None
    status_code: int


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str
    video_id: Optional[str] = None
    moment_id: Optional[str] = None


# Request Models

class GenerateMomentsRequest(BaseModel):
    """Request model for moment generation."""
    user_prompt: Optional[str] = None
    min_moment_length: float = Field(default=60.0, gt=0)
    max_moment_length: float = Field(default=600.0, gt=0)
    min_moments: int = Field(default=1, gt=0)
    max_moments: int = Field(default=10, gt=0)
    model: str = Field(default="minimax")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class RefineMomentRequest(BaseModel):
    """Request model for moment refinement."""
    user_prompt: Optional[str] = None
    model: str = Field(default="minimax")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    include_video: bool = Field(default=False)


class ExtractClipsRequest(BaseModel):
    """Request model for clip extraction."""
    override_existing: bool = Field(default=True)


class CreateMomentRequest(BaseModel):
    """Request model for creating a moment."""
    start_time: float = Field(gt=0)
    end_time: float = Field(gt=0)
    title: str = Field(min_length=1)


# Video Availability Response

class VideoAvailabilityResponse(BaseModel):
    """Response model for video clip availability check."""
    available: bool
    clip_url: Optional[str] = None
    clip_duration: Optional[float] = None
    transcript_duration: Optional[float] = None
    duration_match: bool = False
    warning: Optional[str] = None
    model_supports_video: bool = False

