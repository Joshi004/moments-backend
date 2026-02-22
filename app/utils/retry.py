"""
Retry utility with exponential backoff for transient errors.
Used primarily for GCS upload operations.
"""
import asyncio
import logging
import time
from typing import Callable, TypeVar
from functools import wraps
from google.api_core import exceptions as google_exceptions
import requests

logger = logging.getLogger(__name__)

T = TypeVar('T')


def is_transient_error(error: Exception) -> bool:
    """
    Determine if an error is transient and should be retried.
    
    Transient errors include:
    - Network timeouts
    - HTTP 5xx server errors
    - Connection errors
    - Google API transient errors
    """
    # Google Cloud transient errors
    if isinstance(error, (
        google_exceptions.ServiceUnavailable,
        google_exceptions.DeadlineExceeded,
        google_exceptions.InternalServerError,
        google_exceptions.TooManyRequests,
    )):
        return True
    
    # Network/connection errors
    if isinstance(error, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        TimeoutError,
        ConnectionError,
    )):
        return True
    
    # Check HTTP status codes in exception message
    error_str = str(error).lower()
    if any(code in error_str for code in ['500', '502', '503', '504', '429']):
        return True
    
    return False


async def retry_with_backoff(
    func: Callable[..., T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    operation_name: str = "operation",
    *args,
    **kwargs
) -> T:
    """
    Retry an async function with exponential backoff.
    
    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds (exponentially increased)
        operation_name: Name of operation for logging
        *args: Arguments to pass to func
        **kwargs: Keyword arguments to pass to func
    
    Returns:
        Result from successful function call
    
    Raises:
        Exception: The last exception if all retries fail
    
    Retry delays: base_delay * (2 ** attempt)
    - Attempt 0 (first try): no delay
    - Attempt 1 (first retry): base_delay = 1s
    - Attempt 2 (second retry): base_delay * 2 = 2s
    - Attempt 3 (third retry): base_delay * 4 = 4s
    """
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            # Execute the function
            result = await func(*args, **kwargs)
            
            # Log success if this was a retry
            if attempt > 0:
                logger.info(
                    f"{operation_name} succeeded on attempt {attempt + 1}/{max_retries + 1}"
                )
            
            return result
            
        except Exception as e:
            last_exception = e
            
            # Check if this is the last attempt
            if attempt >= max_retries:
                logger.error(
                    f"{operation_name} failed after {max_retries + 1} attempts: {type(e).__name__}: {e}"
                )
                raise
            
            # Check if error is transient
            if not is_transient_error(e):
                logger.error(
                    f"{operation_name} failed with non-transient error: {type(e).__name__}: {e}"
                )
                raise
            
            # Calculate delay (exponential backoff)
            delay = base_delay * (2 ** attempt)
            
            logger.warning(
                f"{operation_name} attempt {attempt + 1}/{max_retries + 1} failed: "
                f"{type(e).__name__}: {e}. Retrying in {delay}s..."
            )
            
            # Wait before retrying
            await asyncio.sleep(delay)
    
    # Should never reach here, but just in case
    if last_exception:
        raise last_exception


def retry_sync(
    max_retries: int = 3,
    base_delay: float = 1.0,
    operation_name: str = "operation"
):
    """
    Decorator for synchronous functions with retry logic.
    
    Usage:
        @retry_sync(max_retries=3, base_delay=1.0, operation_name="upload")
        def upload_file(path):
            # ... upload logic
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    
                    if attempt > 0:
                        logger.info(
                            f"{operation_name} succeeded on attempt {attempt + 1}/{max_retries + 1}"
                        )
                    
                    return result
                    
                except Exception as e:
                    last_exception = e
                    
                    if attempt >= max_retries:
                        logger.error(
                            f"{operation_name} failed after {max_retries + 1} attempts: "
                            f"{type(e).__name__}: {e}"
                        )
                        raise
                    
                    if not is_transient_error(e):
                        logger.error(
                            f"{operation_name} failed with non-transient error: "
                            f"{type(e).__name__}: {e}"
                        )
                        raise
                    
                    delay = base_delay * (2 ** attempt)
                    
                    logger.warning(
                        f"{operation_name} attempt {attempt + 1}/{max_retries + 1} failed: "
                        f"{type(e).__name__}: {e}. Retrying in {delay}s..."
                    )
                    
                    time.sleep(delay)
            
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator




