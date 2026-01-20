"""
Pydantic models for Generate Moments API request/response validation.
Unified endpoint supporting both existing videos and URL downloads.
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional
from urllib.parse import urlparse


class GenerateMomentsRequest(BaseModel):
    """Request model for generating moments from video (existing or URL)."""
    
    # Video Source (at least one required)
    video_id: Optional[str] = Field(default=None, description="Existing video ID")
    video_url: Optional[str] = Field(default=None, description="URL to download video from")
    
    # Download Options
    force_download: bool = Field(
        default=False,
        description="Force re-download even if URL was previously cached"
    )
    
    # Pipeline Configuration (same as PipelineStartRequest)
    model: str = Field(default="qwen3_vl_fp8", pattern="^(qwen3_vl_fp8|minimax)$")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
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
        
        # Parse URL
        try:
            parsed = urlparse(v)
        except Exception as e:
            raise ValueError(f"Invalid URL format: {e}")
        
        # Check scheme
        if parsed.scheme not in ('http', 'https', 'gs'):
            raise ValueError(
                f"Unsupported URL scheme: {parsed.scheme}. "
                "Supported: http, https, gs (Google Cloud Storage)"
            )
        
        # Check netloc/bucket for gs:// URLs
        if parsed.scheme == 'gs' and not parsed.netloc:
            raise ValueError("GCS URI must specify bucket: gs://bucket/path")
        
        # Check path exists
        if not parsed.path or parsed.path == '/':
            raise ValueError("URL must include a file path")
        
        return v


class GenerateMomentsResponse(BaseModel):
    """Response model for generate moments request."""
    
    request_id: str = Field(description="Unique pipeline request ID")
    video_id: str = Field(description="Video identifier (generated or provided)")
    status: str = Field(description="Pipeline status (queued, processing, etc.)")
    message: str = Field(description="Human-readable status message")
    download_required: bool = Field(
        description="Whether video download is required"
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Source URL if download was requested"
    )
    is_cached: bool = Field(
        default=False,
        description="Whether video was found in cache (no download needed)"
    )


