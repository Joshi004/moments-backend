"""
AI services for Video Moments application.
Handles AI model interactions, prompt building, and moment generation/refinement.
"""

# AI clients
from app.services.ai.tunnel_manager import TunnelManager
from app.services.ai.base_client import BaseAIClient
from app.services.ai.minimax_client import MinimaxClient
from app.services.ai.qwen_client import QwenClient
from app.services.ai.qwen3_omni_client import Qwen3OmniClient
from app.services.ai.qwen3_vl_client import Qwen3VLClient

# Prompt tasks (Strategy + Builder pattern)
from app.services.ai.prompt_tasks import (
    BasePromptTask,
    GenerationTask,
    RefinementTask,
    get_model_config,
    get_response_format_param,
    extract_model_name,
    strip_think_tags,
)

# AI orchestration services
from app.services.ai.generation_service import (
    ssh_tunnel,
    call_ai_model,
    call_ai_model_async
)

from app.services.ai.refinement_service import (
    process_moment_refinement
)

# Request logging
from app.services.ai.request_logger import log_ai_request_response

__all__ = [
    # AI clients
    "TunnelManager",
    "BaseAIClient",
    "MinimaxClient",
    "QwenClient",
    "Qwen3OmniClient",
    "Qwen3VLClient",
    
    # Prompt tasks
    "BasePromptTask",
    "GenerationTask",
    "RefinementTask",
    "get_model_config",
    "get_response_format_param",
    "extract_model_name",
    "strip_think_tags",
    
    # Generation service
    "ssh_tunnel",
    "call_ai_model",
    "call_ai_model_async",
    
    # Refinement service
    "process_moment_refinement",
    
    # Request logging
    "log_ai_request_response",
]

