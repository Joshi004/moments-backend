"""
Pydantic schemas for admin API endpoints.
Used for model configuration management.
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime


class ModelConfigBase(BaseModel):
    """Base model configuration schema with all required fields."""
    
    name: str = Field(..., description="Display name for the model")
    ssh_host: str = Field(..., description="SSH jump host (user@host)")
    ssh_remote_host: str = Field(..., description="SLURM worker hostname")
    ssh_local_port: int = Field(..., ge=1024, le=65535, description="Local tunnel port")
    ssh_remote_port: int = Field(..., ge=1024, le=65535, description="Remote service port")
    model_id: Optional[str] = Field(None, description="Model identifier for API calls")
    supports_video: bool = Field(False, description="Whether model supports video input")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="Sampling top_p parameter")
    top_k: Optional[int] = Field(None, ge=1, description="Sampling top_k parameter")
    
    @validator('ssh_host')
    def validate_ssh_host(cls, v):
        """Validate SSH host format (user@host)."""
        if '@' not in v:
            raise ValueError("SSH host must be in format 'user@host'")
        return v
    
    @validator('ssh_remote_host')
    def validate_remote_host(cls, v):
        """Validate remote host is not empty."""
        if not v or not v.strip():
            raise ValueError("SSH remote host cannot be empty")
        return v.strip()


class ModelConfigCreate(ModelConfigBase):
    """Schema for creating a new model configuration."""
    pass


class ModelConfigUpdate(BaseModel):
    """Schema for partial updates - all fields optional."""
    
    name: Optional[str] = None
    ssh_host: Optional[str] = None
    ssh_remote_host: Optional[str] = None
    ssh_local_port: Optional[int] = Field(None, ge=1024, le=65535)
    ssh_remote_port: Optional[int] = Field(None, ge=1024, le=65535)
    model_id: Optional[str] = None
    supports_video: Optional[bool] = None
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(None, ge=1)
    
    @validator('ssh_host')
    def validate_ssh_host(cls, v):
        """Validate SSH host format if provided."""
        if v is not None and '@' not in v:
            raise ValueError("SSH host must be in format 'user@host'")
        return v
    
    @validator('ssh_remote_host')
    def validate_remote_host(cls, v):
        """Validate remote host if provided."""
        if v is not None and (not v or not v.strip()):
            raise ValueError("SSH remote host cannot be empty")
        return v.strip() if v else None


class ModelConfigResponse(ModelConfigBase):
    """Schema for model configuration responses."""
    
    model_key: str = Field(..., description="Model identifier key")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    
    class Config:
        from_attributes = True


class ModelConfigListResponse(BaseModel):
    """Schema for list of model configurations."""
    
    models: List[ModelConfigResponse]
    count: int = Field(..., description="Total number of models")


class SeedRequest(BaseModel):
    """Schema for seed defaults request."""
    
    force: bool = Field(False, description="Overwrite existing configs")


class SeedResponse(BaseModel):
    """Schema for seed defaults response."""
    
    seeded_count: int = Field(..., description="Number of configs seeded")
    message: str


class DeleteResponse(BaseModel):
    """Schema for delete operation response."""
    
    success: bool
    model_key: str
    message: str


class ErrorResponse(BaseModel):
    """Schema for error responses."""
    
    error: str
    message: str
    model_key: Optional[str] = None
    available_models: Optional[List[str]] = None
    resolution: Optional[str] = None
