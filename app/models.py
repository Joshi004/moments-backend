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
    start_time: float
    end_time: float
    title: str



