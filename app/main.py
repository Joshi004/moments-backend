from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.logging import setup_logging
from app.core.redis import get_redis_client, close_redis_client, health_check
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.error_handling import ErrorHandlingMiddleware
from app.api.endpoints import videos, moments, transcripts, clips
from app.api.deps import cleanup_resources

# Initialize logging system
setup_logging(log_level="INFO")

app = FastAPI(title="Video Moments API", version="1.0.0")

# Add middleware (order matters - last added runs first)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(ErrorHandlingMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers - keep same paths for backward compatibility
app.include_router(videos.router, prefix="/api", tags=["videos"])
app.include_router(moments.router, prefix="/api", tags=["moments"])
app.include_router(transcripts.router, prefix="/api", tags=["transcripts"])
app.include_router(clips.router, prefix="/api", tags=["clips"])

# Mount static files
thumbnails_dir = Path(__file__).parent.parent / "static" / "thumbnails"
thumbnails_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/thumbnails", StaticFiles(directory=str(thumbnails_dir)), name="thumbnails")

audio_dir = Path(__file__).parent.parent / "static" / "audios"
audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/audios", StaticFiles(directory=str(audio_dir)), name="audios")

transcripts_dir = Path(__file__).parent.parent / "static" / "transcripts"
transcripts_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/transcripts", StaticFiles(directory=str(transcripts_dir)), name="transcripts")

moment_clips_dir = Path(__file__).parent.parent / "static" / "moment_clips"
moment_clips_dir.mkdir(parents=True, exist_ok=True)
app.mount("/moment_clips", StaticFiles(directory=str(moment_clips_dir)), name="moment_clips")


# Root and health endpoints
@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Video Moments API", "version": "1.0.0"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    redis_status = "connected" if health_check() else "disconnected"
    return {"status": "healthy", "redis": redis_status}


# Startup event to initialize Redis
@app.on_event("startup")
async def startup_event():
    """Initialize Redis connection on startup."""
    try:
        get_redis_client()
    except Exception as e:
        # Log error but don't prevent startup
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to initialize Redis: {e}")


# Shutdown event to cleanup resources
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown."""
    cleanup_resources()
    close_redis_client()


