from pydantic import BaseModel
from typing import Optional


class Video(BaseModel):
    id: str
    filename: str
    title: str
    thumbnail_url: Optional[str] = None
    has_audio: Optional[bool] = None
    has_transcript: Optional[bool] = None


class Moment(BaseModel):
    id: Optional[str] = None           # Unique identifier (generated from timestamp hash)
    start_time: float
    end_time: float
    title: str
    is_refined: bool = False           # True if this is a refined moment
    parent_id: Optional[str] = None    # ID of original moment (for refined moments)
    model_name: Optional[str] = None   # AI model name used to generate/refine this moment
    prompt: Optional[str] = None       # Full prompt used to generate/refine this moment



