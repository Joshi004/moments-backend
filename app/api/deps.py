"""
Dependency injection for FastAPI endpoints.
Provides singleton instances and factory functions for services.
"""
from functools import lru_cache
from typing import Optional
from app.core.config import Settings, get_settings
from app.repositories.transcript_repository import TranscriptRepository


@lru_cache()
def get_app_settings() -> Settings:
    """
    Get application settings (cached).
    
    Returns:
        Settings instance
    """
    return get_settings()


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

