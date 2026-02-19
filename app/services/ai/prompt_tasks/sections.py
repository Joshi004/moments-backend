"""
Prompt section definitions and templates.

This module defines all possible sections that can be included in prompts
and provides reusable templates for common sections.
"""
from enum import Enum


class PromptSection(Enum):
    """Enumeration of all possible prompt sections."""
    JSON_HEADER = "json_header"
    VIDEO_CONTEXT = "video_context"
    TASK_CONTEXT = "task_context"
    USER_PROMPT = "user_prompt"
    INPUT_FORMAT = "input_format"
    DATA = "data"
    OUTPUT_FORMAT = "output_format"
    CONSTRAINTS = "constraints"
    JSON_FOOTER = "json_footer"


# Input format templates
INPUT_FORMAT_SEGMENTS_TEMPLATE = """INPUT FORMAT:
The transcript is provided as a series of segments. Each segment has:
- A timestamp (in seconds) indicating when that segment starts in the video
- The text content spoken during that segment

Format: [timestamp_in_seconds] text_content

Example:
[0.24] You know, rather than be scared by a jobless future
[2.56] I started to rethink it and I said
[5.12] I could really be excited by a jobless future"""


INPUT_FORMAT_WORDS_TEMPLATE = """INPUT FORMAT:
You are provided with word-level timestamps. Each line shows:
- The start and end time of a specific word in seconds (starting from 0.00)
- The word itself

Format: [start_time-end_time] word

Example:
[5.12-5.48] rather
[5.48-5.76] than
[5.76-5.92] be
[5.92-6.24] scared

The first word in the transcript starts at or near 0.00 seconds."""


# Output format templates
OUTPUT_FORMAT_ARRAY_TEMPLATE = """OUTPUT FORMAT - CRITICAL - READ CAREFULLY:

You MUST respond with ONLY a valid JSON array. Nothing else. No exceptions.

CRITICAL REQUIREMENTS - VIOLATION WILL CAUSE REQUEST FAILURE:
- Your response MUST start with [ and MUST end with ]
- Do NOT output a JSON object { } - ONLY an array [ ]
- Do NOT wrap the array in an object
- Do NOT include ANY other fields like "transcript", "analysis", "validation", "output", "notes", "rules", "final_output", etc.
- Do NOT repeat the same data multiple times
- Do NOT include any thinking, reasoning, or explanation
- NO text before the [
- NO text after the ]
- NO markdown code blocks (no ```json or ```)
- NO comments or notes

REQUIRED STRUCTURE (this is ALL you should output - nothing more, nothing less):
[
  {
    "start_time": 0.24,
    "end_time": 15.5,
    "title": "Introduction to jobless future concept"
  },
  {
    "start_time": 45.2,
    "end_time": 78.8,
    "title": "Discussion about human potential"
  }
]

RULES:
- Each object needs exactly 3 fields: start_time (float), end_time (float), title (string)
- Do not add any other fields to the objects
- Do not add any fields outside the array

FINAL REMINDER: Output ONLY the JSON array [ ... ]. Nothing else."""


OUTPUT_FORMAT_OBJECT_TEMPLATE = """OUTPUT FORMAT - CRITICAL - READ CAREFULLY:

You MUST respond with ONLY a valid JSON object. Nothing else. No exceptions.

CRITICAL REQUIREMENTS - VIOLATION WILL CAUSE REQUEST FAILURE:
- Your response MUST start with { and MUST end with }
- Do NOT output a JSON array [ ] - ONLY an object { }
- Do NOT wrap the object in an array
- Do NOT include ANY other fields like "transcript", "analysis", "validation", "output", "notes", "rules", etc.
- Do NOT include any thinking, reasoning, or explanation
- NO text before the {
- NO text after the }
- NO markdown code blocks (no ```json or ```)
- NO comments or notes

REQUIRED STRUCTURE (this is ALL you should output - nothing more, nothing less):
{
  "start_time": 5.12,
  "end_time": 67.84
}

RULES:
- Must have exactly 2 fields: start_time (float), end_time (float)
- Timestamps must be in the normalized coordinate system (starting from 0.00)
- The start_time and end_time must correspond to word boundaries from the provided transcript
- The start_time must be >= 0.00 and < end_time
- Do not add any other fields

FINAL REMINDER: Output ONLY the JSON object { ... }. Nothing else."""


def get_output_format_template(output_type: str, **kwargs) -> str:
    """
    Get output format template for a specific output type.
    
    Args:
        output_type: Either 'array' or 'object'
        **kwargs: Additional parameters to format the template
            - clip_end: For object templates, maximum end_time
    
    Returns:
        Formatted output format template string
    """
    if output_type == "array":
        return OUTPUT_FORMAT_ARRAY_TEMPLATE
    elif output_type == "object":
        template = OUTPUT_FORMAT_OBJECT_TEMPLATE
        if "clip_end" in kwargs:
            # Add clip_end constraint to the template
            template = template.replace(
                "- Do not add any other fields",
                f"- The end_time must be <= {kwargs['clip_end']:.2f}\n- Do not add any other fields"
            )
        return template
    else:
        raise ValueError(f"Unknown output type: {output_type}")
