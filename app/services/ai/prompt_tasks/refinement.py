"""
Refinement task for refining moment timestamps.

This module implements the prompt building and response parsing logic
for the moment refinement task.
"""
import json
import re
import time
import logging
from typing import Dict, List, Optional, Tuple

from app.services.ai.prompt_tasks.base import BasePromptTask
from app.services.ai.prompt_tasks.sections import (
    PromptSection,
    INPUT_FORMAT_WORDS_TEMPLATE,
    get_output_format_template,
)
from app.services.ai.prompt_tasks.utils import (
    strip_think_tags,
    extract_json_from_markdown,
    find_json_in_text,
)
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    get_request_id,
)

logger = logging.getLogger(__name__)


class RefinementTask(BasePromptTask):
    """
    Task for refining moment timestamps using word-level transcript data.
    
    This task outputs a JSON object with refined start_time and end_time.
    """
    
    def get_output_type(self) -> str:
        """Returns 'object' since refinement outputs a single timestamp pair."""
        return "object"
    
    def get_sections(self) -> List[PromptSection]:
        """Define the ordered sections for refinement prompts."""
        return [
            PromptSection.JSON_HEADER,
            PromptSection.VIDEO_CONTEXT,
            PromptSection.TASK_CONTEXT,
            PromptSection.USER_PROMPT,
            PromptSection.INPUT_FORMAT,
            PromptSection.DATA,
            PromptSection.OUTPUT_FORMAT,
            PromptSection.JSON_FOOTER,
        ]
    
    def render_section(self, section: PromptSection, context: Dict) -> Optional[str]:
        """
        Render a specific section for refinement prompt.
        
        Required context keys:
            - user_prompt: str - User's custom prompt
            - words: List[Dict] - Word-level timestamps with 'word', 'start', 'end'
            - clip_start: float - Normalized clip start (usually 0.0)
            - clip_end: float - Normalized clip end (duration)
            - original_start: float - Original moment start (normalized)
            - original_end: float - Original moment end (normalized)
            - original_title: str - Title of the moment being refined
            - include_video: bool - Whether video is included
            - video_clip_url: Optional[str] - URL of video clip
        """
        if section == PromptSection.VIDEO_CONTEXT:
            include_video = context.get("include_video", False)
            video_clip_url = context.get("video_clip_url")
            
            if not include_video or not video_clip_url:
                return None  # Skip this section if no video
            
            return """VIDEO INPUT:
A video clip is provided along with this request. The video clip is precisely aligned with the transcript below:
- The video starts at 0.00 seconds
- The transcript starts at 0.00 seconds
- Both are synchronized in the same normalized time coordinate system

IMPORTANT: Use the video frames to visually identify the exact moment boundaries. Look for:
- Visual cues that indicate the start of the topic/segment
- Scene changes, speaker changes, or visual transitions
- The exact frame where the engaging content begins and ends
- Correlation between what you see and what you hear in the transcript

Analyze BOTH the video frames and the word-level transcript to determine the most accurate timestamps. The timestamps you output should match this normalized coordinate system (starting from 0.00)."""
        
        elif section == PromptSection.TASK_CONTEXT:
            original_title = context.get("original_title", "Untitled")
            original_start = context.get("original_start", 0.0)
            original_end = context.get("original_end", 0.0)
            clip_end = context.get("clip_end", 0.0)
            include_video = context.get("include_video", False)
            
            return f"""TASK CONTEXT:
You are refining the timestamps for an existing video moment. The moment currently has the following information:
- Title: "{original_title}"
- Current start time: {original_start:.2f} seconds
- Current end time: {original_end:.2f} seconds

IMPORTANT - COORDINATE SYSTEM:
All timestamps (transcript, video, and the current moment times above) are in the SAME normalized coordinate system:
- The clip starts at 0.00 seconds
- The clip ends at {clip_end:.2f} seconds
- Both the transcript and{' video' if include_video else ' audio'} are aligned to this coordinate system
- Your output timestamps must also be in this coordinate system (0.00 to {clip_end:.2f})

The current timestamps may not be precisely aligned with where the content actually begins and ends. Your task is to analyze the word-level transcript{' and video' if include_video else ''} and determine the exact timestamps where this moment should start and end."""
        
        elif section == PromptSection.USER_PROMPT:
            return context.get("user_prompt", "")
        
        elif section == PromptSection.INPUT_FORMAT:
            return INPUT_FORMAT_WORDS_TEMPLATE
        
        elif section == PromptSection.DATA:
            words = context.get("words", [])
            # Format words as [start-end] word
            words_text = "\n".join([
                f"[{word['start']:.2f}-{word['end']:.2f}] {word['word']}"
                for word in words
            ])
            return f"Word-level transcript:\n{words_text}"
        
        elif section == PromptSection.OUTPUT_FORMAT:
            clip_end = context.get("clip_end", 0.0)
            return get_output_format_template("object", clip_end=clip_end)
        
        return None
    
    def parse_response(self, response: Dict) -> Tuple[float, float]:
        """
        Parse AI model response to extract refined timestamps.
        
        Args:
            response: Dictionary containing AI model response
        
        Returns:
            Tuple of (start_time, end_time)
        
        Raises:
            ValueError: If response cannot be parsed or is invalid
        """
        operation = "parse_refinement_response"
        start_time = time.time()
        
        log_operation_start(
            logger="app.services.ai.prompt_tasks.refinement",
            function="parse_response",
            operation=operation,
            message="Parsing refinement response",
            context={
                "response_keys": list(response.keys()) if isinstance(response, dict) else None,
                "request_id": get_request_id()
            }
        )
        
        try:
            # Validate response is not None and is a dictionary
            if response is None:
                logger.error("Response is None")
                raise ValueError("Response is None")
            
            if not isinstance(response, dict):
                logger.error(f"Response is not a dictionary. Type: {type(response)}, Value: {str(response)[:200]}")
                raise ValueError(f"Response is not a dictionary, got {type(response).__name__}")
            
            # Log the full response structure for debugging
            logger.debug(f"Full AI response structure: {json.dumps(response, indent=2)[:1000]}")
            
            # Check if response has error structure
            if 'error' in response:
                error_msg = response.get('error', {})
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get('message', str(error_msg))
                logger.error(f"Response contains error: {error_msg}")
                raise ValueError(f"AI model returned an error: {error_msg}")
            
            # Extract content from response
            if 'choices' not in response:
                logger.error(f"No 'choices' key in response. Response keys: {list(response.keys())}")
                logger.error(f"Response content: {json.dumps(response, indent=2)[:500]}")
                raise ValueError("No 'choices' key in response")
            
            if not isinstance(response['choices'], list) or len(response['choices']) == 0:
                logger.error(f"Choices is empty or not a list. Choices: {response.get('choices', 'N/A')}")
                raise ValueError("No choices in response")
            
            # Validate choice structure
            first_choice = response['choices'][0]
            if not isinstance(first_choice, dict):
                logger.error(f"First choice is not a dictionary. Type: {type(first_choice)}")
                raise ValueError("Invalid choice structure in response")
            
            if 'message' not in first_choice:
                logger.error(f"No 'message' key in choice. Choice keys: {list(first_choice.keys())}")
                raise ValueError("No 'message' key in choice")
            
            message = first_choice['message']
            if not isinstance(message, dict):
                logger.error(f"Message is not a dictionary. Type: {type(message)}")
                raise ValueError("Invalid message structure in response")
            
            content = message.get('content', '')
            if not content:
                logger.error(f"No content in response. Choices structure: {first_choice}")
                logger.error(f"Message structure: {message}")
                raise ValueError("No content in response")
            
            logger.info(f"Extracted content from response (length: {len(content)} chars)")
            logger.debug(f"Content preview: {content[:500]}")
            
            # Log the FULL extracted content string for debugging
            logger.info(f"=== FULL EXTRACTED CONTENT STRING (length: {len(content)} chars) ===")
            logger.info(content)
            logger.info("=== END OF FULL EXTRACTED CONTENT STRING ===")
            
            # Also log to structured JSON log
            log_event(
                level="INFO",
                logger="app.services.ai.prompt_tasks.refinement",
                function="parse_response",
                operation="parse_refinement_response",
                event="content_extracted",
                message="Full extracted content string from AI response",
                context={
                    "content_length": len(content),
                    "full_content": content
                }
            )
            
            # Validate content is a string
            if not isinstance(content, str):
                logger.error(f"Content is not a string. Type: {type(content)}, Value: {str(content)[:200]}")
                raise ValueError(f"Content is not a string, got {type(content).__name__}")
            
            # Strip think tags before processing
            logger.info("parse_response: About to call strip_think_tags")
            content = strip_think_tags(content)
            logger.info(f"parse_response: After strip_think_tags, content length: {len(content)} chars")
            logger.debug(f"parse_response: Content after strip_think_tags (first 300 chars): {content[:300]}")
            
            # Extract JSON from content
            json_str = extract_json_from_markdown(content)
            
            if not json_str:
                logger.error("Content is empty after stripping")
                logger.error(f"Original content (first 500 chars): {content[:500]}")
                raise ValueError("Empty content in response")
            
            # Additional validation: check if json_str looks like JSON
            json_str_trimmed = json_str.strip()
            if not (json_str_trimmed.startswith('{') and json_str_trimmed.endswith('}')):
                logger.warning(f"JSON string doesn't start with {{ and end with }}. Content: {json_str[:200]}")
                # Try to find JSON object in the string
                json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', json_str, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0).strip()
                    logger.info("Extracted JSON object from content")
                else:
                    logger.error(f"Could not find valid JSON object in content: {json_str[:500]}")
                    raise ValueError(f"Content does not appear to be valid JSON: {json_str[:200]}")
            
            logger.debug(f"Attempting to parse JSON (length: {len(json_str)} chars): {json_str[:500]}")
            
            # Parse JSON with better error handling
            try:
                result = json.loads(json_str)
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON decode error: {str(json_err)}")
                logger.error(f"JSON string that failed to parse (first 1000 chars): {json_str[:1000]}")
                logger.error(f"JSON string length: {len(json_str)}")
                # Try to provide more helpful error message
                if len(json_str) == 0:
                    raise ValueError("Empty JSON string in response")
                elif json_str.strip() == '':
                    raise ValueError("JSON string contains only whitespace")
                else:
                    raise ValueError(f"Invalid JSON in response: {str(json_err)}. Content preview: {json_str[:200]}")
            
            # Validate it's a dictionary with required fields
            if not isinstance(result, dict):
                raise ValueError("Response is not a dictionary")
            
            if 'start_time' not in result or 'end_time' not in result:
                raise ValueError("Response missing start_time or end_time fields")
            
            refined_start_time = float(result['start_time'])
            refined_end_time = float(result['end_time'])
            
            # Validate times
            if refined_end_time <= refined_start_time:
                raise ValueError(f"Invalid times: end_time ({refined_end_time}) must be > start_time ({refined_start_time})")
            
            duration = time.time() - start_time
            
            log_operation_complete(
                logger="app.services.ai.prompt_tasks.refinement",
                function="parse_response",
                operation=operation,
                message="Successfully parsed refinement timestamps",
                context={
                    "start_time": refined_start_time,
                    "end_time": refined_end_time
                },
                duration=duration
            )
            
            return refined_start_time, refined_end_time
            
        except ValueError as e:
            duration = time.time() - start_time
            log_event(
                level="ERROR",
                logger="app.services.ai.prompt_tasks.refinement",
                function="parse_response",
                operation=operation,
                event="parse_error",
                message="Validation error parsing refinement response",
                context={
                    "error": str(e),
                    "duration_seconds": duration
                }
            )
            raise
        except json.JSONDecodeError as e:
            duration = time.time() - start_time
            json_str_preview = json_str[:1000] if 'json_str' in locals() else 'N/A'
            log_event(
                level="ERROR",
                logger="app.services.ai.prompt_tasks.refinement",
                function="parse_response",
                operation=operation,
                event="parse_error",
                message="JSON decode error parsing refinement response",
                context={
                    "error": str(e),
                    "json_string_preview": json_str_preview,
                    "duration_seconds": duration
                }
            )
            raise ValueError(f"Invalid JSON in response: {str(e)}")
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error parsing refinement response: {str(e)}")
            raise ValueError(f"Error parsing response: {str(e)}")
