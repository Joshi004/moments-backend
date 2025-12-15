"""
Model-specific prompt building and configuration for optimal JSON output.
"""
from typing import List, Dict, Optional

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

# Model-specific configurations for moment generation
GENERATION_MODEL_CONFIGS = {
    "qwen3_vl_fp8": {
        "json_header": JSON_HEADERS["qwen_json_array"],
        "json_footer": "",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "top",
    },
    "qwen": {
        "json_header": JSON_HEADERS["qwen_json_array"],
        "json_footer": "",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "top",
    },
    "qwen3_omni": {
        "json_header": JSON_HEADERS["qwen_json_array"],
        "json_footer": "",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "top",
    },
    "minimax": {
        "json_header": JSON_HEADERS["standard"],
        "json_footer": "\n\nRemember: Output ONLY the JSON array, nothing else.",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "normal",
    },
}

# Refinement-specific configurations (for JSON object output)
REFINEMENT_MODEL_CONFIGS = {
    "qwen3_vl_fp8": {
        "json_header": JSON_HEADERS["qwen_json_object"],
        "json_footer": "",
        "use_response_format_param": False,
        "response_format_type": None,
        "header_priority": "top",
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


class PromptBuilder:
    """Build model-specific prompts for moment operations."""
    
    @staticmethod
    def get_generation_config(model_key: str) -> Dict:
        """Get prompt configuration for moment generation."""
        return GENERATION_MODEL_CONFIGS.get(model_key, {
            "json_header": JSON_HEADERS["standard"],
            "json_footer": "\n\nOutput ONLY valid JSON.",
            "use_response_format_param": False,
            "response_format_type": None,
            "header_priority": "normal",
        })
    
    @staticmethod
    def get_refinement_config(model_key: str) -> Dict:
        """Get prompt configuration for moment refinement."""
        return REFINEMENT_MODEL_CONFIGS.get(model_key, {
            "json_header": JSON_HEADERS["standard"],
            "json_footer": "\n\nOutput ONLY valid JSON object with start_time and end_time.",
            "use_response_format_param": False,
            "response_format_type": None,
            "header_priority": "normal",
        })
    
    @staticmethod
    def build_generation_prompt(
        user_prompt: str,
        segments: List[Dict],
        video_duration: float,
        constraints: Dict,
        model_key: str
    ) -> str:
        """
        Build prompt for moment generation.
        
        Args:
            user_prompt: User's custom prompt (optional)
            segments: Transcript segments with timestamps
            video_duration: Total video duration in seconds
            constraints: Constraints (min_length, max_length, min_moments, max_moments)
            model_key: Model identifier
            
        Returns:
            Complete prompt string
        """
        config = PromptBuilder.get_generation_config(model_key)
        
        # Build prompt sections
        sections = []
        
        # Add JSON header at top for models that require it
        if config.get("header_priority") == "top":
            sections.append(config["json_header"])
        
        # Add user prompt if provided
        if user_prompt:
            sections.append(f"User request: {user_prompt}\n")
        
        # Add main instructions
        sections.append(f"""
Analyze this video transcript and identify {constraints.get('min_moments', 1)}-{constraints.get('max_moments', 10)} interesting moments.
Each moment should be between {constraints.get('min_moment_length', 60)} and {constraints.get('max_moment_length', 600)} seconds long.

VIDEO DURATION: {video_duration:.2f} seconds

TRANSCRIPT:
""")
        
        # Add transcript segments
        for segment in segments:
            sections.append(f"[{segment['start']:.2f}s - {segment['end']:.2f}s]: {segment['text']}")
        
        # Add JSON header for normal priority models
        if config.get("header_priority") == "normal":
            sections.append("\n" + config["json_header"])
        
        # Add output format instructions
        sections.append("""
Output a JSON array of moments with this exact structure:
[
  {
    "start_time": 10.5,
    "end_time": 45.8,
    "title": "Brief descriptive title"
  }
]

RULES:
- Each moment must have start_time, end_time, and title
- Times must be within 0 to """ + f"{video_duration:.2f}" + """ seconds
- Moments should not overlap
- Title should be concise and descriptive
""")
        
        # Add JSON footer
        if config["json_footer"]:
            sections.append(config["json_footer"])
        
        return "\n".join(sections)
    
    @staticmethod
    def build_refinement_prompt(
        user_prompt: str,
        words: List[Dict],
        moment: Dict,
        clip_start: float,
        clip_duration: float,
        model_key: str,
        include_video: bool = False
    ) -> str:
        """
        Build prompt for moment refinement.
        
        Args:
            user_prompt: User's refinement instructions
            words: Word-level timestamps from transcript
            moment: Original moment data
            clip_start: Start time of the clip (normalized to 0)
            clip_duration: Duration of the clip
            model_key: Model identifier
            include_video: Whether video is included in the request
            
        Returns:
            Complete prompt string
        """
        config = PromptBuilder.get_refinement_config(model_key)
        
        # Build prompt sections
        sections = []
        
        # Add JSON header at top for models that require it
        if config.get("header_priority") == "top":
            sections.append(config["json_header"])
        
        # Add user instructions
        if user_prompt:
            sections.append(f"User request: {user_prompt}\n")
        
        # Add main instructions
        sections.append(f"""
Refine the timing of this moment to be more precise.
Original moment: "{moment.get('title', 'Untitled')}"
Clip duration: {clip_duration:.2f} seconds (normalized timeline starts at 0)
""")
        
        if include_video:
            sections.append("Video clip is provided for visual context.\n")
        
        # Add word-level timestamps
        sections.append("WORD-LEVEL TRANSCRIPT (normalized timeline starting at 0):")
        for word in words:
            sections.append(f"[{word['start']:.2f}s - {word['end']:.2f}s]: {word['word']}")
        
        # Add JSON header for normal priority models
        if config.get("header_priority") == "normal":
            sections.append("\n" + config["json_header"])
        
        # Add output format instructions
        sections.append("""
Output a JSON object with refined timing:
{
  "start_time": 0.5,
  "end_time": 12.3
}

RULES:
- Times must be within 0 to """ + f"{clip_duration:.2f}" + """ seconds (normalized timeline)
- Align to word boundaries for precise cuts
- end_time must be greater than start_time
""")
        
        # Add JSON footer
        if config["json_footer"]:
            sections.append(config["json_footer"])
        
        return "\n".join(sections)
    
    @staticmethod
    def get_response_format_param(model_key: str, is_refinement: bool = False) -> Optional[Dict]:
        """
        Get response_format parameter for API call if supported.
        
        Args:
            model_key: Model identifier
            is_refinement: Whether this is a refinement task
            
        Returns:
            Dictionary with response format or None
        """
        config = (PromptBuilder.get_refinement_config(model_key) if is_refinement 
                 else PromptBuilder.get_generation_config(model_key))
        
        if config.get("use_response_format_param"):
            format_type = config.get("response_format_type", "json_object")
            return {"type": format_type}
        
        return None

