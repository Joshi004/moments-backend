"""
Request/response logging middleware.
"""
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.logging import (
    generate_request_id,
    set_request_id,
    log_event
)

# Path fragments that, when found in a GET request URL, suppress verbose logging.
# These are all endpoints the UI polls repeatedly to check status or refresh data.
_QUIET_PATH_FRAGMENTS = [
    "/refinement-status/",
    "/generation-status",
    "/clip-extraction-status",
    "/audio-extraction-status",
    "/transcription-status",
    "/health",
]

# Last URL segment values that mark a GET request as a routine data read.
_QUIET_LAST_SEGMENTS = {"url", "moments", "transcript"}


def _is_polling_endpoint(path: str, method: str) -> bool:
    """Return True for GET requests that are pure UI polling or routine data reads.

    These requests happen many times per second and add no meaningful information
    to the log file.  Errors from these endpoints are still always logged.
    """
    if method != "GET":
        return False

    # Exact match: video list
    if path == "/api/videos":
        return True

    for fragment in _QUIET_PATH_FRAGMENTS:
        if fragment in path:
            return True

    # /api/pipeline/{id}/status  and  /api/pipeline/{id}/history
    if "/pipeline/" in path and ("/status" in path or "/history" in path):
        return True

    # /api/videos/{id}/url, /api/videos/{id}/moments, /api/videos/{id}/transcript
    if path.startswith("/api/videos/") and path.rstrip("/").split("/")[-1] in _QUIET_LAST_SEGMENTS:
        return True

    return False


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to track request IDs and log all requests/responses."""
    
    async def dispatch(self, request: Request, call_next):
        """Process each request and log it."""
        # Generate or get request ID
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        set_request_id(request_id)
        
        # Store start time
        start_time = time.time()
        
        # Polling / routine-read endpoints are logged silently (errors still logged below)
        is_polling_endpoint = _is_polling_endpoint(request.url.path, request.method)

        if not is_polling_endpoint:
            # Log request received
            log_event(
                level="INFO",
                logger="app.middleware.logging",
                function="dispatch",
                operation="http_request",
                event="request_received",
                message=f"Request received: {request.method} {request.url.path}",
                context={
                    "method": request.method,
                    "path": request.url.path,
                    "query_params": dict(request.query_params),
                    "client_host": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent"),
                    "content_type": request.headers.get("content-type"),
                    "content_length": request.headers.get("content-length"),
                }
            )
        
        try:
            # Process request
            response = await call_next(request)
            
            # Calculate duration
            duration = time.time() - start_time
            
            if not is_polling_endpoint:
                # Log response
                log_event(
                    level="INFO",
                    logger="app.middleware.logging",
                    function="dispatch",
                    operation="http_request",
                    event="response_sent",
                    message=f"Response sent: {request.method} {request.url.path}",
                    context={
                        "status_code": response.status_code,
                        "duration_seconds": duration,
                        "response_headers": dict(response.headers),
                    }
                )
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            
            return response
            
        except Exception as e:
            # Calculate duration even on error
            duration = time.time() - start_time
            
            # Always log errors, even for polling endpoints
            log_event(
                level="ERROR",
                logger="app.middleware.logging",
                function="dispatch",
                operation="http_request",
                event="request_error",
                message=f"Request error: {request.method} {request.url.path}",
                context={
                    "duration_seconds": duration,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                },
                exc_info=e
            )
            raise

