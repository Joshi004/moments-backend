"""
Dependency injection for FastAPI endpoints.
Provides singleton instances and factory functions for services.
"""
from functools import lru_cache
from app.core.config import Settings, get_settings


@lru_cache()
def get_app_settings() -> Settings:
    """
    Get application settings (cached).

    Returns:
        Settings instance
    """
    return get_settings()


# Cleanup function for application shutdown
def cleanup_resources():
    """Clean up resources on application shutdown."""
    # No resources to clean up currently
    pass
