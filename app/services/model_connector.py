"""
Model service URL resolver.

Returns the correct HTTP endpoint for a given model key by reading
host and port from the model's Redis config. The application only
ever makes direct HTTP calls — how the host:port becomes reachable
(VPN, tunnel, Docker network) is an external infrastructure concern.
"""
import logging

from app.utils.model_config import get_model_config

logger = logging.getLogger(__name__)


async def get_service_url(model_key: str) -> str:
    """
    Return the API URL for a model using its host and port from Redis config.

    Parakeet uses /transcribe; all other models use /v1/chat/completions.

    Args:
        model_key: Model identifier, e.g. "minimax", "qwen3_vl_fp8", "parakeet"

    Returns:
        Full URL string for the model's API endpoint
    """
    config = await get_model_config(model_key)
    api_path = "/transcribe" if model_key == "parakeet" else "/v1/chat/completions"
    url = f"http://{config['host']}:{config['port']}{api_path}"
    logger.info(f"Service URL for '{model_key}': {url}")
    return url
