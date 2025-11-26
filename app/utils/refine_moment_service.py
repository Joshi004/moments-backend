import threading
import time
import json
import re
from typing import Optional, Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

# In-memory job tracking dictionary for moment refinement
# Structure: {job_key: {"status": "processing"|"completed"|"failed", "started_at": timestamp}}
_refinement_jobs: Dict[str, Dict] = {}
_refinement_lock = threading.Lock()


def get_refinement_job_key(video_id: str, moment_id: str) -> str:
    """Generate a unique key for a refinement job."""
    return f"{video_id}:{moment_id}"


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


def extract_word_timestamps_for_range(
    transcript: Dict,
    start: float,
    end: float,
    left_padding: float,
    right_padding: float
) -> List[Dict]:
    """
    Extract word-level timestamps within padded time range.
    
    Args:
        transcript: Dictionary containing transcript data with 'word_timestamps'
        start: Original moment start time in seconds
        end: Original moment end time in seconds
        left_padding: Seconds to pad before start
        right_padding: Seconds to pad after end
        
    Returns:
        List of word dictionaries with 'word', 'start', and 'end' fields
    """
    if not transcript or 'word_timestamps' not in transcript:
        logger.warning("Transcript does not contain word_timestamps")
        return []
    
    word_timestamps = transcript['word_timestamps']
    if not isinstance(word_timestamps, list):
        logger.warning("word_timestamps is not a list")
        return []
    
    # Calculate padded range
    padded_start = max(0, start - left_padding)
    padded_end = end + right_padding
    
    # Extract words within range
    extracted_words = []
    for word_data in word_timestamps:
        if isinstance(word_data, dict) and 'word' in word_data and 'start' in word_data and 'end' in word_data:
            word_start = float(word_data['start'])
            word_end = float(word_data['end'])
            
            # Include word if it overlaps with our range
            if word_end >= padded_start and word_start <= padded_end:
                extracted_words.append({
                    'word': str(word_data['word']),
                    'start': word_start,
                    'end': word_end
                })
    
    logger.info(f"Extracted {len(extracted_words)} words from range [{padded_start:.2f}s - {padded_end:.2f}s]")
    return extracted_words


def build_refinement_prompt(
    user_prompt: str,
    words: List[Dict],
    original_start: float,
    original_end: float,
    original_title: str
) -> str:
    """
    Build the complete prompt for moment refinement.
    
    Args:
        user_prompt: User-provided prompt (editable, visible in UI)
        words: List of word dictionaries with 'word', 'start', and 'end' fields
        original_start: Original moment start time
        original_end: Original moment end time
        original_title: Title of the moment being refined
        
    Returns:
        Complete prompt string with all sections assembled
    """
    # Format words as [start-end] word
    words_text = "\n".join([
        f"[{word['start']:.2f}-{word['end']:.2f}] {word['word']}"
        for word in words
    ])
    
    # Context explanation (backend-only, not editable)
    context_explanation = f"""TASK CONTEXT:
You are refining the timestamps for an existing video moment. The moment currently has the following information:
- Title: "{original_title}"
- Current start time: {original_start:.2f} seconds
- Current end time: {original_end:.2f} seconds

The timestamps may not be precisely aligned with where the content actually begins and ends. Your task is to analyze the word-level transcript and determine the exact timestamps where this moment should start and end."""
    
    # Input format explanation (backend-only, not editable)
    input_format_explanation = """INPUT FORMAT:
You are provided with word-level timestamps. Each line shows:
- The start and end time of a specific word in seconds
- The word itself

Format: [start_time-end_time] word

Example:
[5.12-5.48] rather
[5.48-5.76] than
[5.76-5.92] be
[5.92-6.24] scared"""
    
    # Response format specification (backend-only, not editable)
    response_format_specification = """OUTPUT FORMAT:
You must respond with a valid JSON object containing only two fields:
- start_time: (float) The precise start time in seconds where this moment should begin
- end_time: (float) The precise end time in seconds where this moment should end

Example response:
{
  "start_time": 5.12,
  "end_time": 67.84
}

IMPORTANT:
- The start_time and end_time must correspond to word boundaries from the provided transcript
- The start_time must be less than end_time
- Both timestamps must be within the range of the provided word timestamps
- Do NOT include any other fields in your response
- Do NOT add explanations or comments, just return the JSON object"""
    
    # Assemble complete prompt
    complete_prompt = f"""{context_explanation}

{user_prompt}

{input_format_explanation}

Word-level transcript:
{words_text}

{response_format_specification}"""
    
    return complete_prompt


def parse_refinement_response(response: Dict) -> Tuple[float, float]:
    """
    Parse the AI model response to extract refined timestamps.
    
    Args:
        response: Dictionary containing AI model response
        
    Returns:
        Tuple of (start_time, end_time)
    """
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
        
        # Validate content is a string
        if not isinstance(content, str):
            logger.error(f"Content is not a string. Type: {type(content)}, Value: {str(content)[:200]}")
            raise ValueError(f"Content is not a string, got {type(content).__name__}")
        
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
        
        logger.info(f"Successfully parsed refinement: [{start_time:.2f}s - {end_time:.2f}s]")
        return start_time, end_time
        
    except ValueError as e:
        # Re-raise ValueError as-is (these are our validation errors)
        logger.error(f"Validation error parsing refinement response: {str(e)}")
        raise
    except json.JSONDecodeError as e:
        # This should not happen now since we catch it above, but keep for safety
        logger.error(f"JSON decode error parsing refinement response: {str(e)}")
        json_str_preview = json_str[:1000] if 'json_str' in locals() else 'N/A'
        logger.error(f"JSON string that failed to parse: {json_str_preview}")
        raise ValueError(f"Invalid JSON in response: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error parsing refinement response: {str(e)}")
        logger.error(f"Response type: {type(response).__name__ if 'response' in locals() else 'N/A'}")
        if 'response' in locals() and isinstance(response, dict):
            logger.error(f"Response keys: {list(response.keys())}")
        import traceback
        logger.error(traceback.format_exc())
        raise ValueError(f"Error parsing response: {str(e)}")


def start_refinement_job(video_id: str, moment_id: str) -> bool:
    """
    Register a new refinement job.
    
    Args:
        video_id: ID of the video (filename stem)
        moment_id: ID of the moment being refined
        
    Returns:
        True if job was registered, False if already processing
    """
    job_key = get_refinement_job_key(video_id, moment_id)
    with _refinement_lock:
        # Check if job exists and is currently processing
        if job_key in _refinement_jobs:
            job_status = _refinement_jobs[job_key].get("status", "")
            if job_status == "processing":
                return False
            # If job is completed or failed, we can start a new one
            del _refinement_jobs[job_key]
        
        _refinement_jobs[job_key] = {
            "status": "processing",
            "started_at": time.time()
        }
        return True


def complete_refinement_job(video_id: str, moment_id: str, success: bool = True) -> None:
    """
    Mark a refinement job as complete.
    
    Args:
        video_id: ID of the video
        moment_id: ID of the moment
        success: True if processing succeeded, False otherwise
    """
    job_key = get_refinement_job_key(video_id, moment_id)
    with _refinement_lock:
        if job_key in _refinement_jobs:
            _refinement_jobs[job_key]["status"] = "completed" if success else "failed"


def is_refining(video_id: str, moment_id: str) -> bool:
    """
    Check if a moment is currently being refined.
    
    Args:
        video_id: ID of the video
        moment_id: ID of the moment
        
    Returns:
        True if refining, False otherwise
    """
    job_key = get_refinement_job_key(video_id, moment_id)
    with _refinement_lock:
        if job_key not in _refinement_jobs:
            return False
        status = _refinement_jobs[job_key].get("status", "")
        return status == "processing"


def get_refinement_status(video_id: str, moment_id: str) -> Optional[Dict]:
    """
    Get refinement status for a specific moment.
    
    Args:
        video_id: ID of the video
        moment_id: ID of the moment
        
    Returns:
        Dictionary with 'status' and 'started_at', or None if no job exists
    """
    job_key = get_refinement_job_key(video_id, moment_id)
    with _refinement_lock:
        if job_key not in _refinement_jobs:
            return None
        
        job_info = _refinement_jobs[job_key]
        return {
            "status": job_info.get("status", "unknown"),
            "started_at": job_info.get("started_at", 0)
        }


def process_moment_refinement_async(
    video_id: str,
    moment_id: str,
    video_filename: str,
    user_prompt: str,
    left_padding: float,
    right_padding: float,
    model: str = "minimax",
    temperature: float = 0.7
) -> None:
    """
    Process moment refinement asynchronously in a background thread.
    
    Args:
        video_id: ID of the video (filename stem)
        moment_id: ID of the moment to refine
        video_filename: Name of the video file (e.g., "motivation.mp4")
        user_prompt: User-provided prompt (editable, visible in UI)
        left_padding: Seconds to pad before moment start
        right_padding: Seconds to pad after moment end
        model: Model identifier ("minimax" or "qwen"), default: "minimax"
        temperature: Temperature parameter for the model, default: 0.7
    """
    def refine():
        try:
            # Import here to avoid circular imports
            from app.utils.transcript_service import load_transcript
            from app.utils.moments_service import load_moments, add_moment, get_moment_by_id
            from app.utils.moments_generation_service import ssh_tunnel, call_ai_model
            from app.utils.model_config import get_model_config
            from app.utils.video_utils import get_video_by_filename
            import cv2
            
            logger.info(f"Starting moment refinement for video {video_id}, moment {moment_id}")
            
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
            
            # Extract word-level timestamps for the padded range
            words = extract_word_timestamps_for_range(
                transcript_data,
                moment['start_time'],
                moment['end_time'],
                left_padding,
                right_padding
            )
            
            if not words:
                raise Exception("No words found in specified time range")
            
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
            
            logger.info(f"Video duration: {video_duration:.2f} seconds, Words: {len(words)}")
            
            # Build refinement prompt
            complete_prompt = build_refinement_prompt(
                user_prompt=user_prompt,
                words=words,
                original_start=moment['start_time'],
                original_end=moment['end_time'],
                original_title=moment['title']
            )
            
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
                
                # Call AI model
                logger.info(f"Calling AI model ({model}) for moment refinement...")
                ai_response = call_ai_model(messages, model_key=model, model_id=model_id, temperature=temperature)
                
                if ai_response is None:
                    raise Exception("AI model call failed or returned no response")
                
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
                
                # Parse response to extract refined timestamps
                logger.info("Parsing AI model response...")
                refined_start, refined_end = parse_refinement_response(ai_response)
                
                logger.info(f"Refined timestamps: [{refined_start:.2f}s - {refined_end:.2f}s]")
                
                # Validate refined timestamps
                if refined_start < 0 or refined_end > video_duration:
                    raise Exception(f"Refined timestamps outside video bounds [0, {video_duration:.2f}]")
                
                # Create refined moment
                refined_moment = {
                    'start_time': refined_start,
                    'end_time': refined_end,
                    'title': moment['title'],  # Keep same title as original
                    'is_refined': True,
                    'parent_id': moment_id,
                    'model_name': model_name,
                    'prompt': complete_prompt
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
                complete_refinement_job(video_id, moment_id, success=True)
                
                logger.info(f"Moment refinement completed successfully for {video_id}:{moment_id}")
                
        except Exception as e:
            logger.error(f"Error in async moment refinement for {video_id}:{moment_id}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            complete_refinement_job(video_id, moment_id, success=False)
    
    # Start processing in background thread
    thread = threading.Thread(target=refine, daemon=True)
    thread.start()

