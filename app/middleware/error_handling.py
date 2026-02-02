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
        
        except Exception as e:
            # Import here to avoid circular dependencies
            from app.services.config_registry import ModelConfigNotFoundError
            
            # Handle model config not found (fail fast)
            if isinstance(e, ModelConfigNotFoundError):
                logger.error(
                    f"Model config not found: {e.model_key}",
                    extra={
                        "model_key": e.model_key,
                        "available_models": e.available_keys,
                        "path": request.url.path,
                        "method": request.method
                    }
                )
                
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "model_not_configured",
                        "message": str(e),
                        "model_key": e.model_key,
                        "available_models": e.available_keys,
                        "resolution": "Configure via Admin UI (/admin) or CLI: python -m app.cli.model_config seed",
                        "status_code": 503
                    }
                )
            
            # Handle custom application exceptions
            if isinstance(e, VideoMomentsException):
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

