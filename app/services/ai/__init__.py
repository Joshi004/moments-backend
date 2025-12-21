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

# Prompt utilities
from app.services.ai.prompt_builder import PromptBuilder

# AI orchestration services
from app.services.ai.generation_service import (
    process_moments_generation_async,
    strip_think_tags,
    ssh_tunnel,
    call_ai_model
)

from app.services.ai.refinement_service import (
    process_moment_refinement_async
)

# Prompt configuration
from app.services.ai.prompt_config import (
    get_model_prompt_config,
    get_refinement_prompt_config,
    get_response_format_param
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
    
    # Prompt utilities
    "PromptBuilder",
    
    # Generation service
    "process_moments_generation_async",
    "strip_think_tags",
    "ssh_tunnel",
    "call_ai_model",
    
    # Refinement service
    "process_moment_refinement_async",
    
    # Prompt configuration
    "get_model_prompt_config",
    "get_refinement_prompt_config",
    "get_response_format_param",
    
    # Request logging
    "log_ai_request_response",
]

