import time
import json
from typing import Optional, Dict, List
import logging
from app.utils.model_config import get_model_config
from app.services.model_connector import get_service_url
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)
from app.services.ai.request_logger import log_ai_request_response
from app.services.ai.prompt_tasks import GenerationTask, get_response_format_param, extract_model_name

logger = logging.getLogger(__name__)

# Hardcoded max_tokens for all models
MAX_TOKENS = 15000


async def call_ai_model_async(
    messages: List[Dict], 
    model_key: str = "minimax", 
    model_id: Optional[str] = None, 
    temperature: float = 0.7,
    video_url: Optional[str] = None,
    output_type: str = "array"
) -> Optional[Dict]:
    """
    Call the AI model via tunnel asynchronously using httpx.
    
    This is the async version of call_ai_model() for use in async contexts.
    The SSH tunnel must already be established before calling this function.
    
    Args:
        messages: List of message dictionaries with 'role' and 'content'
        model_key: Model identifier ("minimax", "qwen", or "qwen3_omni")
        model_id: Optional model ID to use in the request (if None, uses config default)
        temperature: Temperature parameter for the model (default: 0.7)
        video_url: Optional URL to video clip for multimodal requests
        output_type: Output type for response format ("array" or "object"), defaults to "array"
    
    Returns:
        Dictionary with AI model response or None if failed
    """
    import httpx
    
    operation = "ai_model_call_async"
    start_time = time.time()
    model_url = None
    
    try:
        model_url = await get_service_url(model_key)
        config = await get_model_config(model_key)
        
        # Use provided model_id or get from config
        if model_id is None:
            model_id = config.get('model_id')
        
        # Transform messages to multimodal format if video_url is provided
        if video_url:
            logger.info(f"Building multimodal request with video URL: {video_url}")
            transformed_messages = []
            for msg in messages:
                if msg.get('role') == 'user' and isinstance(msg.get('content'), str):
                    # Convert text content to multimodal content array with video
                    multimodal_content = [
                        {"type": "video_url", "video_url": {"url": video_url}},
                        {"type": "text", "text": msg['content']}
                    ]
                    transformed_messages.append({
                        "role": msg['role'],
                        "content": multimodal_content
                    })
                else:
                    transformed_messages.append(msg)
            messages = transformed_messages
        
        payload = {
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": temperature
        }
        
        # Only add model_id if it's specified (Qwen needs it, MiniMax might not)
        if model_id:
            payload["model"] = model_id
        
        # Add top_p and top_k if they're specified in the model config
        if 'top_p' in config:
            payload["top_p"] = config['top_p']
        if 'top_k' in config:
            payload["top_k"] = config['top_k']
        
        # Add response_format for models that support it (vLLM 0.10+)
        response_format = get_response_format_param(model_key, output_type)
        if response_format:
            payload["response_format"] = response_format
            logger.info(f"Using response_format enforcement: {response_format}")
        
        # Log prompt being sent (first message content, truncated)
        first_content = messages[0].get('content', '') if messages else 'N/A'
        if isinstance(first_content, list):
            text_parts = [item.get('text', '') for item in first_content if item.get('type') == 'text']
            prompt_preview = (text_parts[0][:500] if text_parts else 'N/A')
            prompt_length = len(text_parts[0]) if text_parts else 0
        else:
            prompt_preview = first_content[:500] if first_content else 'N/A'
            prompt_length = len(first_content) if first_content else 0
        
        log_operation_start(
            logger="app.services.ai.generation_service",
            function="call_ai_model_async",
            operation=operation,
            message="Calling AI model (async)",
            context={
                "model_key": model_key,
                "model_id": model_id,
                "model_url": model_url,
                "temperature": temperature,
                "max_tokens": MAX_TOKENS,
                "message_count": len(messages),
                "prompt_preview": prompt_preview,
                "prompt_length": prompt_length,
                "video_url": video_url,
                "is_multimodal": video_url is not None,
                "request_id": get_request_id()
            }
        )
        
        # Use httpx AsyncClient for async HTTP requests
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                model_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
        
        duration = time.time() - start_time
        
        log_event(
            level="DEBUG",
            logger="app.services.ai.generation_service",
            function="call_ai_model_async",
            operation=operation,
            event="model_call_complete",
            message="Received response from AI model",
            context={
                "status_code": response.status_code,
                "response_size_bytes": len(response.content) if response.content else 0,
                "duration_seconds": duration
            }
        )
        
        response.raise_for_status()
        
        try:
            result = response.json()
            
            log_operation_complete(
                logger="app.services.ai.generation_service",
                function="call_ai_model_async",
                operation=operation,
                message="AI model call completed successfully (async)",
                context={
                    "model_key": model_key,
                    "model_id": model_id,
                    "response_keys": list(result.keys()) if isinstance(result, dict) else None,
                    "has_choices": "choices" in result if isinstance(result, dict) else False,
                    "duration_seconds": duration
                }
            )
            return result
        except json.JSONDecodeError as e:
            log_event(
                level="ERROR",
                logger="app.services.ai.generation_service",
                function="call_ai_model_async",
                operation=operation,
                event="parse_error",
                message="Failed to parse AI model response as JSON",
                context={
                    "error": str(e),
                    "response_preview": response.text[:2000] if hasattr(response, 'text') else None,
                    "duration_seconds": duration
                }
            )
            raise
        
    except httpx.ConnectError as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.ai.generation_service",
            function="call_ai_model_async",
            operation=operation,
            error=e,
            message="Connection error calling AI model (async)",
            context={
                "model_key": model_key,
                "model_url": model_url,
                "error": str(e),
                "duration_seconds": duration
            }
        )
        return None
    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.ai.generation_service",
            function="call_ai_model_async",
            operation=operation,
            error=e,
            message="Timeout calling AI model (async)",
            context={
                "model_key": model_key,
                "model_url": model_url,
                "timeout_seconds": 600,
                "duration_seconds": duration
            }
        )
        return None
    except httpx.HTTPStatusError as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.ai.generation_service",
            function="call_ai_model_async",
            operation=operation,
            error=e,
            message="HTTP error calling AI model (async)",
            context={
                "model_key": model_key,
                "model_url": model_url,
                "status_code": e.response.status_code if e.response else None,
                "response_preview": e.response.text[:500] if e.response and e.response.text else None,
                "duration_seconds": duration
            }
        )
        return None
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.ai.generation_service",
            function="call_ai_model_async",
            operation=operation,
            error=e,
            message="Unexpected error in AI model call (async)",
            context={
                "model_key": model_key,
                "model_url": model_url,
                "duration_seconds": duration
            }
        )
        return None


def extract_segment_data(transcript: Dict) -> List[Dict]:
    """
    Extract segment timestamps from transcript, returning only start time and text.
    
    Args:
        transcript: Dictionary containing transcript data with 'segment_timestamps'
    
    Returns:
        List of dictionaries with 'start' (float) and 'text' (string)
    """
    if not transcript or 'segment_timestamps' not in transcript:
        logger.warning("Transcript does not contain segment_timestamps")
        return []
    
    segments = transcript['segment_timestamps']
    if not isinstance(segments, list):
        logger.warning("segment_timestamps is not a list")
        return []
    
    extracted = []
    for segment in segments:
        if isinstance(segment, dict) and 'start' in segment and 'text' in segment:
            extracted.append({
                'start': float(segment['start']),
                'text': str(segment['text'])
            })
    
    logger.info(f"Extracted {len(extracted)} segments from transcript")
    return extracted


# build_prompt and parse_moments_response functions have been moved to GenerationTask class


async def process_moments_generation(
    video_id: str,
    video_filename: str,
    user_prompt: str,
    min_moment_length: float,
    max_moment_length: float,
    min_moments: int,
    max_moments: int,
    model: str = "minimax",
    temperature: float = 0.7
) -> Dict:
    """
    Process moment generation as an async coroutine.
    
    This is the recommended async version that integrates with the pipeline orchestrator.
    Unlike the deprecated thread-based version, this function:
    - Returns moments list and config ID (no Redis polling needed)
    - Raises exceptions on errors (native exception handling)
    - Can be used with asyncio.wait_for() for timeout handling
    - Creates database records for prompts and generation configs (Phase 5)
    
    Args:
        video_id: ID of the video (filename stem)
        video_filename: Name of the video file (e.g., "motivation.mp4")
        user_prompt: User-provided prompt (editable, visible in UI)
        min_moment_length: Minimum moment length in seconds
        max_moment_length: Maximum moment length in seconds
        min_moments: Minimum number of moments to generate
        max_moments: Maximum number of moments to generate
        model: Model identifier ("minimax", "qwen", or "qwen3_omni"), default: "minimax"
        temperature: Temperature parameter for the model, default: 0.7
    
    Returns:
        Dictionary with:
            - "moments": List of validated moment dictionaries
            - "generation_config_id": Database ID of the generation config (or None if DB failed)
    
    Raises:
        Exception: If generation fails with an error that should stop processing
    """
    operation = "moment_generation"
    start_time = time.time()
    
    try:
        # Import here to avoid circular imports
        from app.services.transcript_service import load_transcript
        
        log_operation_start(
            logger="app.services.ai.generation_service",
            function="process_moments_generation",
            operation=operation,
            message=f"Starting moment generation (async) for {video_id}",
            context={
                "video_id": video_id,
                "video_filename": video_filename,
                "model": model,
                "temperature": temperature,
                "min_moment_length": min_moment_length,
                "max_moment_length": max_moment_length,
                "min_moments": min_moments,
                "max_moments": max_moments,
                "request_id": get_request_id()
            }
        )
        
        # Load transcript
        audio_filename = video_filename.rsplit('.', 1)[0] + ".wav"
        
        log_event(
            level="DEBUG",
            logger="app.services.ai.generation_service",
            function="process_moments_generation",
            operation=operation,
            event="file_operation_start",
            message="Loading transcript",
            context={"audio_filename": audio_filename}
        )
        
        transcript_data = await load_transcript(audio_filename)
        
        if transcript_data is None:
            log_event(
                level="ERROR",
                logger="app.services.ai.generation_service",
                function="process_moments_generation",
                operation=operation,
                event="file_operation_error",
                message="Transcript not found",
                context={"audio_filename": audio_filename}
            )
            raise Exception(f"Transcript not found for {audio_filename}")
        
        # Extract segments (only start timestamp and text)
        segments = extract_segment_data(transcript_data)
        
        log_event(
            level="DEBUG",
            logger="app.services.ai.generation_service",
            function="process_moments_generation",
            operation=operation,
            event="operation_start",
            message="Extracted segments from transcript",
            context={"segment_count": len(segments)}
        )
        
        if not segments:
            log_event(
                level="ERROR",
                logger="app.services.ai.generation_service",
                function="process_moments_generation",
                operation=operation,
                event="validation_error",
                message="No segments found in transcript",
            )
            raise Exception("No segments found in transcript")
        
        # Get video duration from database
        from app.database.session import get_session_factory
        from app.repositories import video_db_repository as _video_db_repo
        _session_factory = get_session_factory()
        async with _session_factory() as _session:
            _video_record = await _video_db_repo.get_by_identifier(_session, video_id)
        if not _video_record or not _video_record.duration_seconds:
            raise Exception(f"Video not found or duration unknown in database: {video_id}")
        video_duration = _video_record.duration_seconds

        if video_duration <= 0:
            raise Exception(f"Could not determine video duration for {video_filename}")
        
        logger.info(f"Video duration: {video_duration:.2f} seconds, Segments: {len(segments)}")
        
        # Create generation task and build complete prompt
        task = GenerationTask()
        complete_prompt = task.build_prompt(
            model_key=model,
            context={
                "user_prompt": user_prompt,
                "segments": segments,
                "video_duration": video_duration,
                "min_moment_length": min_moment_length,
                "max_moment_length": max_moment_length,
                "min_moments": min_moments,
                "max_moments": max_moments,
            }
        )
        
        logger.debug(f"Complete prompt length: {len(complete_prompt)} characters")
        
        # Get model configuration here so it is available both for the DB block below
        # and for the SSH tunnel call that follows.
        model_config = await get_model_config(model)
        model_id = model_config.get('model_id')
        
        # --- Phase 5: Create database records for prompt and generation config ---
        generation_config_id = None
        try:
            from app.database.session import get_session_factory
            from app.repositories import prompt_db_repository, generation_config_db_repository
            from app.repositories import video_db_repository, transcript_db_repository
            
            # Build system template (excludes DATA and USER_PROMPT sections)
            system_template = task.build_system_template(
                model_key=model,
                context={
                    "user_prompt": user_prompt,
                    "segments": segments,
                    "video_duration": video_duration,
                    "min_moment_length": min_moment_length,
                    "max_moment_length": max_moment_length,
                    "min_moments": min_moments,
                    "max_moments": max_moments,
                }
            )
            
            session_factory = get_session_factory()
            async with session_factory() as session:
                # Create or get prompt record
                prompt_record = await prompt_db_repository.create_or_get(
                    session, user_prompt, system_template
                )
                
                # Look up transcript_id
                transcript_id = None
                video_record = await video_db_repository.get_by_identifier(session, video_id)
                if video_record:
                    transcript_record = await transcript_db_repository.get_by_video_id(session, video_record.id)
                    if transcript_record:
                        transcript_id = transcript_record.id
                
                # Extract top_p and top_k from model config
                top_p = model_config.get('top_p')
                top_k = model_config.get('top_k')
                
                # Create or get generation config record
                config_record = await generation_config_db_repository.create_or_get(
                    session,
                    prompt_id=prompt_record.id,
                    model=model,
                    operation_type="generation",
                    transcript_id=transcript_id,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    min_moment_length=min_moment_length,
                    max_moment_length=max_moment_length,
                    min_moments=min_moments,
                    max_moments=max_moments,
                )
                await session.commit()
                generation_config_id = config_record.id
                logger.info(f"Created/retrieved generation config (id={generation_config_id})")
        except Exception as db_err:
            logger.warning(f"Failed to create DB records for generation config: {db_err}")
            # Non-fatal: pipeline continues even if DB record creation fails
        
        # Prepare messages for AI model
        messages = [{
            "role": "user",
            "content": complete_prompt
        }]
        
        logger.info(f"Calling AI model ({model}) for moment generation (async)...")
        ai_response = await call_ai_model_async(messages, model_key=model, model_id=model_id, temperature=temperature)
        
        if ai_response is None:
            raise Exception("AI model call failed or returned no response")
        
        # Extract model name from response
        model_name = extract_model_name(ai_response)
        logger.info(f"Using AI model: {model_name}")
        
        # Extract response content for logging
        response_content = ai_response.get('choices', [{}])[0].get('message', {}).get('content', '')
        
        # Parse response to extract moments
        logger.info("Parsing AI model response...")
        parsing_success = False
        parsing_error = None
        moments = []
        
        try:
            moments = task.parse_response(ai_response)
            parsing_success = True
            
            if not moments:
                raise Exception("No moments found in AI model response")
            
            logger.info(f"Parsed {len(moments)} moments from AI response")
        except Exception as parse_err:
            parsing_error = str(parse_err)
            logger.error(f"Error parsing moments: {parsing_error}")
            raise
        finally:
            # Log request/response for debugging
            model_url = await get_service_url(model)
            payload = {
                "messages": messages,
                "max_tokens": MAX_TOKENS,
                "temperature": temperature
            }
            if model_id:
                payload["model"] = model_id
            if 'top_p' in model_config:
                payload["top_p"] = model_config['top_p']
            if 'top_k' in model_config:
                payload["top_k"] = model_config['top_k']
            
            log_ai_request_response(
                operation="moment_generation_async",
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
                extracted_data=moments if parsing_success else None,
                request_id=get_request_id(),
            )
        
        # Create generation_config dictionary with all parameters
        generation_config = {
            "model": model,
            "temperature": temperature,
            "user_prompt": user_prompt,
            "complete_prompt": complete_prompt,
            "min_moment_length": min_moment_length,
            "max_moment_length": max_moment_length,
            "min_moments": min_moments,
            "max_moments": max_moments,
            "operation_type": "generation"
        }
        
        # Add model_name, generation_config, and generation_config_id to each moment
        for moment in moments:
            moment['model_name'] = model_name
            moment['generation_config'] = generation_config
            if generation_config_id is not None:
                moment['generation_config_id'] = generation_config_id
        
        # Validate moments against constraints
        validated_moments = []
        for i, moment in enumerate(moments):
            # Check moment duration
            duration = moment['end_time'] - moment['start_time']
            if duration < min_moment_length or duration > max_moment_length:
                logger.warning(f"Moment {i} duration {duration:.2f}s outside range [{min_moment_length:.2f}, {max_moment_length:.2f}], skipping")
                continue
            
            # Check bounds
            if moment['start_time'] < 0 or moment['end_time'] > video_duration:
                logger.warning(f"Moment {i} outside video bounds, skipping")
                continue
            
            # Check start < end
            if moment['end_time'] <= moment['start_time']:
                logger.warning(f"Moment {i} has invalid time range, skipping")
                continue
            
            validated_moments.append(moment)
        
        # Check number of moments constraint
        if len(validated_moments) < min_moments:
            logger.info(f"Generated {len(validated_moments)} moments (requested minimum: {min_moments})")
        elif len(validated_moments) > max_moments:
            logger.warning(f"{len(validated_moments)} valid moments found, but maximum is {max_moments}. Truncating to {max_moments}")
            validated_moments = validated_moments[:max_moments]
        
        # Check for overlaps
        validated_moments.sort(key=lambda x: x['start_time'])
        non_overlapping = []
        for moment in validated_moments:
            overlaps = False
            for existing in non_overlapping:
                if (moment['start_time'] < existing['end_time'] and 
                    moment['end_time'] > existing['start_time']):
                    overlaps = True
                    logger.warning(f"Moment '{moment['title']}' overlaps with '{existing['title']}', skipping")
                    break
            if not overlaps:
                non_overlapping.append(moment)
        
        validated_moments = non_overlapping
        
        if not validated_moments:
            raise Exception(
                f"Moment generation failed: AI generated {len(moments)} moment(s) "
                f"but none passed validation for video '{video_id}'. "
                f"Check bounds, duration, and overlap constraints."
            )
        
        # --- Phase 6: Bulk insert moments into database ---
        if validated_moments:
            try:
                from app.database.session import get_session_factory as _get_sf
                from app.repositories import moment_db_repository as moment_db_repo
                from app.repositories import video_db_repository as _video_db_repo
                from app.services.moments_service import generate_moment_id

                _sf = _get_sf()
                async with _sf() as db_session:
                    video_record = await _video_db_repo.get_by_identifier(db_session, video_id)
                    if video_record:
                        moments_data = []
                        for m in validated_moments:
                            if 'id' not in m or not m['id']:
                                m['id'] = generate_moment_id(m['start_time'], m['end_time'])
                            moments_data.append({
                                "identifier": m['id'],
                                "video_id": video_record.id,
                                "start_time": m['start_time'],
                                "end_time": m['end_time'],
                                "title": m['title'],
                                "is_refined": False,
                                "generation_config_id": generation_config_id,
                            })
                        await moment_db_repo.bulk_create(db_session, moments_data)
                        await db_session.commit()
                        logger.info(f"Saved {len(moments_data)} moments to database for {video_id}")
                    else:
                        logger.warning(f"Video '{video_id}' not found in DB, moments not saved to database")
            except Exception as db_err:
                logger.warning(f"Failed to save moments to database: {db_err}")

        duration = time.time() - start_time
        log_operation_complete(
            logger="app.services.ai.generation_service",
            function="process_moments_generation",
            operation=operation,
            message="Moment generation completed successfully (async)",
            context={
                "video_id": video_id,
                "moment_count": len(validated_moments),
                "duration_seconds": duration
            }
        )
        
        return {
            "moments": validated_moments,
            "generation_config_id": generation_config_id
        }
    
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.ai.generation_service",
            function="process_moments_generation",
            operation=operation,
            error=e,
            message="Error in moment generation (async)",
            context={
                "video_id": video_id,
                "duration_seconds": duration
            }
        )
        raise

