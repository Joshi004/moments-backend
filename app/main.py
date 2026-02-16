import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.logging import setup_logging
from app.core.redis import get_async_redis_client, close_async_redis_client, async_health_check
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.error_handling import ErrorHandlingMiddleware
from app.api.endpoints import videos, moments, transcripts, clips, pipeline, generate_moments, delete, admin
from app.api.deps import cleanup_resources
from app.workers.pipeline_worker import ensure_pipeline_consumer_group, start_pipeline_worker

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
app.include_router(pipeline.router, prefix="/api", tags=["pipeline"])
app.include_router(generate_moments.router, prefix="/api", tags=["generate_moments"])
app.include_router(delete.router, prefix="/api", tags=["delete"])
app.include_router(admin.router, prefix="/api", tags=["admin"])

# Mount static files
thumbnails_dir = Path(__file__).parent.parent / "static" / "thumbnails"
thumbnails_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/thumbnails", StaticFiles(directory=str(thumbnails_dir)), name="thumbnails")

audio_dir = Path(__file__).parent.parent / "static" / "audios"
audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/audios", StaticFiles(directory=str(audio_dir)), name="audios")

# Transcripts are now served from database via API, not as static files
# JSON files kept on disk as backup only

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
    """Health check endpoint using async Redis and PostgreSQL."""
    redis_status = "connected" if await async_health_check() else "disconnected"
    
    # Database health check
    db_status = "disconnected"
    try:
        from app.database.session import get_async_session
        from sqlalchemy import text
        async for session in get_async_session():
            await session.execute(text("SELECT 1"))
            db_status = "connected"
            break
    except Exception:
        db_status = "disconnected"
    
    overall_status = "healthy" if redis_status == "connected" and db_status == "connected" else "degraded"
    return {"status": overall_status, "redis": redis_status, "database": db_status}


# Startup event to initialize Redis and Database
@app.on_event("startup")
async def startup_event():
    """Initialize async Redis connection, database, and pipeline worker on startup."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        await get_async_redis_client()
        logger.info("Async Redis client initialized successfully")
    except Exception as e:
        # Log error but don't prevent startup
        logger.error(f"Failed to initialize async Redis: {e}")
    
    # Initialize database connection pool
    try:
        from app.database.session import init_db
        await init_db()
        logger.info("Database connection pool initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
    
    # Auto-seed model configs if Redis is empty
    try:
        from app.services.config_registry import get_config_registry
        from app.utils.model_config import seed_default_configs
        
        registry = get_config_registry()
        registered_keys = await registry.get_registered_keys()
        
        if len(registered_keys) == 0:
            logger.info("No model configs in Redis - seeding defaults...")
            count = await seed_default_configs()
            logger.info(f"Seeded {count} default model configs")
        else:
            logger.info(f"Model configs already exist in Redis: {registered_keys}")
    except Exception as e:
        logger.error(f"Failed to seed model configs: {e}")
    
    # Initialize pipeline consumer group (now async)
    try:
        await ensure_pipeline_consumer_group()
        logger.info("Pipeline consumer group initialized")
    except Exception as e:
        logger.error(f"Failed to initialize pipeline consumer group: {e}")
    
    # Start pipeline worker if in worker mode
    if os.getenv("RUN_PIPELINE_WORKER", "false").lower() == "true":
        try:
            asyncio.create_task(start_pipeline_worker())
            logger.info("Pipeline worker started in background")
        except Exception as e:
            logger.error(f"Failed to start pipeline worker: {e}")


# Shutdown event to cleanup resources
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown."""
    import logging
    logger = logging.getLogger(__name__)
    
    cleanup_resources()
    
    # Close database connection pool
    try:
        from app.database.session import close_db
        await close_db()
        logger.info("Database connection pool closed")
    except Exception as e:
        logger.error(f"Failed to close database: {e}")
    
    await close_async_redis_client()
