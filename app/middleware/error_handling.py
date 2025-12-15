"""
Error handling middleware that converts exceptions to HTTP responses.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.exceptions import VideoMomentsException
import logging

logger = logging.getLogger(__name__)


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware to convert exceptions to appropriate HTTP responses."""
    
    async def dispatch(self, request: Request, call_next):
        """Process each request and handle exceptions."""
        try:
            response = await call_next(request)
            return response
            
        except VideoMomentsException as e:
            # Handle custom application exceptions
            logger.error(
                f"Application error: {e.message}",
                extra={
                    "status_code": e.status_code,
                    "path": request.url.path,
                    "method": request.method
                }
            )
            
            return JSONResponse(
                status_code=e.status_code,
                content={
                    "error": e.message,
                    "status_code": e.status_code
                }
            )
            
        except Exception as e:
            # Handle unexpected exceptions
            logger.error(
                f"Unexpected error: {str(e)}",
                exc_info=True,
                extra={
                    "path": request.url.path,
                    "method": request.method
                }
            )
            
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "detail": str(e),
                    "status_code": 500
                }
            )

