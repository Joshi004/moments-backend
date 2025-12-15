"""
Domain models for business logic.
These are internal representations separate from API schemas.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum


class JobStatus(Enum):
    """Job status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(Enum):
    """Job type enumeration."""
    AUDIO_EXTRACTION = "audio_extraction"
    TRANSCRIPTION = "transcription"
    MOMENT_GENERATION = "moment_generation"
    MOMENT_REFINEMENT = "moment_refinement"
    CLIP_EXTRACTION = "clip_extraction"


@dataclass
class Video:
    """Internal video representation."""
    id: str
    filename: str
    title: str
    thumbnail_url: Optional[str] = None
    has_audio: bool = False
    has_transcript: bool = False
    duration: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "filename": self.filename,
            "title": self.title,
            "thumbnail_url": self.thumbnail_url,
            "has_audio": self.has_audio,
            "has_transcript": self.has_transcript,
            "duration": self.duration
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Video':
        """Create from dictionary."""
        return cls(
            id=data["id"],
            filename=data["filename"],
            title=data["title"],
            thumbnail_url=data.get("thumbnail_url"),
            has_audio=data.get("has_audio", False),
            has_transcript=data.get("has_transcript", False),
            duration=data.get("duration")
        )


@dataclass
class Moment:
    """Internal moment representation."""
    start_time: float
    end_time: float
    title: str
    id: Optional[str] = None
    is_refined: bool = False
    parent_id: Optional[str] = None
    model_name: Optional[str] = None
    prompt: Optional[str] = None
    generation_config: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "title": self.title,
            "is_refined": self.is_refined,
            "parent_id": self.parent_id,
            "model_name": self.model_name,
            "prompt": self.prompt,
            "generation_config": self.generation_config
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Moment':
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            start_time=data["start_time"],
            end_time=data["end_time"],
            title=data["title"],
            is_refined=data.get("is_refined", False),
            parent_id=data.get("parent_id"),
            model_name=data.get("model_name"),
            prompt=data.get("prompt"),
            generation_config=data.get("generation_config")
        )
    
    @property
    def duration(self) -> float:
        """Get moment duration in seconds."""
        return self.end_time - self.start_time


@dataclass
class Job:
    """Internal job representation."""
    job_type: JobType
    video_id: str
    status: JobStatus
    started_at: float
    moment_id: Optional[str] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    progress: Optional[Dict[str, Any]] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "job_type": self.job_type.value,
            "video_id": self.video_id,
            "moment_id": self.moment_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "progress": self.progress
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Job':
        """Create from dictionary."""
        return cls(
            job_type=JobType(data["job_type"]),
            video_id=data["video_id"],
            moment_id=data.get("moment_id"),
            status=JobStatus(data["status"]),
            started_at=data["started_at"],
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            progress=data.get("progress", {})
        )


@dataclass
class Transcript:
    """Internal transcript representation."""
    audio_filename: str
    text: str
    segments: list
    word_timestamps: list
    duration: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "audio_filename": self.audio_filename,
            "text": self.text,
            "segments": self.segments,
            "word_timestamps": self.word_timestamps,
            "duration": self.duration
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Transcript':
        """Create from dictionary."""
        return cls(
            audio_filename=data["audio_filename"],
            text=data["text"],
            segments=data["segments"],
            word_timestamps=data["word_timestamps"],
            duration=data["duration"]
        )

