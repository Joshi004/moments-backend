"""
Prompt tasks module for Video Moments application.

This module implements the Strategy + Builder pattern for AI prompt generation
and response parsing. Each task (generation, refinement, etc.) is a concrete
strategy that defines its own structure while sharing common building logic.
"""

# Core components
from app.services.ai.prompt_tasks.base import BasePromptTask
from app.services.ai.prompt_tasks.sections import PromptSection
from app.services.ai.prompt_tasks.config import (
    ModelConfig,
    get_model_config,
    get_response_format_param,
    JSON_HEADERS,
)

# Concrete task implementations
from app.services.ai.prompt_tasks.generation import GenerationTask
from app.services.ai.prompt_tasks.refinement import RefinementTask

# Utilities
from app.services.ai.prompt_tasks.utils import (
    strip_think_tags,
    extract_model_name,
    extract_json_from_markdown,
    find_json_in_text,
    validate_json_structure,
    safe_json_loads,
)

__all__ = [
    # Core components
    "BasePromptTask",
    "PromptSection",
    "ModelConfig",
    "get_model_config",
    "get_response_format_param",
    "JSON_HEADERS",
    
    # Task implementations
    "GenerationTask",
    "RefinementTask",
    
    # Utilities
    "strip_think_tags",
    "extract_model_name",
    "extract_json_from_markdown",
    "find_json_in_text",
    "validate_json_structure",
    "safe_json_loads",
]
