from pydantic import BaseModel
from typing import Optional


class Video(BaseModel):
    id: str
    filename: str
    title: str
    thumbnail_url: Optional[str] = None

