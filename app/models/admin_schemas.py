"""
Pydantic schemas for admin API endpoints.
Used for model configuration management.
"""
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime


class ModelConfigBase(BaseModel):
    """Base schema shared by create, update, and response models."""

    name: str = Field(..., description="Display name for the model")
    model_id: Optional[str] = Field(None, description="Model identifier for API calls")
    supports_video: bool = Field(False, description="Whether model supports video input")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="Sampling top_p parameter")
    top_k: Optional[int] = Field(None, ge=1, description="Sampling top_k parameter")
    host: str = Field(..., description="Host the application calls (IP, hostname, or localhost)")
    port: int = Field(..., ge=1, le=65535, description="Port the application calls")


class ModelConfigCreate(ModelConfigBase):
    """Schema for creating a new model configuration."""
    pass


class ModelConfigUpdate(BaseModel):
    """Schema for partial updates — all fields optional."""

    name: Optional[str] = None
    model_id: Optional[str] = None
    supports_video: Optional[bool] = None
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(None, ge=1)
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)


class ModelConfigResponse(ModelConfigBase):
    """Schema for model configuration responses."""

    model_key: str = Field(..., description="Model identifier key")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")

    # Override base required fields to be optional — Redis data seeded before
    # these fields were introduced may legitimately not have them.
    host: Optional[str] = Field(None, description="Host the application calls")
    port: Optional[int] = Field(None, ge=1, le=65535, description="Port the application calls")

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
