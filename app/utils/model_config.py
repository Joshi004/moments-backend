"""
Model configuration for AI models used in moment generation and refinement.
"""
from pathlib import Path

# Configuration for video clipping and transcript extraction
CLIPPING_CONFIG = {
    "padding": 30.0,  # Single padding value used for both left and right (seconds)
    "margin": 2.0     # Allow extra margin when finding word boundaries (seconds)
}

# Configuration for video server serving moment clips
VIDEO_SERVER_CONFIG = {
    "base_url": "http://localhost:8080",  # Base URL for video clip server
    "clips_path": "moment_clips",  # Path segment for moment clips
    "duration_tolerance": 0.5,  # Tolerance in seconds for transcript-video duration matching
}

MODELS = {
    "minimax": {
        "name": "MiniMax",
        "model_id": None,  # MiniMax doesn't require explicit model_id in the request
        "ssh_host": "naresh@85.234.64.44",
        "ssh_remote_host": "worker-9",
        "ssh_local_port": 8007,
        "ssh_remote_port": 7104,
    },
    "qwen": {
        "name": "Qwen3-VL",
        "model_id": "qwen3-vl-235b-thinking",
        "ssh_host": "naresh@85.234.64.44",  # Update if Qwen uses different SSH host
        "ssh_remote_host": "worker-9",  # Update if Qwen runs on different remote host
        "ssh_local_port": 6101,
        "ssh_remote_port": 7001,  # Update with actual remote port for Qwen service
    },
    "qwen3_omni": {
        "name": "Qwen3-Omini",
        "model_id": None,  # Qwen3-Omini doesn't require explicit model_id in the request
        "ssh_host": "naresh@85.234.64.44",
        "ssh_remote_host": "worker-9",
        "ssh_local_port": 7101,
        "ssh_remote_port": 8002,  # Update with actual remote port for Qwen3-Omini service
        "supports_video": False,  # Text-only model
        "top_p": 0.95,
        "top_k": 20,
    },
    "qwen3_vl_fp8": {
        "name": "Qwen3-VL-FP8",
        "model_id": None,  # Qwen3-VL-FP8 doesn't require explicit model_id in the request
        "ssh_host": "naresh@85.234.64.44",
        "ssh_remote_host": "worker-9",
        "ssh_local_port": 6010,
        "ssh_remote_port": 8010,
        "supports_video": True,  # This model supports video input for refinement
    },
    "parakeet": {
        "name": "Parakeet",
        "ssh_host": "naresh@85.234.64.44",
        "ssh_remote_host": "worker-9",
        "ssh_local_port": 6106,
        "ssh_remote_port": 8006,
    }
}


def get_model_config(model_key: str) -> dict:
    """
    Get configuration for a specific model.
    
    Args:
        model_key: Model identifier ("minimax", "qwen", or "qwen3_omni")
    
    Returns:
        Dictionary with model configuration
    
    Raises:
        ValueError: If model_key is not found
    """
    if model_key not in MODELS:
        raise ValueError(f"Unknown model: {model_key}. Available models: {list(MODELS.keys())}")
    
    return MODELS[model_key]


def get_model_url(model_key: str) -> str:
    """
    Get the local URL for a model API endpoint.
    
    Args:
        model_key: Model identifier ("minimax", "qwen", or "qwen3_omni")
    
    Returns:
        URL string for the model API endpoint
    """
    config = get_model_config(model_key)
    return f"http://localhost:{config['ssh_local_port']}/v1/chat/completions"


def get_transcription_service_url() -> str:
    """
    Get the local URL for the transcription service endpoint.
    
    Returns:
        URL string for the transcription service API endpoint
    """
    config = get_model_config("parakeet")
    return f"http://localhost:{config['ssh_local_port']}/transcribe"


def get_clipping_config() -> dict:
    """
    Get configuration for video clipping and transcript extraction.
    
    Returns:
        Dictionary with clipping configuration (padding, margin)
    """
    return CLIPPING_CONFIG.copy()


def get_video_server_config() -> dict:
    """
    Get configuration for video server.
    
    Returns:
        Dictionary with video server configuration (base_url, clips_path, duration_tolerance)
    """
    return VIDEO_SERVER_CONFIG.copy()


def model_supports_video(model_key: str) -> bool:
    """
    Check if a model supports video input.
    
    Args:
        model_key: Model identifier
    
    Returns:
        True if model supports video, False otherwise
    """
    try:
        config = get_model_config(model_key)
        return config.get('supports_video', False)
    except ValueError:
        return False


def get_video_clip_url(moment_id: str, video_filename: str) -> str:
    """
    Get the full URL for a video clip.
    
    Args:
        moment_id: Unique identifier for the moment
        video_filename: Original video filename (e.g., "ProjectUpdateVideo.mp4")
    
    Returns:
        Full URL for the video clip
    """
    video_stem = Path(video_filename).stem
    clip_filename = f"{video_stem}_{moment_id}_clip.mp4"
    config = get_video_server_config()
    return f"{config['base_url']}/{config['clips_path']}/{clip_filename}"


def get_duration_tolerance() -> float:
    """
    Get the tolerance for transcript-video duration matching.
    
    Returns:
        Tolerance in seconds
    """
    return VIDEO_SERVER_CONFIG['duration_tolerance']


