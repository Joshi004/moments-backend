from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from app.routes import videos
from app.utils.logging_config import (
    setup_logging,
    generate_request_id,
    set_request_id,
    log_event
)
from pathlib import Path
import time
import json

# Initialize logging system
setup_logging(log_level="INFO")

app = FastAPI(title="Video Moments API", version="1.0.0")

# Configure CORS - Allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Middleware to track request IDs and log all requests/responses."""
    # Generate or get request ID
    request_id = request.headers.get("X-Request-ID") or generate_request_id()
    set_request_id(request_id)
    
    # Store start time
    start_time = time.time()
    
    # Check if this is a status endpoint (skip verbose logging for these)
    is_status_endpoint = (
        "/refinement-status/" in request.url.path or 
        "/generation-status" in request.url.path
    )
    
    # Skip verbose logging for status endpoints (they handle their own compact logging)
    if not is_status_endpoint:
        # Log request received
        log_event(
            level="INFO",
            logger="app.main",
            function="request_logging_middleware",
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
                logger="app.main",
                function="request_logging_middleware",
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
            logger="app.main",
            function="request_logging_middleware",
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

# Include routers
app.include_router(videos.router, prefix="/api", tags=["videos"])

# Mount static files for thumbnails
thumbnails_dir = Path(__file__).parent.parent / "static" / "thumbnails"
thumbnails_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/thumbnails", StaticFiles(directory=str(thumbnails_dir)), name="thumbnails")

# Mount static files for audio
audio_dir = Path(__file__).parent.parent / "static" / "audios"
audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/audios", StaticFiles(directory=str(audio_dir)), name="audios")

# Mount static files for transcripts
transcripts_dir = Path(__file__).parent.parent / "static" / "transcripts"
transcripts_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/transcripts", StaticFiles(directory=str(transcripts_dir)), name="transcripts")


@app.get("/")
async def root():
    return {"message": "Video Moments API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


