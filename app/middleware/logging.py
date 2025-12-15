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


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to track request IDs and log all requests/responses."""
    
    async def dispatch(self, request: Request, call_next):
        """Process each request and log it."""
        # Generate or get request ID
        request_id = request.headers.get("X-Request-ID") or generate_request_id()
        set_request_id(request_id)
        
        # Store start time
        start_time = time.time()
        
        # Check if this is a status endpoint (skip verbose logging for these)
        is_status_endpoint = (
            "/refinement-status/" in request.url.path or 
            "/generation-status" in request.url.path or
            "/clip-extraction-status" in request.url.path
        )
        
        # Skip verbose logging for status endpoints (they handle their own compact logging)
        if not is_status_endpoint:
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
            
            # Skip verbose logging for status endpoints
            if not is_status_endpoint:
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
            
            # Always log errors, even for status endpoints
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

