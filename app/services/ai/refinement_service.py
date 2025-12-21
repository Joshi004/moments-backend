import threading
import time
import json
import re
from typing import Optional, Dict, List, Tuple
import logging
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)
from app.services.ai.request_logger import log_ai_request_response
from app.services.ai.prompt_config import get_refinement_prompt_config
from app.utils.timestamp import calculate_padded_boundaries, extract_words_in_range, normalize_word_timestamps, denormalize_timestamp
from app.repositories.job_repository import JobRepository, JobType, JobStatus

logger = logging.getLogger(__name__)

# Job repository for distributed job tracking
job_repo = JobRepository()


# Removed get_refinement_job_key - handled by JobRepository


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
    # Note: Matching <think> tags as that's what the AI models actually return
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


def extract_word_timestamps_for_range(
    transcript: Dict,
    start: float,
    end: float,
    padding: float
) -> Tuple[List[Dict], float, float]:
    """
    Extract word-level timestamps within padded time range, aligned to word boundaries.
    
    Args:
        transcript: Dictionary containing transcript data with 'word_timestamps'
        start: Original moment start time in seconds
        end: Original moment end time in seconds
        padding: Seconds to pad before start and after end (single value for both sides)
        
    Returns:
        Tuple of (words, clip_start, clip_end) where:
        - words: List of word dictionaries with 'word', 'start', and 'end' fields
        - clip_start: Actual start time aligned to word boundary
        - clip_end: Actual end time aligned to word boundary
    """
    if not transcript or 'word_timestamps' not in transcript:
        logger.warning("Transcript does not contain word_timestamps")
        return [], max(0, start - padding), end + padding
    
    word_timestamps = transcript['word_timestamps']
    if not isinstance(word_timestamps, list):
        logger.warning("word_timestamps is not a list")
        return [], max(0, start - padding), end + padding
    
    # Use the common utility to calculate precise boundaries
    from app.utils.model_config import get_clipping_config
    config = get_clipping_config()
    margin = config.get('margin', 2.0)
    
    clip_start, clip_end = calculate_padded_boundaries(
        word_timestamps=word_timestamps,
        moment_start=start,
        moment_end=end,
        padding=padding,
        margin=margin
    )
    
    # Extract words within the calculated boundaries
    extracted_words = extract_words_in_range(
        word_timestamps=word_timestamps,
        start_time=clip_start,
        end_time=clip_end
    )
    
    logger.info(
        f"Extracted {len(extracted_words)} words from range "
        f"[{clip_start:.2f}s - {clip_end:.2f}s] with {padding:.1f}s padding"
    )
    
    return extracted_words, clip_start, clip_end


def build_refinement_prompt(
    user_prompt: str,
    words: List[Dict],
    clip_start: float,
    clip_end: float,
    original_start: float,
    original_end: float,
    original_title: str,
    model_key: str = "minimax",
    include_video: bool = False,
    video_clip_url: Optional[str] = None
) -> str:
    """
    Build the complete prompt for moment refinement.
    
    Args:
        user_prompt: User-provided prompt (editable, visible in UI)
        words: List of word dictionaries with 'word', 'start', and 'end' fields
        clip_start: Clip start time (0 if normalized, or actual clip_start if absolute)
        clip_end: Clip end time (duration if normalized, or actual clip_end if absolute)
        original_start: Original moment start time (absolute, for context)
        original_end: Original moment end time (absolute, for context)
        original_title: Title of the moment being refined
        model_key: Model identifier for model-specific prompting
        include_video: Whether video is included in this refinement request
        video_clip_url: URL of the video clip (if include_video is True)
        
    Returns:
        Complete prompt string with all sections assembled
    """
    # Get model-specific prompt configuration for refinement (requires JSON object, not array)
    prompt_config = get_refinement_prompt_config(model_key)
    json_header = prompt_config["json_header"]
    json_footer = prompt_config.get("json_footer", "")
    # Format words as [start-end] word
    words_text = "\n".join([
        f"[{word['start']:.2f}-{word['end']:.2f}] {word['word']}"
        for word in words
    ])
    
    # Video context section (only when video is included)
    video_context = ""
    if include_video and video_clip_url:
        video_context = f"""VIDEO INPUT:
A video clip is provided along with this request. The video clip is precisely aligned with the transcript below:
- The video starts at 0.00 seconds
- The transcript starts at 0.00 seconds
- Both are synchronized in the same normalized time coordinate system

IMPORTANT: Use the video frames to visually identify the exact moment boundaries. Look for:
- Visual cues that indicate the start of the topic/segment
- Scene changes, speaker changes, or visual transitions
- The exact frame where the engaging content begins and ends
- Correlation between what you see and what you hear in the transcript

Analyze BOTH the video frames and the word-level transcript to determine the most accurate timestamps. The timestamps you output should match this normalized coordinate system (starting from 0.00).

"""
    
    # Context explanation (backend-only, not editable)
    context_explanation = f"""TASK CONTEXT:
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
    
    # Input format explanation (backend-only, not editable)
    input_format_explanation = """INPUT FORMAT:
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
    
    # Response format specification (backend-only, not editable)
    response_format_specification = """OUTPUT FORMAT - CRITICAL - READ CAREFULLY:

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
- The end_time must be <= {clip_end:.2f}
- Do not add any other fields

FINAL REMINDER: Output ONLY the JSON object { ... }. Nothing else."""
    
    # Assemble complete prompt with model-specific JSON header
    # For Qwen models, JSON header MUST be at the very top
    complete_prompt = f"""{json_header}{video_context}{context_explanation}

{user_prompt}

{input_format_explanation}

Word-level transcript:
{words_text}

{response_format_specification}{json_footer}"""
    
    return complete_prompt


def parse_refinement_response(response: Dict) -> Tuple[float, float]:
    """
    Parse the AI model response to extract refined timestamps.
    
    Args:
        response: Dictionary containing AI model response
        
    Returns:
        Tuple of (start_time, end_time)
    """
    operation = "parse_refinement_response"
    start_time = time.time()
    
    log_operation_start(
        logger="app.services.ai.refinement_service",
        function="parse_refinement_response",
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
        
        # Check if response has error structure (some APIs return errors in different formats)
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
            logger="app.services.ai.refinement_service",
            function="parse_refinement_response",
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
        logger.info("parse_refinement_response: About to call strip_think_tags")
        content = strip_think_tags(content)
        logger.info(f"parse_refinement_response: After strip_think_tags, content length: {len(content)} chars")
        logger.debug(f"parse_refinement_response: Content after strip_think_tags (first 300 chars): {content[:300]}")
        
        # Try to extract JSON from content (handle markdown code blocks)
        json_str = content.strip()
        
        if not json_str:
            logger.error("Content is empty after stripping")
            logger.error(f"Original content (first 500 chars): {content[:500]}")
            raise ValueError("Empty content in response")
        
        # Remove markdown code blocks if present
        if json_str.startswith('```'):
            # Extract content between ```json and ```
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                logger.info("Extracted JSON from markdown code block")
            else:
                logger.warning("Content starts with ``` but no closing ``` found, attempting to parse as-is")
        
        if not json_str:
            logger.error("JSON string is empty after processing markdown code blocks")
            logger.error(f"Original content (first 500 chars): {content[:500]}")
            raise ValueError("Empty JSON string in response after processing")
        
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
        
        start_time = float(result['start_time'])
        end_time = float(result['end_time'])
        
        # Validate times
        if end_time <= start_time:
            raise ValueError(f"Invalid times: end_time ({end_time}) must be > start_time ({start_time})")
        
        duration = time.time() - start_time
        
        log_operation_complete(
            logger="app.services.ai.refinement_service",
            function="parse_refinement_response",
            operation=operation,
            message="Successfully parsed refinement timestamps",
            context={
                "start_time": start_time,
                "end_time": end_time
            },
            duration=duration
        )
        
        return start_time, end_time
        
    except ValueError as e:
        duration = time.time() - start_time
        log_event(
            level="ERROR",
            logger="app.services.ai.refinement_service",
            function="parse_refinement_response",
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
            logger="app.services.ai.refinement_service",
            function="parse_refinement_response",
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
        log_operation_error(
            logger="app.services.ai.refinement_service",
            function="parse_refinement_response",
            operation=operation,
            error=e,
            message="Unexpected error parsing refinement response",
            context={
                "response_type": type(response).__name__ if 'response' in locals() else 'N/A',
                "response_keys": list(response.keys()) if 'response' in locals() and isinstance(response, dict) else None,
                "duration_seconds": duration
            }
        )
        raise ValueError(f"Error parsing response: {str(e)}")


# Job management functions now handled by JobRepository


def process_moment_refinement_async(
    video_id: str,
    moment_id: str,
    video_filename: str,
    user_prompt: str,
    model: str = "minimax",
    temperature: float = 0.7,
    include_video: bool = False,
    video_clip_url: Optional[str] = None
) -> None:
    """
    Process moment refinement asynchronously in a background thread.
    
    Args:
        video_id: ID of the video (filename stem)
        moment_id: ID of the moment to refine
        video_filename: Name of the video file (e.g., "motivation.mp4")
        user_prompt: User-provided prompt (editable, visible in UI)
        model: Model identifier ("minimax", "qwen", or "qwen3_omni"), default: "minimax"
        temperature: Temperature parameter for the model, default: 0.7
        include_video: Whether to include video clip in the refinement request
        video_clip_url: URL of the video clip (if include_video is True)
    """
    def refine():
        try:
            # Import here to avoid circular imports
            from app.services.transcript_service import load_transcript
            from app.services.moments_service import load_moments, add_moment, get_moment_by_id
            from app.services.ai.generation_service import ssh_tunnel, call_ai_model
            from app.utils.model_config import get_model_config, get_clipping_config
            from app.utils.video import get_video_by_filename
            import cv2
            
            logger.info(f"Starting moment refinement for video {video_id}, moment {moment_id}, include_video={include_video}")
            
            # Get padding configuration from backend config
            clipping_config = get_clipping_config()
            padding = clipping_config['padding']
            
            # Load the moment to be refined
            moment = get_moment_by_id(video_filename, moment_id)
            if moment is None:
                raise Exception(f"Moment with ID {moment_id} not found")
            
            logger.info(f"Refining moment: '{moment['title']}' [{moment['start_time']:.2f}s - {moment['end_time']:.2f}s]")
            
            # Load transcript
            audio_filename = video_filename.rsplit('.', 1)[0] + ".wav"
            transcript_data = load_transcript(audio_filename)
            
            if transcript_data is None:
                raise Exception(f"Transcript not found for {audio_filename}")
            
            # Extract word-level timestamps for the padded range with precise boundaries
            words, clip_start, clip_end = extract_word_timestamps_for_range(
                transcript_data,
                moment['start_time'],
                moment['end_time'],
                padding
            )
            
            if not words:
                raise Exception("No words found in specified time range")
            
            # Store the offset for timestamp normalization
            # The clip_start is the offset - it's the absolute time where the clip begins
            offset = clip_start
            
            # Normalize word timestamps to start from 0
            # This aligns the transcript with video clips that will also start from 0
            normalized_words = normalize_word_timestamps(words, offset)
            
            # Calculate normalized clip boundaries (relative to 0)
            normalized_clip_start = 0.0
            normalized_clip_end = clip_end - offset
            
            logger.info(
                f"Timestamp normalization: offset={offset:.2f}s, "
                f"absolute clip=[{clip_start:.2f}s - {clip_end:.2f}s], "
                f"normalized clip=[{normalized_clip_start:.2f}s - {normalized_clip_end:.2f}s]"
            )
            
            # Get video duration for validation
            video_file = get_video_by_filename(video_filename)
            if not video_file:
                raise Exception(f"Video file not found: {video_filename}")
            
            cap = cv2.VideoCapture(str(video_file))
            if not cap.isOpened():
                raise Exception(f"Could not open video file: {video_filename}")
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            video_duration = frame_count / fps if fps > 0 else 0.0
            cap.release()
            
            if video_duration <= 0:
                raise Exception(f"Could not determine video duration for {video_filename}")
            
            logger.info(f"Video duration: {video_duration:.2f} seconds, Words: {len(words)}, Clip: [{clip_start:.2f}s - {clip_end:.2f}s]")
            
            # Normalize original moment timestamps to match the coordinate system
            # of the words and video clip (both start at 0.0)
            normalized_original_start = moment['start_time'] - offset
            normalized_original_end = moment['end_time'] - offset
            
            logger.info(
                f"Original moment timestamps: absolute=[{moment['start_time']:.2f}s - {moment['end_time']:.2f}s], "
                f"normalized=[{normalized_original_start:.2f}s - {normalized_original_end:.2f}s]"
            )
            
            # Build refinement prompt with normalized clip boundaries and words
            # The model will receive ALL timestamps starting from 0 (normalized coordinate system)
            complete_prompt = build_refinement_prompt(
                user_prompt=user_prompt,
                words=normalized_words,  # Use normalized words (starting from 0)
                clip_start=normalized_clip_start,  # 0.0
                clip_end=normalized_clip_end,  # Duration of clip
                original_start=normalized_original_start,  # Normalized to match transcript/video
                original_end=normalized_original_end,  # Normalized to match transcript/video
                original_title=moment['title'],
                model_key=model,  # Pass model key for model-specific prompting
                include_video=include_video,
                video_clip_url=video_clip_url
            )
            
            if include_video:
                logger.info(f"Video included in refinement request: {video_clip_url}")
            
            logger.debug(f"Complete prompt length: {len(complete_prompt)} characters")
            
            # Get model configuration
            model_config = get_model_config(model)
            model_id = model_config.get('model_id')
            
            # Create SSH tunnel and call AI model
            with ssh_tunnel(model):
                # Prepare messages for AI model
                messages = [{
                    "role": "user",
                    "content": complete_prompt
                }]
                
                # Call AI model (with video URL if include_video is True)
                logger.info(f"Calling AI model ({model}) for moment refinement (include_video={include_video})...")
                ai_response = call_ai_model(
                    messages, 
                    model_key=model, 
                    model_id=model_id, 
                    temperature=temperature,
                    video_url=video_clip_url if include_video else None
                )
                
                if ai_response is None:
                    raise Exception("AI model call failed or returned no response")
                
                # Log the full raw response for debugging
                try:
                    response_json = json.dumps(ai_response, indent=2, ensure_ascii=False)
                    logger.info(f"=== FULL RAW AI RESPONSE (length: {len(response_json)} chars) ===")
                    logger.info(response_json)
                    logger.info("=== END OF FULL RAW AI RESPONSE ===")
                    
                    # Also log to structured JSON log
                    log_event(
                        level="INFO",
                        logger="app.services.ai.refinement_service",
                        function="refine",
                        operation="ai_model_response",
                        event="raw_response_received",
                        message="Full raw AI model response received",
                        context={
                            "response_length": len(response_json),
                            "response_keys": list(ai_response.keys()) if isinstance(ai_response, dict) else None,
                            "full_response": response_json
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to serialize full response for logging: {e}")
                    logger.info(f"Raw response (string representation, first 2000 chars): {str(ai_response)[:2000]}")
                
                # Validate response structure before parsing
                if not isinstance(ai_response, dict):
                    logger.error(f"AI response is not a dictionary. Type: {type(ai_response)}, Value: {str(ai_response)[:200]}")
                    raise Exception(f"AI model returned invalid response type: {type(ai_response).__name__}")
                
                # Check for error in response
                if 'error' in ai_response:
                    error_info = ai_response.get('error', {})
                    if isinstance(error_info, dict):
                        error_msg = error_info.get('message', str(error_info))
                    else:
                        error_msg = str(error_info)
                    logger.error(f"AI model returned an error: {error_msg}")
                    raise Exception(f"AI model error: {error_msg}")
                
                # Validate response has expected structure
                if 'choices' not in ai_response:
                    logger.error(f"AI response missing 'choices' key. Response keys: {list(ai_response.keys())}")
                    logger.error(f"Response content: {json.dumps(ai_response, indent=2)[:500]}")
                    raise Exception("AI model response missing 'choices' key")
                
                if not isinstance(ai_response['choices'], list) or len(ai_response['choices']) == 0:
                    logger.error(f"AI response has empty or invalid 'choices'. Choices: {ai_response.get('choices', 'N/A')}")
                    raise Exception("AI model response has no choices")
                
                # Extract model name from response
                model_name = extract_model_name(ai_response)
                logger.info(f"Using AI model: {model_name}")
                
                # Extract response content for logging
                response_content = ai_response.get('choices', [{}])[0].get('message', {}).get('content', '')
                
                # Parse response to extract refined timestamps
                logger.info("Parsing AI model response...")
                parsing_success = False
                parsing_error = None
                refined_start = None
                refined_end = None
                
                try:
                    # Model returns normalized timestamps (relative to 0)
                    refined_start_normalized, refined_end_normalized = parse_refinement_response(ai_response)
                    
                    # Denormalize timestamps to get absolute times
                    refined_start = denormalize_timestamp(refined_start_normalized, offset)
                    refined_end = denormalize_timestamp(refined_end_normalized, offset)
                    
                    parsing_success = True
                    logger.info(
                        f"Refined timestamps: normalized=[{refined_start_normalized:.2f}s - {refined_end_normalized:.2f}s], "
                        f"absolute=[{refined_start:.2f}s - {refined_end:.2f}s]"
                    )
                except Exception as parse_err:
                    parsing_error = str(parse_err)
                    logger.error(f"Error parsing refinement: {parsing_error}")
                    raise
                finally:
                    # Log request/response for debugging
                    from app.utils.model_config import get_model_url
                    model_url = get_model_url(model)
                    payload = {
                        "messages": messages,
                        "max_tokens": 15000,  # MAX_TOKENS from moments_generation_service
                        "temperature": temperature
                    }
                    if model_id:
                        payload["model"] = model_id
                    if 'top_p' in model_config:
                        payload["top_p"] = model_config['top_p']
                    if 'top_k' in model_config:
                        payload["top_k"] = model_config['top_k']
                    
                    extracted_data = None
                    if parsing_success and refined_start is not None and refined_end is not None:
                        extracted_data = {"start_time": refined_start, "end_time": refined_end}
                    
                    log_ai_request_response(
                        operation="moment_refinement",
                        video_id=video_id,
                        model_key=model,
                        model_name=model_name,
                        model_id=model_id,
                        model_url=model_url,
                        request_payload=payload,
                        response_status_code=200,  # If we got here, status was 200
                        response_data=ai_response,
                        response_content=response_content,
                        duration_seconds=time.time() - time.time(),  # Will be approximated
                        parsing_success=parsing_success,
                        parsing_error=parsing_error,
                        extracted_data=extracted_data,
                        request_id=get_request_id(),
                    )
                
                # Validate refined timestamps
                if refined_start < 0 or refined_end > video_duration:
                    raise Exception(f"Refined timestamps outside video bounds [0, {video_duration:.2f}]")
                
                # Create generation_config dictionary with all refinement parameters
                generation_config = {
                    "model": model,
                    "temperature": temperature,
                    "user_prompt": user_prompt,
                    "complete_prompt": complete_prompt,
                    "padding": padding,
                    "clip_start": clip_start,
                    "clip_end": clip_end,
                    "timestamp_offset": offset,  # Store offset for traceability
                    "normalized_clip_start": normalized_clip_start,
                    "normalized_clip_end": normalized_clip_end,
                    "operation_type": "refinement",
                    "video_included": include_video,
                    "video_clip_url": video_clip_url if include_video else None
                }
                
                # Create refined moment
                refined_moment = {
                    'start_time': refined_start,
                    'end_time': refined_end,
                    'title': moment['title'],  # Keep same title as original
                    'is_refined': True,
                    'parent_id': moment_id,
                    'model_name': model_name,
                    'prompt': complete_prompt,
                    'generation_config': generation_config
                }
                
                # Add refined moment
                success, error_message, created_moment = add_moment(
                    video_filename,
                    refined_moment,
                    video_duration
                )
                
                if not success:
                    raise Exception(f"Failed to save refined moment: {error_message}")
                
                # Mark job as complete
                job_repo.update_status(
                    JobType.MOMENT_REFINEMENT,
                    video_id,
                    JobStatus.COMPLETED,
                    moment_id=moment_id
                )
                
                logger.info(f"Moment refinement completed successfully for {video_id}:{moment_id}")
                
        except Exception as e:
            logger.error(f"Error in async moment refinement for {video_id}:{moment_id}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            job_repo.update_status(
                JobType.MOMENT_REFINEMENT,
                video_id,
                JobStatus.FAILED,
                moment_id=moment_id,
                error=str(e)
            )
    
    # Start processing in background thread
    thread = threading.Thread(target=refine, daemon=True)
    thread.start()

