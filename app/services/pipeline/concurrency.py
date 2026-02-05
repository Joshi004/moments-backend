"""
Global concurrency limits for cross-pipeline coordination.

This module provides a singleton class that manages asyncio.Semaphore instances
for coordinating concurrent operations across multiple pipeline executions within
a single worker process.

For multi-worker deployments, these semaphores would need to be migrated to
Redis-based distributed semaphores to coordinate across worker processes.
"""
import asyncio
import logging
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class GlobalConcurrencyLimits:
    """
    Global semaphores for cross-pipeline coordination within a single worker.
    
    Uses asyncio.Semaphore for in-process coordination across multiple concurrent
    pipeline executions. This prevents resource overload by limiting concurrent
    operations in CPU/memory/API-intensive phases.
    
    Example:
        >>> limits = GlobalConcurrencyLimits.get()
        >>> async with limits.audio_extraction:
        ...     # Only N audio extractions can run concurrently
        ...     await extract_audio(...)
    
    Note:
        For multi-worker setup, migrate to Redis-based semaphores for
        cross-process coordination.
    """
    
    _instance: Optional["GlobalConcurrencyLimits"] = None
    
    def __init__(self):
        """Initialize semaphores with configured limits."""
        settings = get_settings()
        
        # Phase-level limits shared across all pipelines in this worker
        self.audio_extraction = asyncio.Semaphore(
            settings.audio_extraction_max_concurrent
        )
        self.transcription = asyncio.Semaphore(
            settings.transcription_max_concurrent
        )
        self.moment_generation = asyncio.Semaphore(
            settings.moment_generation_max_concurrent
        )
        self.clip_extraction = asyncio.Semaphore(
            settings.clip_extraction_max_concurrent
        )
        self.refinement = asyncio.Semaphore(
            settings.refinement_max_concurrent
        )
        
        logger.info(
            f"Initialized global concurrency limits: "
            f"audio_extraction={settings.audio_extraction_max_concurrent}, "
            f"transcription={settings.transcription_max_concurrent}, "
            f"moment_generation={settings.moment_generation_max_concurrent}, "
            f"clip_extraction={settings.clip_extraction_max_concurrent}, "
            f"refinement={settings.refinement_max_concurrent}"
        )
    
    @classmethod
    def get(cls) -> "GlobalConcurrencyLimits":
        """
        Get or create the singleton instance.
        
        Returns:
            GlobalConcurrencyLimits: The singleton instance
        """
        if cls._instance is None:
            cls._instance = GlobalConcurrencyLimits()
        return cls._instance
    
    @classmethod
    def reset(cls) -> None:
        """
        Reset the singleton instance.
        
        This is primarily used for testing to ensure a fresh instance
        with new semaphore states.
        """
        cls._instance = None
        logger.debug("Reset GlobalConcurrencyLimits singleton")
