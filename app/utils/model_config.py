"""
Model configuration for AI models used in moment generation and refinement.
"""

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
        "name": "Qwen",
        "model_id": "qwen3-vl-235b-thinking",
        "ssh_host": "naresh@85.234.64.44",  # Update if Qwen uses different SSH host
        "ssh_remote_host": "worker-9",  # Update if Qwen runs on different remote host
        "ssh_local_port": 7001,
        "ssh_remote_port": 7001,  # Update with actual remote port for Qwen service
    }
}


def get_model_config(model_key: str) -> dict:
    """
    Get configuration for a specific model.
    
    Args:
        model_key: Model identifier ("minimax" or "qwen")
    
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
        model_key: Model identifier ("minimax" or "qwen")
    
    Returns:
        URL string for the model API endpoint
    """
    config = get_model_config(model_key)
    return f"http://localhost:{config['ssh_local_port']}/v1/chat/completions"

