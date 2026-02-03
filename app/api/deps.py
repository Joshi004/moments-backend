"""
Dependency injection for FastAPI endpoints.
Provides singleton instances and factory functions for services.
"""
from functools import lru_cache
from typing import Optional
from app.core.config import Settings, get_settings
from app.repositories.moments_repository import MomentsRepository
from app.repositories.transcript_repository import TranscriptRepository
from app.repositories.job_repository import JobRepository

# Global singleton instances
_job_repository: Optional[JobRepository] = None


@lru_cache()
def get_app_settings() -> Settings:
    """
    Get application settings (cached).
    
    Returns:
        Settings instance
    """
    return get_settings()


def get_job_repository() -> JobRepository:
    """
    Get job repository singleton.
    
    Returns:
        JobRepository instance
    """
    global _job_repository
    if _job_repository is None:
        _job_repository = JobRepository()
    return _job_repository


def get_moments_repository(
    settings: Settings = None
) -> MomentsRepository:
    """
    Get moments repository.
    
    Args:
        settings: Application settings (optional, will get default if not provided)
        
    Returns:
        MomentsRepository instance
    """
    if settings is None:
        settings = get_app_settings()
    return MomentsRepository(settings.moments_dir)


def get_transcript_repository(
    settings: Settings = None
) -> TranscriptRepository:
    """
    Get transcript repository.
    
    Args:
        settings: Application settings (optional, will get default if not provided)
        
    Returns:
        TranscriptRepository instance
    """
    if settings is None:
        settings = get_app_settings()
    return TranscriptRepository(settings.transcripts_dir)


# Cleanup function for application shutdown
def cleanup_resources():
    """Clean up resources on application shutdown."""
    # No resources to clean up currently
    pass

