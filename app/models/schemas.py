"""
Pydantic models for API request/response validation.
"""
from pydantic import BaseModel
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
    # New fields from database
    duration_seconds: Optional[float] = None
    cloud_url: Optional[str] = None
    source_url: Optional[str] = None
    created_at: Optional[str] = None


class ClipInfo(BaseModel):
    """Minimal clip data embedded in moment responses."""
    id: int
    cloud_url: str


class MomentResponse(BaseModel):
    """Response model for moment data."""
    id: Optional[str] = None
    start_time: float
    end_time: float
    title: str
    is_refined: bool = False
    parent_id: Optional[str] = None
    model_name: Optional[str] = None
    generation_config: Optional[Dict[str, Any]] = None
    clip: Optional[ClipInfo] = None


class VideoAvailabilityResponse(BaseModel):
    """Response model for video clip availability check."""
    available: bool
    clip_url: Optional[str] = None
    clip_duration: Optional[float] = None
    transcript_duration: Optional[float] = None
    duration_match: bool = False
    warning: Optional[str] = None
    model_supports_video: bool = False
