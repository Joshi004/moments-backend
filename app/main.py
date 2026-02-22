import os
import asyncio
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

# All media is now served via GCS signed URLs through API endpoints (Phases 3, 7, 8).
# Transcripts and moments are served from the database (Phases 4, 6).
# No static file mounts remain -- all intermediate files go to temp/ (Phase 11).


# Background task handle for the temp file cleanup scheduler
_cleanup_task: asyncio.Task = None


async def _cleanup_scheduler() -> None:
    """
    Background asyncio task that periodically removes old temp files.

    Runs every temp_cleanup_interval_hours (default 6h), deletes files
    older than temp_max_age_hours (default 24h). Errors are logged but
    never crash the scheduler -- the next iteration will retry.
    """
    from app.core.config import get_settings
    from app.services.temp_file_manager import cleanup_old_files

    settings = get_settings()
    interval_seconds = settings.temp_cleanup_interval_hours * 3600
    _logger = logging.getLogger(__name__)

    _logger.info(
        f"Temp cleanup scheduler started "
        f"(interval={settings.temp_cleanup_interval_hours}h, "
        f"max_age={settings.temp_max_age_hours}h)"
    )

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            result = await cleanup_old_files(max_age_hours=settings.temp_max_age_hours)
            _logger.info(
                f"Temp cleanup: deleted {result['files_deleted']} files, "
                f"freed {result['bytes_freed'] / (1024 ** 3):.2f} GB, "
                f"removed {result['dirs_removed']} dirs ({result['duration_ms']}ms)"
            )
        except asyncio.CancelledError:
            _logger.info("Temp cleanup scheduler stopped.")
            break
        except Exception as exc:
            _logger.error(f"Temp cleanup scheduler error: {exc}", exc_info=True)
            # Continue -- next iteration will retry after the configured interval


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

    # Start temp file cleanup scheduler
    global _cleanup_task
    try:
        _cleanup_task = asyncio.create_task(_cleanup_scheduler())
        logger.info("Temp file cleanup scheduler started")
    except Exception as e:
        logger.error(f"Failed to start temp cleanup scheduler: {e}")


# Shutdown event to cleanup resources
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown."""
    import logging
    logger = logging.getLogger(__name__)
    
    # Cancel temp cleanup scheduler
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
        logger.info("Temp file cleanup scheduler stopped")

    cleanup_resources()

    # Close database connection pool
    try:
        from app.database.session import close_db
        await close_db()
        logger.info("Database connection pool closed")
    except Exception as e:
        logger.error(f"Failed to close database: {e}")

    await close_async_redis_client()
