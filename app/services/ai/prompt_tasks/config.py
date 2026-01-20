"""
Model-specific configurations for prompt generation.

This module consolidates all model configurations, including JSON headers,
footers, and other model-specific settings.
"""
from dataclasses import dataclass
from typing import Optional


# JSON enforcement headers for different model types
JSON_HEADERS = {
    "qwen_json_array": """CRITICAL OUTPUT REQUIREMENT - READ THIS FIRST:

You MUST output ONLY a JSON array. Nothing else. No exceptions.

REQUIREMENTS:
- Your response MUST start with [ and MUST end with ]
- Do NOT output ANY explanation, notes, thoughts, reasoning, validation, analysis, or commentary
- Do NOT output <think> tags, <think> tags, hidden chain-of-thought, or any text before or after the array
- Do NOT include transcript data, rules, validation, analysis, notes, or any other fields
- Do NOT repeat the same data multiple times
- Do NOT wrap the array in an object
- Your response must be ONLY: [ ... ] - nothing before, nothing after

If you need to think, think internally—but the output must ONLY be the JSON array.

CRITICAL: Output ONLY the JSON array. No wrapper object. No other fields. Just [ ... ].

""",
    
    "qwen_json_object": """CRITICAL OUTPUT REQUIREMENT - READ THIS FIRST:

You MUST output ONLY a JSON object. Nothing else. No exceptions.

REQUIREMENTS:
- Your response MUST start with { and MUST end with }
- Do NOT output ANY explanation, notes, thoughts, reasoning, validation, analysis, or commentary
- Do NOT output <think> tags, <think> tags, hidden chain-of-thought, or any text before or after the object
- Do NOT include transcript data, rules, validation, analysis, notes, or any other fields
- Do NOT repeat the same data multiple times
- Do NOT wrap the object in an array
- Your response must be ONLY: { ... } - nothing before, nothing after

If you need to think, think internally—but the output must ONLY be the JSON object.

CRITICAL: Output ONLY the JSON object. No wrapper array. No other fields. Just { ... }.

""",
    
    "standard": """IMPORTANT: You must respond with ONLY valid JSON. Do not include any explanations, notes, or text outside the JSON structure.

""",
    
    "strict": """OUTPUT FORMAT REQUIREMENT:
Your response must be ONLY valid JSON.
- NO explanations before or after
- NO comments or notes
- NO markdown formatting
- JUST the JSON array/object

""",
}


@dataclass
class ModelConfig:
    """Configuration for a specific model."""
    json_header: str
    json_footer: str
    header_priority: str  # "top" or "normal"
    use_response_format_param: bool = False
    response_format_type: Optional[str] = None


# Unified model configurations
# The output_type parameter determines which header to use
MODEL_CONFIGS = {
    "qwen3_vl_fp8": {
        "array": ModelConfig(
            json_header=JSON_HEADERS["qwen_json_array"],
            json_footer="",
            header_priority="top",
            use_response_format_param=False,
            response_format_type=None,
        ),
        "object": ModelConfig(
            json_header=JSON_HEADERS["qwen_json_object"],
            json_footer="",
            header_priority="top",
            use_response_format_param=False,
            response_format_type=None,
        ),
    },
    "qwen": {
        "array": ModelConfig(
            json_header=JSON_HEADERS["qwen_json_array"],
            json_footer="",
            header_priority="top",
            use_response_format_param=False,
            response_format_type=None,
        ),
        "object": ModelConfig(
            json_header=JSON_HEADERS["qwen_json_object"],
            json_footer="",
            header_priority="top",
            use_response_format_param=False,
            response_format_type=None,
        ),
    },
    "qwen3_omni": {
        "array": ModelConfig(
            json_header=JSON_HEADERS["qwen_json_array"],
            json_footer="",
            header_priority="top",
            use_response_format_param=False,
            response_format_type=None,
        ),
        "object": ModelConfig(
            json_header=JSON_HEADERS["qwen_json_object"],
            json_footer="",
            header_priority="top",
            use_response_format_param=False,
            response_format_type=None,
        ),
    },
    "minimax": {
        "array": ModelConfig(
            json_header=JSON_HEADERS["standard"],
            json_footer="\n\nRemember: Output ONLY the JSON array, nothing else.",
            header_priority="normal",
            use_response_format_param=False,
            response_format_type=None,
        ),
        "object": ModelConfig(
            json_header=JSON_HEADERS["standard"],
            json_footer="\n\nRemember: Output ONLY the JSON object with start_time and end_time, nothing else.",
            header_priority="normal",
            use_response_format_param=False,
            response_format_type=None,
        ),
    },
}


def get_model_config(model_key: str, output_type: str) -> ModelConfig:
    """
    Get configuration for a specific model and output type.
    
    Args:
        model_key: Model identifier (e.g., 'qwen3_vl_fp8', 'minimax')
        output_type: Output type ('array' or 'object')
        
    Returns:
        ModelConfig with appropriate settings
        
    Raises:
        ValueError: If output_type is not 'array' or 'object'
    """
    if output_type not in ("array", "object"):
        raise ValueError(f"output_type must be 'array' or 'object', got: {output_type}")
    
    # Get model-specific config or use default
    if model_key in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_key][output_type]
    
    # Default configuration for unknown models
    return ModelConfig(
        json_header=JSON_HEADERS["standard"],
        json_footer=f"\n\nOutput ONLY valid JSON {output_type}.",
        header_priority="normal",
        use_response_format_param=False,
        response_format_type=None,
    )


def get_response_format_param(model_key: str, output_type: str) -> Optional[dict]:
    """
    Get response_format parameter for API call if supported.
    
    Args:
        model_key: Model identifier
        output_type: Output type ('array' or 'object')
        
    Returns:
        Dictionary with response format configuration, or None if not supported
        Example: {"type": "json_object"}
    """
    config = get_model_config(model_key, output_type)
    if config.use_response_format_param and config.response_format_type:
        return {"type": config.response_format_type}
    return None
