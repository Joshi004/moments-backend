"""
Shared utilities for prompt tasks.

This module contains common utility functions used across different prompt tasks,
including response cleaning, JSON extraction, and model name extraction.
"""
import re
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def strip_think_tags(content: str) -> str:
    """
    Remove <think>...</think> tags and their content from AI model responses.
    
    Some AI models wrap their reasoning process in <think> tags, which need to be
    stripped before parsing JSON responses.
    
    Args:
        content: Raw content string from AI model response
        
    Returns:
        Content with think tags removed and stripped
    """
    logger.debug(f"strip_think_tags called with content length: {len(content) if isinstance(content, str) else 'N/A'}")
    
    if not isinstance(content, str):
        logger.warning(f"strip_think_tags: content is not a string, type: {type(content)}")
        return content
    
    # Log content preview before processing
    content_preview = content[:300] if len(content) > 300 else content
    logger.debug(f"strip_think_tags: Content before processing (first 300 chars): {content_preview}")
    
    # Pattern to match <think>...</think> tags (non-greedy, handles multiline with DOTALL)
    # Also handle variations like <think>, <thinking>, etc.
    think_pattern = r'<think[^>]*>.*?</think>'
    logger.debug(f"strip_think_tags: Using pattern: {think_pattern}")
    
    # Count occurrences before removal for logging
    matches = re.findall(think_pattern, content, re.DOTALL | re.IGNORECASE)
    logger.info(f"strip_think_tags: Found {len(matches)} think tag block(s) using pattern")
    
    if matches:
        # Log what was matched
        for i, match in enumerate(matches[:3]):  # Log first 3 matches
            match_preview = match[:200] if len(match) > 200 else match
            logger.debug(f"strip_think_tags: Match {i+1} preview (first 200 chars): {match_preview}")
        
        if len(matches) > 3:
            logger.debug(f"strip_think_tags: ... and {len(matches) - 3} more matches")
        
        logger.info(f"strip_think_tags: Stripping {len(matches)} think tag block(s) from content")
        content_length_before = len(content)
        content = re.sub(think_pattern, '', content, flags=re.DOTALL | re.IGNORECASE)
        # Strip extra whitespace that may have been left behind
        content = content.strip()
        
        # Log result after stripping
        content_after_preview = content[:300] if len(content) > 300 else content
        logger.info(f"strip_think_tags: Content after stripping (first 300 chars): {content_after_preview}")
        logger.debug(f"strip_think_tags: Content length changed from {content_length_before} to {len(content)} chars")
    else:
        logger.warning(f"strip_think_tags: No think tags found with pattern '{think_pattern}'")
        # Try to find what tags ARE in the content
        opening_tags = re.findall(r'<([^/>\s]+)[^>]*>', content)
        closing_tags = re.findall(r'</([^/>\s]+)>', content)
        if opening_tags or closing_tags:
            logger.debug(f"strip_think_tags: Found opening tags: {set(opening_tags)}")
            logger.debug(f"strip_think_tags: Found closing tags: {set(closing_tags)}")
        else:
            logger.debug(f"strip_think_tags: No XML/HTML-like tags found in content")
    
    return content


def extract_model_name(response: Dict) -> str:
    """
    Extract model name from AI API response.
    
    Args:
        response: Dictionary containing AI model response
    
    Returns:
        Model name string, or "Unknown Model" if not available
    """
    if not isinstance(response, dict):
        return "Unknown Model"
    
    model_name = response.get('model', 'Unknown Model')
    if not model_name or model_name == '':
        return "Unknown Model"
    
    return str(model_name)


def extract_json_from_markdown(content: str) -> str:
    """
    Extract JSON content from markdown code blocks.
    
    Args:
        content: Content string that may contain markdown code blocks
        
    Returns:
        Extracted JSON string without markdown formatting
    """
    json_str = content.strip()
    
    # Remove markdown code blocks if present
    if json_str.startswith('```'):
        # Extract content between ```json and ```
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', json_str, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
            logger.info("Extracted JSON from markdown code block")
        else:
            logger.warning("Content starts with ``` but no closing ``` found")
    
    return json_str


def find_json_in_text(text: str, expected_type: str = "object") -> Optional[str]:
    """
    Find and extract JSON from text that may contain other content.
    
    Args:
        text: Text that may contain JSON
        expected_type: Either "object" or "array"
        
    Returns:
        Extracted JSON string, or None if not found
    """
    if expected_type == "object":
        start_char, end_char = '{', '}'
    elif expected_type == "array":
        start_char, end_char = '[', ']'
    else:
        raise ValueError(f"expected_type must be 'object' or 'array', got: {expected_type}")
    
    # Find first occurrence of start character
    first_start = text.find(start_char)
    if first_start == -1:
        return None
    
    # Find matching closing character (handle nesting)
    depth = 0
    for i in range(first_start, len(text)):
        if text[i] == start_char:
            depth += 1
        elif text[i] == end_char:
            depth -= 1
            if depth == 0:
                # Found matching closing character
                return text[first_start:i+1]
    
    # No matching closing character found
    return None


def validate_json_structure(json_str: str, expected_type: str) -> bool:
    """
    Validate that a JSON string has the expected structure.
    
    Args:
        json_str: JSON string to validate
        expected_type: Either "object" or "array"
        
    Returns:
        True if structure matches expected type
    """
    json_str_trimmed = json_str.strip()
    
    if expected_type == "object":
        return json_str_trimmed.startswith('{') and json_str_trimmed.endswith('}')
    elif expected_type == "array":
        return json_str_trimmed.startswith('[') and json_str_trimmed.endswith(']')
    else:
        return False


def safe_json_loads(json_str: str) -> Optional[Dict]:
    """
    Safely parse JSON string with error handling.
    
    Args:
        json_str: JSON string to parse
        
    Returns:
        Parsed JSON object, or None if parsing fails
    """
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {str(e)}")
        logger.error(f"JSON string that failed to parse (first 1000 chars): {json_str[:1000]}")
        return None
