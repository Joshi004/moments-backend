"""
Centralized logging configuration for structured JSON logging.
Provides helpers for consistent structured logging across the application.
"""

import json
import logging
import logging.handlers
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextvars import ContextVar
from typing import Optional, Dict, Any
import traceback
import functools

# Context variable to store request ID for the current request
_request_id: ContextVar[Optional[str]] = ContextVar('request_id', default=None)

# Context variable to store operation name
_operation: ContextVar[Optional[str]] = ContextVar('operation', default=None)

# Global logger instance
_logger: Optional[logging.Logger] = None


class StructuredJSONFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON logs."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add request ID if available
        request_id = _request_id.get()
        if request_id:
            log_data["request_id"] = request_id
        
        # Add operation if available
        operation = _operation.get()
        if operation:
            log_data["operation"] = operation
        
        # Add event type if present
        if hasattr(record, 'event'):
            log_data["event"] = record.event
        
        # Add message
        log_data["message"] = record.getMessage()
        
        # Add context if present
        if hasattr(record, 'context') and record.context:
            log_data["context"] = record.context
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info)
            }
        
        return json.dumps(log_data, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """Custom formatter that outputs human-readable unstructured logs."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as human-readable text."""
        # Format timestamp
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Build main log line
        level = record.levelname
        logger_name = record.name
        function_name = record.funcName
        message = record.getMessage()
        
        # Format main line
        log_lines = [f"{timestamp} {level:8s} [{logger_name}] {function_name}() - {message}"]
        
        # Add request ID if available
        request_id = _request_id.get()
        if request_id:
            log_lines.append(f"  request_id: {request_id}")
        
        # Add operation if available
        operation = _operation.get()
        if operation:
            log_lines.append(f"  operation: {operation}")
        
        # Add event type if present
        if hasattr(record, 'event') and record.event:
            log_lines.append(f"  event: {record.event}")
        
        # Add context if present
        if hasattr(record, 'context') and record.context:
            context = record.context
            if isinstance(context, dict):
                for key, value in context.items():
                    # Format value for readability
                    if isinstance(value, (dict, list)):
                        value_str = json.dumps(value, indent=2, ensure_ascii=False, default=str)
                        # Indent multi-line values
                        indented_value = '\n'.join('    ' + line for line in value_str.split('\n'))
                        log_lines.append(f"  {key}:")
                        log_lines.append(indented_value)
                    else:
                        # Truncate very long values
                        value_str = str(value)
                        if len(value_str) > 500:
                            value_str = value_str[:500] + "... (truncated)"
                        log_lines.append(f"  {key}: {value_str}")
            else:
                log_lines.append(f"  context: {context}")
        
        # Add exception info if present
        if record.exc_info:
            exc_type, exc_value, exc_traceback = record.exc_info
            log_lines.append(f"  exception_type: {exc_type.__name__ if exc_type else 'Unknown'}")
            log_lines.append(f"  exception_message: {str(exc_value) if exc_value else 'N/A'}")
            
            # Add traceback
            if exc_traceback:
                tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
                log_lines.append("  traceback:")
                for tb_line in tb_lines:
                    # Indent each traceback line
                    for line in tb_line.rstrip().split('\n'):
                        log_lines.append(f"    {line}")
        
        return '\n'.join(log_lines)


def setup_logging(log_level: str = "INFO", log_dir: Optional[Path] = None) -> None:
    """
    Initialize the logging system with dual file output:
    - Structured JSON logs for LLM analysis
    - Unstructured human-readable logs for developers
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files. If None, uses moments-backend/logs/
    """
    global _logger
    
    # Determine log directory
    if log_dir is None:
        current_file = Path(__file__).resolve()
        backend_dir = current_file.parent.parent.parent
        log_dir = backend_dir / "logs"
    else:
        log_dir = Path(log_dir)
    
    # Create log directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Create JSON file handler with rotation (for structured logs)
    json_log_file = log_dir / "application.log.json"
    json_file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(json_log_file),
        when='midnight',
        interval=1,
        backupCount=30,  # Keep 30 days of logs
        encoding='utf-8'
    )
    json_file_handler.setLevel(getattr(logging, log_level.upper()))
    json_file_handler.setFormatter(StructuredJSONFormatter())
    
    # Create unstructured file handler with rotation (for human-readable logs)
    unstructured_log_file = log_dir / "application.log"
    unstructured_file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(unstructured_log_file),
        when='midnight',
        interval=1,
        backupCount=30,  # Keep 30 days of logs
        encoding='utf-8'
    )
    unstructured_file_handler.setLevel(getattr(logging, log_level.upper()))
    unstructured_file_handler.setFormatter(HumanReadableFormatter())
    
    # Add handlers (no console handler - logs only go to files)
    root_logger.addHandler(json_file_handler)
    root_logger.addHandler(unstructured_file_handler)
    
    _logger = root_logger
    
    # Log initialization
    log_event(
        level="INFO",
        logger="app.core.logging",
        function="setup_logging",
        operation="logging_setup",
        event="logging_initialized",
        message="Logging system initialized",
        context={
            "log_level": log_level,
            "log_dir": str(log_dir),
            "json_log_file": str(json_log_file),
            "unstructured_log_file": str(unstructured_log_file)
        }
    )


def get_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return _request_id.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID in context."""
    _request_id.set(request_id)


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return f"req_{uuid.uuid4().hex[:12]}"


def set_operation(operation: str) -> None:
    """Set the current operation name in context."""
    _operation.set(operation)


def get_operation() -> Optional[str]:
    """Get the current operation name from context."""
    return _operation.get()


def log_event(
    level: str,
    logger: str,
    function: str,
    operation: Optional[str] = None,
    event: Optional[str] = None,
    message: str = "",
    context: Optional[Dict[str, Any]] = None,
    exc_info: Optional[Exception] = None
) -> None:
    """
    Log a structured event.
    
    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        logger: Logger name (usually module path)
        function: Function name where log originated
        operation: High-level operation name
        event: Specific event type
        message: Human-readable message
        context: Operation-specific data
        exc_info: Exception info to include
    """
    logger_instance = logging.getLogger(logger)
    log_method = getattr(logger_instance, level.lower(), logger_instance.info)
    
    # Create log record with extra fields
    extra = {}
    if event:
        extra['event'] = event
    if context:
        extra['context'] = context
    
    # Temporarily set operation in context if provided
    if operation:
        old_operation = _operation.get()
        _operation.set(operation)
        try:
            log_method(message, extra=extra, exc_info=exc_info)
        finally:
            if old_operation:
                _operation.set(old_operation)
            else:
                _operation.set(None)
    else:
        log_method(message, extra=extra, exc_info=exc_info)


def log_operation_start(
    logger: str,
    function: str,
    operation: str,
    message: str = "",
    context: Optional[Dict[str, Any]] = None
) -> None:
    """Log the start of an operation."""
    set_operation(operation)
    log_event(
        level="INFO",
        logger=logger,
        function=function,
        operation=operation,
        event="operation_start",
        message=message or f"Starting {operation}",
        context=context
    )


def log_operation_complete(
    logger: str,
    function: str,
    operation: str,
    message: str = "",
    context: Optional[Dict[str, Any]] = None,
    duration: Optional[float] = None
) -> None:
    """Log the completion of an operation."""
    if context is None:
        context = {}
    if duration is not None:
        context["duration_seconds"] = duration
    
    log_event(
        level="INFO",
        logger=logger,
        function=function,
        operation=operation,
        event="operation_complete",
        message=message or f"Completed {operation}",
        context=context
    )


def log_operation_error(
    logger: str,
    function: str,
    operation: str,
    error: Exception,
    message: str = "",
    context: Optional[Dict[str, Any]] = None
) -> None:
    """Log an operation error."""
    if context is None:
        context = {}
    
    context["error_type"] = type(error).__name__
    context["error_message"] = str(error)
    
    log_event(
        level="ERROR",
        logger=logger,
        function=function,
        operation=operation,
        event="operation_error",
        message=message or f"Error in {operation}",
        context=context,
        exc_info=error
    )


def operation_logger(operation_name: str):
    """
    Decorator to automatically log operation start/complete/error.
    
    Usage:
        @operation_logger("audio_extraction")
        def extract_audio(...):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger_name = func.__module__
            function_name = func.__name__
            
            # Log operation start
            log_operation_start(
                logger=logger_name,
                function=function_name,
                operation=operation_name,
                context={
                    "args": str(args)[:500] if args else None,
                    "kwargs": {k: str(v)[:200] for k, v in kwargs.items()} if kwargs else None
                }
            )
            
            start_time = datetime.now(timezone.utc)
            try:
                result = func(*args, **kwargs)
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                
                # Log operation complete
                log_operation_complete(
                    logger=logger_name,
                    function=function_name,
                    operation=operation_name,
                    context={"result_type": type(result).__name__},
                    duration=duration
                )
                
                return result
            except Exception as e:
                duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                
                # Log operation error
                log_operation_error(
                    logger=logger_name,
                    function=function_name,
                    operation=operation_name,
                    error=e,
                    context={"duration_seconds": duration}
                )
                raise
        
        return wrapper
    return decorator


def log_status_check(
    endpoint_type: str,  # "refinement" or "generation"
    video_id: str,
    moment_id: Optional[str],
    status: str,  # "processing", "completed", "failed", etc.
    status_code: int,
    duration: float
) -> None:
    """Log a compact one-line status check."""
    logger = logging.getLogger("app.routes.videos")
    
    if moment_id:
        path = f"/{endpoint_type}-status/{moment_id}"
    else:
        path = f"/{endpoint_type}-status"
    
    # Single compact line: GET /refinement-status/abc123 -> 200 OK (failed) [0.001s]
    logger.info(
        f"GET {path} -> {status_code} OK ({status}) [{duration:.3f}s]"
    )

