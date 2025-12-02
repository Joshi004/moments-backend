"""
Model configuration for AI models used in moment generation and refinement.
"""

# Configuration for video clipping and transcript extraction
CLIPPING_CONFIG = {
    "padding": 30.0,  # Single padding value used for both left and right (seconds)
    "margin": 2.0     # Allow extra margin when finding word boundaries (seconds)
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


