"""
Switchable connection mechanism for AI model services.

Supports two modes per model (configured in Redis via config_registry):
  - "tunnel": SSH tunnel to localhost (existing behavior, default)
  - "direct": straight HTTP call to the server by IP/port

New config keys required for direct mode: connection_mode, direct_host, direct_port
Tunnel mode works with existing config keys and requires no changes.
"""
from contextlib import asynccontextmanager
import logging

from app.utils.model_config import get_model_config

logger = logging.getLogger(__name__)


async def get_service_url(model_key: str) -> str:
    """
    Return the correct API URL for a model based on its connection_mode config.

    - direct mode: http://{direct_host}:{direct_port}{api_path}
    - tunnel mode: http://localhost:{ssh_local_port}{api_path}  (default / backward compat)

    Args:
        model_key: Model identifier, e.g. "minimax", "qwen3_vl_fp8", "parakeet"

    Returns:
        Full URL string for the model's API endpoint
    """
    config = await get_model_config(model_key)
    connection_mode = config.get("connection_mode") or "tunnel"
    api_path = "/transcribe" if model_key == "parakeet" else "/v1/chat/completions"

    if connection_mode == "direct":
        url = f"http://{config['direct_host']}:{config['direct_port']}{api_path}"
    else:
        url = f"http://localhost:{config['ssh_local_port']}{api_path}"

    logger.info(f"Service URL for '{model_key}' [{connection_mode}]: {url}")
    return url


@asynccontextmanager
async def connect(model_key: str):
    """
    Async context manager that establishes (or skips) a connection to a model service.

    - direct mode: no-op; yields None immediately with no setup or teardown
    - tunnel mode: delegates to the existing ssh_tunnel() context manager from
      the appropriate service module, yielding the tunnel process

    Args:
        model_key: Model identifier, e.g. "minimax", "qwen3_vl_fp8", "parakeet"

    Yields:
        subprocess.Popen tunnel process (tunnel mode) or None (direct mode)
    """
    config = await get_model_config(model_key)
    connection_mode = config.get("connection_mode") or "tunnel"

    if connection_mode == "direct":
        logger.info(f"Direct connection mode for '{model_key}' — no tunnel needed")
        yield None
        return

    # Tunnel mode: delegate to the existing ssh_tunnel() in the relevant service.
    # Imports are lazy to avoid circular imports and keep this module side-effect-free.
    if model_key == "parakeet":
        from app.services.transcript_service import ssh_tunnel
    else:
        from app.services.ai.generation_service import ssh_tunnel

    async with ssh_tunnel(model_key) as tunnel_process:
        yield tunnel_process
