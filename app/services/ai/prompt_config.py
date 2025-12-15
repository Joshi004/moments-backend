"""
Model-specific prompting configurations for optimal JSON output.

This module provides model-specific configurations to ensure each AI model
returns pure JSON output without chain-of-thought reasoning or explanatory text.
"""

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

"""
}

# Model-specific configurations
MODEL_CONFIGS = {
    "qwen3_vl_fp8": {
        "json_header": JSON_HEADERS["qwen_json_array"],
        "json_footer": "",  # Qwen ignores footer instructions
        "use_response_format_param": False,  # Disabled: response_format forces json_object, but we need json_array
        "response_format_type": None,
        "header_priority": "top",  # Must be at very top
    },
    "qwen": {
        "json_header": JSON_HEADERS["qwen_json_array"],
        "json_footer": "",
        "use_response_format_param": False,  # Disabled: response_format forces json_object, but we need json_array
        "response_format_type": None,
        "header_priority": "top",
    },
    "qwen3_omni": {
        "json_header": JSON_HEADERS["qwen_json_array"],
        "json_footer": "",
        "use_response_format_param": False,  # Disabled: response_format forces json_object, but we need json_array
        "response_format_type": None,
        "header_priority": "top",
    },
    "minimax": {
        "json_header": JSON_HEADERS["standard"],
        "json_footer": "\n\nRemember: Output ONLY the JSON array, nothing else.",
        "use_response_format_param": False,  # Test if MiniMax supports this
        "response_format_type": None,
        "header_priority": "normal",
    },
}


def get_model_prompt_config(model_key: str) -> dict:
    """
    Get prompt configuration for a specific model.
    
    Args:
        model_key: Model identifier (e.g., 'qwen3_vl_fp8', 'minimax')
        
    Returns:
        Dictionary with prompt configuration including:
        - json_header: Header to prepend to prompts
        - json_footer: Footer to append to prompts
        - use_response_format_param: Whether to use response_format API parameter
        - response_format_type: Type of response format to use
        - header_priority: Where to place the JSON header ('top' or 'normal')
    """
    return MODEL_CONFIGS.get(model_key, {
        "json_header": JSON_HEADERS["standard"],
        "json_footer": "\n\nOutput ONLY valid JSON.",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "normal",
    })


def should_use_response_format(model_key: str) -> bool:
    """
    Check if model supports response_format parameter.
    
    Args:
        model_key: Model identifier
        
    Returns:
        True if model supports response_format parameter, False otherwise
    """
    config = get_model_prompt_config(model_key)
    return config.get("use_response_format_param", False)


def get_response_format_param(model_key: str) -> dict:
    """
    Get response_format parameter for API call.
    
    Args:
        model_key: Model identifier
    
    Returns:
        Dictionary with response format configuration, or None if not supported
        Example: {"type": "json_object"}
    """
    config = get_model_prompt_config(model_key)
    if config.get("use_response_format_param"):
        format_type = config.get("response_format_type", "json_object")
        return {"type": format_type}
    return None


# Refinement-specific configurations (for JSON object output)
REFINEMENT_MODEL_CONFIGS = {
    "qwen3_vl_fp8": {
        "json_header": JSON_HEADERS["qwen_json_object"],
        "json_footer": "",  # Qwen ignores footer instructions
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "top",  # Must be at very top
    },
    "qwen": {
        "json_header": JSON_HEADERS["qwen_json_object"],
        "json_footer": "",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "top",
    },
    "qwen3_omni": {
        "json_header": JSON_HEADERS["qwen_json_object"],
        "json_footer": "",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "top",
    },
    "minimax": {
        "json_header": JSON_HEADERS["standard"],
        "json_footer": "\n\nRemember: Output ONLY the JSON object with start_time and end_time, nothing else.",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "normal",
    },
}


def get_refinement_prompt_config(model_key: str) -> dict:
    """
    Get prompt configuration for moment refinement tasks.
    This is specifically for refinement which requires JSON object output (not array).
    
    Args:
        model_key: Model identifier (e.g., 'qwen3_vl_fp8', 'minimax')
        
    Returns:
        Dictionary with prompt configuration including:
        - json_header: Header to prepend to prompts (enforces JSON object)
        - json_footer: Footer to append to prompts
        - use_response_format_param: Whether to use response_format API parameter
        - response_format_type: Type of response format to use
        - header_priority: Where to place the JSON header ('top' or 'normal')
    """
    return REFINEMENT_MODEL_CONFIGS.get(model_key, {
        "json_header": JSON_HEADERS["standard"],
        "json_footer": "\n\nOutput ONLY valid JSON object with start_time and end_time.",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "normal",
    })

