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
from app.services.ai.tunnel_manager import TunnelManager
from app.services.ai.minimax_client import MinimaxClient
from app.services.ai.qwen_client import QwenClient
from app.services.ai.qwen3_omni_client import Qwen3OmniClient
from app.services.ai.qwen3_vl_client import Qwen3VLClient

# Global singleton instances
_tunnel_manager: Optional[TunnelManager] = None
_job_repository: Optional[JobRepository] = None


@lru_cache()
def get_app_settings() -> Settings:
    """
    Get application settings (cached).
    
    Returns:
        Settings instance
    """
    return get_settings()


def get_tunnel_manager() -> TunnelManager:
    """
    Get tunnel manager singleton.
    
    Returns:
        TunnelManager instance
    """
    global _tunnel_manager
    if _tunnel_manager is None:
        settings = get_app_settings()
        _tunnel_manager = TunnelManager(settings)
    return _tunnel_manager


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


# AI Model Clients

def get_minimax_client() -> MinimaxClient:
    """
    Get MiniMax AI client.
    
    Returns:
        MinimaxClient instance
    """
    tunnel_manager = get_tunnel_manager()
    settings = get_app_settings()
    return MinimaxClient(tunnel_manager, settings)


def get_qwen_client() -> QwenClient:
    """
    Get Qwen AI client.
    
    Returns:
        QwenClient instance
    """
    tunnel_manager = get_tunnel_manager()
    settings = get_app_settings()
    return QwenClient(tunnel_manager, settings)


def get_qwen3_omni_client() -> Qwen3OmniClient:
    """
    Get Qwen3-Omni AI client.
    
    Returns:
        Qwen3OmniClient instance
    """
    tunnel_manager = get_tunnel_manager()
    settings = get_app_settings()
    return Qwen3OmniClient(tunnel_manager, settings)


def get_qwen3_vl_client() -> Qwen3VLClient:
    """
    Get Qwen3-VL-FP8 AI client.
    
    Returns:
        Qwen3VLClient instance
    """
    tunnel_manager = get_tunnel_manager()
    settings = get_app_settings()
    return Qwen3VLClient(tunnel_manager, settings)


def get_ai_client_by_name(model_name: str):
    """
    Get AI client by model name.
    
    Args:
        model_name: Model identifier (minimax, qwen, qwen3_omni, qwen3_vl_fp8)
        
    Returns:
        Appropriate AI client instance
        
    Raises:
        ValueError: If model name is invalid
    """
    client_map = {
        "minimax": get_minimax_client,
        "qwen": get_qwen_client,
        "qwen3_omni": get_qwen3_omni_client,
        "qwen3_vl_fp8": get_qwen3_vl_client,
    }
    
    if model_name not in client_map:
        raise ValueError(
            f"Invalid model name: {model_name}. "
            f"Must be one of: {', '.join(client_map.keys())}"
        )
    
    return client_map[model_name]()


# Cleanup function for application shutdown
def cleanup_resources():
    """Clean up resources on application shutdown."""
    global _tunnel_manager
    if _tunnel_manager is not None:
        _tunnel_manager.close_all_tunnels()

