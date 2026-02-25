"""
Database models package.
All models must be imported here so Alembic can discover them via Base.metadata.
"""
from app.database.models.video import Video
from app.database.models.transcript import Transcript
from app.database.models.moment import Moment
from app.database.models.prompt import Prompt
from app.database.models.generation_config import GenerationConfig
from app.database.models.clip import Clip
from app.database.models.thumbnail import Thumbnail
from app.database.models.pipeline_history import PipelineHistory
from app.database.models.audio import Audio

__all__ = [
    "Video",
    "Transcript",
    "Moment",
    "Prompt",
    "GenerationConfig",
    "Clip",
    "Thumbnail",
    "PipelineHistory",
    "Audio",
]
