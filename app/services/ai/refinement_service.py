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
from app.services.ai.prompt_tasks import RefinementTask, extract_model_name, strip_think_tags
from app.utils.timestamp import calculate_padded_boundaries, extract_words_in_range, normalize_word_timestamps, denormalize_timestamp

logger = logging.getLogger(__name__)


# extract_model_name and strip_think_tags are now imported from prompt_tasks.utils


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


# build_refinement_prompt and parse_refinement_response have been moved to RefinementTask class


# Job management functions now handled by JobRepository


async def process_moment_refinement(
    video_id: str,
    moment_id: str,
    video_filename: str,
    user_prompt: str,
    model: str = "minimax",
    temperature: float = 0.7,
    include_video: bool = False,
    video_clip_url: Optional[str] = None
) -> bool:
    """
    Process moment refinement as an async coroutine.
    
    This is the recommended async version that integrates with the pipeline orchestrator.
    Unlike the deprecated thread-based version, this function:
    - Returns True/False directly (no JobRepository polling needed)
    - Raises exceptions on errors (native exception handling)
    - Can be used with asyncio.wait_for() for timeout handling
    
    Args:
        video_id: ID of the video (filename stem)
        moment_id: ID of the moment to refine
        video_filename: Name of the video file (e.g., "motivation.mp4")
        user_prompt: User-provided prompt (editable, visible in UI)
        model: Model identifier ("minimax", "qwen", or "qwen3_omni"), default: "minimax"
        temperature: Temperature parameter for the model, default: 0.7
        include_video: Whether to include video clip in the refinement request
        video_clip_url: URL of the video clip (if include_video is True)
    
    Returns:
        True if refinement succeeded, False otherwise
    
    Raises:
        Exception: If refinement fails with an error that should stop processing
    """
    # Import here to avoid circular imports
    from app.services.transcript_service import load_transcript
    from app.services.moments_service import add_moment, get_moment_by_id
    from app.services.ai.generation_service import ssh_tunnel, call_ai_model_async
    from app.utils.model_config import get_model_config, get_clipping_config, get_model_url
    from app.utils.video import get_video_by_filename
    import cv2
    
    start_time = time.time()
    
    try:
        logger.info(f"Starting moment refinement (async) for video {video_id}, moment {moment_id}, include_video={include_video}")
        
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
        offset = clip_start
        
        # Normalize word timestamps to start from 0
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
        
        # Normalize original moment timestamps
        normalized_original_start = moment['start_time'] - offset
        normalized_original_end = moment['end_time'] - offset
        
        logger.info(
            f"Original moment timestamps: absolute=[{moment['start_time']:.2f}s - {moment['end_time']:.2f}s], "
            f"normalized=[{normalized_original_start:.2f}s - {normalized_original_end:.2f}s]"
        )
        
        # Create refinement task and build prompt
        task = RefinementTask()
        complete_prompt = task.build_prompt(
            model_key=model,
            context={
                "user_prompt": user_prompt,
                "words": normalized_words,
                "clip_start": normalized_clip_start,
                "clip_end": normalized_clip_end,
                "original_start": normalized_original_start,
                "original_end": normalized_original_end,
                "original_title": moment['title'],
                "include_video": include_video,
                "video_clip_url": video_clip_url,
            }
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
            
            # Call AI model asynchronously
            logger.info(f"Calling AI model ({model}) for moment refinement (async, include_video={include_video})...")
            ai_response = await call_ai_model_async(
                messages, 
                model_key=model, 
                model_id=model_id, 
                temperature=temperature,
                video_url=video_clip_url if include_video else None
            )
            
            if ai_response is None:
                raise Exception("AI model call failed or returned no response")
            
            # Log the raw response
            try:
                response_json = json.dumps(ai_response, indent=2, ensure_ascii=False)
                logger.info(f"=== FULL RAW AI RESPONSE (async) (length: {len(response_json)} chars) ===")
                logger.info(response_json)
                logger.info("=== END OF FULL RAW AI RESPONSE ===")
                
                log_event(
                    level="INFO",
                    logger="app.services.ai.refinement_service",
                    function="process_moment_refinement",
                    operation="ai_model_response",
                    event="raw_response_received",
                    message="Full raw AI model response received (async)",
                    context={
                        "response_length": len(response_json),
                        "response_keys": list(ai_response.keys()) if isinstance(ai_response, dict) else None,
                        "full_response": response_json
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to serialize full response for logging: {e}")
                logger.info(f"Raw response (string representation, first 2000 chars): {str(ai_response)[:2000]}")
            
            # Validate response structure
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
                raise Exception("AI model response missing 'choices' key")
            
            if not isinstance(ai_response['choices'], list) or len(ai_response['choices']) == 0:
                logger.error(f"AI response has empty or invalid 'choices'.")
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
                refined_start_normalized, refined_end_normalized = task.parse_response(ai_response)
                
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
                model_url = get_model_url(model)
                payload = {
                    "messages": messages,
                    "max_tokens": 15000,
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
                    operation="moment_refinement_async",
                    video_id=video_id,
                    model_key=model,
                    model_name=model_name,
                    model_id=model_id,
                    model_url=model_url,
                    request_payload=payload,
                    response_status_code=200,
                    response_data=ai_response,
                    response_content=response_content,
                    duration_seconds=time.time() - start_time,
                    parsing_success=parsing_success,
                    parsing_error=parsing_error,
                    extracted_data=extracted_data,
                    request_id=get_request_id(),
                )
            
            # Validate refined timestamps
            if refined_start < 0 or refined_end > video_duration:
                raise Exception(f"Refined timestamps outside video bounds [0, {video_duration:.2f}]")
            
            # Create generation_config dictionary
            generation_config = {
                "model": model,
                "temperature": temperature,
                "user_prompt": user_prompt,
                "complete_prompt": complete_prompt,
                "padding": padding,
                "clip_start": clip_start,
                "clip_end": clip_end,
                "timestamp_offset": offset,
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
                'title': moment['title'],
                'is_refined': True,
                'parent_id': moment_id,
                'model_name': model_name,
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
            
            logger.info(f"Moment refinement (async) completed successfully for {video_id}:{moment_id}")
            return True
            
    except Exception as e:
        logger.error(f"Error in async moment refinement for {video_id}:{moment_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # Re-raise to allow orchestrator to handle the error
        raise


# DEPRECATED: Use process_moment_refinement() instead for pipeline operations
