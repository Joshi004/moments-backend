"""
Generation task for identifying moments in videos.

This module implements the prompt building and response parsing logic
for the moment generation task.
"""
import json
import re
import time
import logging
from typing import Dict, List, Optional

from app.services.ai.prompt_tasks.base import BasePromptTask
from app.services.ai.prompt_tasks.sections import (
    PromptSection,
    INPUT_FORMAT_SEGMENTS_TEMPLATE,
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


class GenerationTask(BasePromptTask):
    """
    Task for generating moments from video transcripts.
    
    This task outputs a JSON array of moments, each with start_time, end_time, and title.
    """
    
    def get_output_type(self) -> str:
        """Returns 'array' since generation outputs a list of moments."""
        return "array"
    
    def get_sections(self) -> List[PromptSection]:
        """Define the ordered sections for generation prompts."""
        return [
            PromptSection.JSON_HEADER,
            PromptSection.USER_PROMPT,
            PromptSection.INPUT_FORMAT,
            PromptSection.DATA,
            PromptSection.OUTPUT_FORMAT,
            PromptSection.CONSTRAINTS,
            PromptSection.JSON_FOOTER,
        ]
    
    def render_section(self, section: PromptSection, context: Dict) -> Optional[str]:
        """
        Render a specific section for generation prompt.
        
        Required context keys:
            - user_prompt: str - User's custom prompt
            - segments: List[Dict] - Transcript segments with 'start' and 'text'
            - video_duration: float - Total video duration
            - min_moment_length: float - Minimum moment length
            - max_moment_length: float - Maximum moment length
            - min_moments: int - Minimum number of moments
            - max_moments: int - Maximum number of moments
        """
        if section == PromptSection.USER_PROMPT:
            return context.get("user_prompt", "")
        
        elif section == PromptSection.INPUT_FORMAT:
            return INPUT_FORMAT_SEGMENTS_TEMPLATE
        
        elif section == PromptSection.DATA:
            segments = context.get("segments", [])
            # Format segments as [timestamp] text
            segments_text = "\n".join([
                f"[{seg['start']:.2f}] {seg['text']}"
                for seg in segments
            ])
            return f"Transcript segments:\n{segments_text}"
        
        elif section == PromptSection.OUTPUT_FORMAT:
            return get_output_format_template("array")
        
        elif section == PromptSection.CONSTRAINTS:
            video_duration = context.get("video_duration", 0.0)
            min_moment_length = context.get("min_moment_length", 30.0)
            max_moment_length = context.get("max_moment_length", 90.0)
            min_moments = context.get("min_moments", 1)
            max_moments = context.get("max_moments", 10)
            
            return f"""CONSTRAINTS:
- Video duration: {video_duration:.2f} seconds
- Moment length: Between {min_moment_length:.2f} and {max_moment_length:.2f} seconds
- Number of moments: Between {min_moments} and {max_moments}
- All moments must be non-overlapping
- All start_time values must be >= 0
- All end_time values must be <= {video_duration:.2f}
- Each moment's end_time must be > start_time"""
        
        return None
    
    def parse_response(self, response: Dict) -> List[Dict]:
        """
        Parse AI model response to extract moments.
        
        Args:
            response: Dictionary containing AI model response
        
        Returns:
            List of moment dictionaries with start_time, end_time, and title
        
        Raises:
            ValueError: If response cannot be parsed or is invalid
        """
        operation = "parse_moments_response"
        start_time = time.time()
        
        log_operation_start(
            logger="app.services.ai.prompt_tasks.generation",
            function="parse_response",
            operation=operation,
            message="Parsing AI model response to extract moments",
            context={
                "response_keys": list(response.keys()) if isinstance(response, dict) else None,
                "request_id": get_request_id()
            }
        )
        
        try:
            # Log the full response structure for debugging
            log_event(
                level="DEBUG",
                logger="app.services.ai.prompt_tasks.generation",
                function="parse_response",
                operation=operation,
                event="parse_start",
                message="Full AI response structure",
                context={"response_preview": json.dumps(response, indent=2)[:2000]}
            )
            
            # Extract content from response
            if 'choices' not in response or len(response['choices']) == 0:
                log_event(
                    level="ERROR",
                    logger="app.services.ai.prompt_tasks.generation",
                    function="parse_response",
                    operation=operation,
                    event="parse_error",
                    message="No choices in response",
                    context={"response_keys": list(response.keys())}
                )
                raise ValueError("No choices in response")
            
            content = response['choices'][0].get('message', {}).get('content', '')
            if not content:
                log_event(
                    level="ERROR",
                    logger="app.services.ai.prompt_tasks.generation",
                    function="parse_response",
                    operation=operation,
                    event="parse_error",
                    message="No content in response",
                    context={"choices_structure": response['choices'][0]}
                )
                raise ValueError("No content in response")
            
            log_event(
                level="DEBUG",
                logger="app.services.ai.prompt_tasks.generation",
                function="parse_response",
                operation=operation,
                event="parse_start",
                message="Extracted content from response",
                context={
                    "content_length": len(content),
                    "content_preview": content[:500]
                }
            )
            
            # Strip think tags before processing
            content = strip_think_tags(content)
            
            log_event(
                level="DEBUG",
                logger="app.services.ai.prompt_tasks.generation",
                function="parse_response",
                operation=operation,
                event="parse_start",
                message="Think tags stripped",
                context={
                    "content_length_after": len(content),
                    "content_preview": content[:300]
                }
            )
            
            # Extract JSON from content
            json_str = extract_json_from_markdown(content)
            
            if not json_str:
                logger.error("Content is empty after stripping")
                raise ValueError("Empty content in response")
            
            logger.debug(f"Attempting to parse JSON: {json_str[:500]}")
            
            # Parse JSON - try full parse first
            try:
                parsed_data = json.loads(json_str)
            except json.JSONDecodeError as e:
                # If JSON is malformed, try to extract moments from partial JSON
                logger.warning(f"JSON parse error: {str(e)}. Attempting to extract moments from partial/truncated JSON...")
                parsed_data = self._extract_moments_from_partial_json(json_str)
            
            # Handle case where model returns an object instead of array
            if isinstance(parsed_data, dict):
                moments = self._extract_array_from_object(parsed_data)
            elif isinstance(parsed_data, list):
                moments = parsed_data
            else:
                raise ValueError(f"Response is not a list or object, got {type(parsed_data).__name__}")
            
            # Validate each moment has required fields
            validated_moments = self._validate_moments(moments)
            
            duration = time.time() - start_time
            
            log_operation_complete(
                logger="app.services.ai.prompt_tasks.generation",
                function="parse_response",
                operation=operation,
                message="Successfully parsed moments from response",
                context={"moment_count": len(validated_moments)},
                duration=duration
            )
            
            return validated_moments
            
        except json.JSONDecodeError as e:
            duration = time.time() - start_time
            json_str_preview = json_str[:1000] if 'json_str' in locals() else 'N/A'
            log_event(
                level="ERROR",
                logger="app.services.ai.prompt_tasks.generation",
                function="parse_response",
                operation=operation,
                event="parse_error",
                message="Error parsing JSON from response",
                context={
                    "error": str(e),
                    "json_string_preview": json_str_preview,
                    "duration_seconds": duration
                }
            )
            raise ValueError(f"Invalid JSON in response: {str(e)}")
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error parsing moments response: {str(e)}")
            raise ValueError(f"Error parsing response: {str(e)}")
    
    def _extract_moments_from_partial_json(self, json_str: str) -> List[Dict]:
        """
        Extract moments from partial or truncated JSON.
        
        Args:
            json_str: Potentially malformed JSON string
        
        Returns:
            List of extracted moment objects
        
        Raises:
            ValueError: If no valid moments can be extracted
        """
        # Try to find arrays in common field names
        moments_pattern = r'"moments"\s*:\s*(\[[^\]]*(?:\{[^\}]*"start_time"[^\}]*"end_time"[^\}]*"title"[^\}]*\}[^\]]*)*\])'
        moments_match = re.search(moments_pattern, json_str, re.DOTALL)
        
        if not moments_match:
            # Try other common field names
            for field_name in ["output", "final_output", "response", "final_json_output", "json_output"]:
                field_pattern = f'"{field_name}"\\s*:\\s*(\\[[^\\]]*(?:\\{{[^\\}}]*"start_time"[^\\}}]*"end_time"[^\\}}]*"title"[^\\}}]*\\}}[^\\]]*)*\\])'
                moments_match = re.search(field_pattern, json_str, re.DOTALL)
                if moments_match:
                    logger.info(f"Found moments array in field '{field_name}'")
                    break
        
        if moments_match:
            try:
                array_str = moments_match.group(1)
                return json.loads(array_str)
            except json.JSONDecodeError:
                pass
        
        # Try simple bracket matching
        first_bracket = json_str.find('[')
        last_bracket = json_str.rfind(']')
        if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
            try:
                array_str = json_str[first_bracket:last_bracket+1]
                return json.loads(array_str)
            except json.JSONDecodeError:
                pass
        
        # Last resort: extract complete moment objects
        moment_objects = []
        moment_pattern = r'\{\s*"start_time"\s*:\s*[\d.]+\s*,\s*"end_time"\s*:\s*[\d.]+\s*,\s*"title"\s*:\s*"[^"]*"\s*\}'
        for match in re.finditer(moment_pattern, json_str):
            try:
                moment_obj = json.loads(match.group(0))
                moment_objects.append(moment_obj)
            except:
                pass
        
        if moment_objects:
            logger.info(f"Extracted {len(moment_objects)} complete moment objects from truncated JSON")
            return moment_objects
        
        raise ValueError("Could not extract valid moments array from partial JSON")
    
    def _extract_array_from_object(self, parsed_data: Dict) -> List[Dict]:
        """
        Extract moments array from a JSON object response.
        
        Args:
            parsed_data: Parsed JSON object
        
        Returns:
            List of moments
        
        Raises:
            ValueError: If no moments array found in object
        """
        logger.warning("Model returned a JSON object instead of array. Attempting to extract array from common fields...")
        
        # Try common field names that might contain the moments array
        possible_fields = ["moments", "output", "final_output", "response", "final_json", 
                          "json_output", "final_json_output", "final", "final_output"]
        
        for field in possible_fields:
            if field in parsed_data and isinstance(parsed_data[field], list):
                logger.info(f"Found moments array in field '{field}'")
                return parsed_data[field]
        
        # Try to find any list field
        for key, value in parsed_data.items():
            if isinstance(value, list) and len(value) > 0:
                # Check if it looks like moments (has objects with start_time/end_time)
                if isinstance(value[0], dict) and 'start_time' in value[0]:
                    logger.info(f"Found moments array in field '{key}'")
                    return value
        
        raise ValueError("Response is a JSON object but no moments array found in common fields")
    
    def _validate_moments(self, moments: List) -> List[Dict]:
        """
        Validate that moments have required fields and correct types.
        
        Args:
            moments: List of moment objects
        
        Returns:
            List of validated moment dictionaries
        """
        validated_moments = []
        for i, moment in enumerate(moments):
            if not isinstance(moment, dict):
                logger.warning(f"Moment {i} is not a dictionary, skipping")
                continue
            
            if 'start_time' not in moment or 'end_time' not in moment or 'title' not in moment:
                logger.warning(f"Moment {i} missing required fields, skipping")
                continue
            
            try:
                validated_moments.append({
                    'start_time': float(moment['start_time']),
                    'end_time': float(moment['end_time']),
                    'title': str(moment['title']).strip()
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"Moment {i} has invalid types: {e}, skipping")
                continue
        
        return validated_moments
