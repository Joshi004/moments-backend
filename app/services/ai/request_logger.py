import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def get_ai_requests_directory() -> Path:
    """Get or create the AI requests log directory."""
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent.parent
    logs_dir = backend_dir / "logs" / "ai_requests"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def log_ai_request_response(
    operation: str,
    video_id: str,
    model_key: str,
    model_name: str,
    model_id: Optional[str],
    model_url: str,
    request_payload: Dict[str, Any],
    response_status_code: int,
    response_data: Dict[str, Any],
    response_content: str,
    duration_seconds: float,
    parsing_success: bool,
    parsing_error: Optional[str] = None,
    extracted_data: Optional[Any] = None,
    request_id: Optional[str] = None,
) -> Optional[Path]:
    """
    Log an AI model request and response to a JSON file.
    
    Args:
        operation: Type of operation (moment_generation, moment_refinement)
        video_id: ID of the video being processed
        model_key: Model identifier (e.g., 'qwen3_vl_fp8')
        model_name: Human-readable model name
        model_id: Model ID sent to API (if any)
        model_url: Full URL of the API endpoint
        request_payload: Complete request payload sent to API
        response_status_code: HTTP status code from response
        response_data: Complete response JSON from API
        response_content: Extracted content from response
        duration_seconds: Time taken for the API call
        parsing_success: Whether parsing was successful
        parsing_error: Error message if parsing failed
        extracted_data: Parsed/extracted data (moments or timestamps)
        request_id: Request ID for tracing
    
    Returns:
        Path to the created log file, or None if logging failed
    """
    try:
        logs_dir = get_ai_requests_directory()
        
        # Create timestamp for filename and record
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime("%Y-%m-%d_%H-%M-%S")
        
        # Generate filename
        filename = f"{timestamp_str}_{model_key}_{operation}_{video_id}.json"
        log_file = logs_dir / filename
        
        # Build log structure
        log_data = {
            "timestamp": timestamp.isoformat(),
            "request_id": request_id,
            "operation": operation,
            "video_id": video_id,
            "model": {
                "key": model_key,
                "name": model_name,
                "model_id": model_id,
                "url": model_url,
            },
            "request": {
                "payload": request_payload,
                "prompt_length": len(request_payload.get("messages", [{}])[0].get("content", "")),
                "message_count": len(request_payload.get("messages", [])),
            },
            "response": {
                "status_code": response_status_code,
                "raw_response": response_data,
                "content": response_content,
                "content_length": len(response_content),
                "duration_seconds": duration_seconds,
            },
            "parsing": {
                "success": parsing_success,
                "error": parsing_error,
                "extracted_data": extracted_data,
                "data_count": len(extracted_data) if isinstance(extracted_data, list) else (1 if extracted_data else 0),
            }
        }
        
        # Write to file
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"AI request/response logged to: {log_file}")
        return log_file
        
    except Exception as e:
        logger.error(f"Error logging AI request/response: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None

