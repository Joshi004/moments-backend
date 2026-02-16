#!/usr/bin/env python3
"""
Dedicated pipeline worker script.
Runs the pipeline worker independently of the API server.
"""
import asyncio
import signal
import sys
import logging
import os
from pathlib import Path

# Setup logging first
from app.core.logging import setup_logging
setup_logging(log_level="INFO")

from app.core.redis import get_async_redis_client
from app.workers.pipeline_worker import start_pipeline_worker

logger = logging.getLogger(__name__)

# PID file path
PID_FILE = Path(__file__).parent / "worker.pid"

def acquire_pid_lock():
    """Ensure only one worker instance runs."""
    if PID_FILE.exists():
        # Check if the process is actually running
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            
            # Check if process exists
            try:
                os.kill(old_pid, 0)  # Signal 0 just checks if process exists
                logger.error(f"Worker already running with PID {old_pid}")
                logger.error(f"If you're sure it's not running, delete {PID_FILE}")
                sys.exit(1)
            except OSError:
                # Process doesn't exist, remove stale PID file
                logger.warning(f"Removing stale PID file (process {old_pid} not found)")
                PID_FILE.unlink()
        except Exception as e:
            logger.warning(f"Could not read PID file: {e}, removing it")
            PID_FILE.unlink()
    
    # Write our PID
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    logger.info(f"Acquired PID lock: {os.getpid()}")

def release_pid_lock():
    """Release PID file on shutdown."""
    if PID_FILE.exists():
        PID_FILE.unlink()
        logger.info("Released PID lock")

def handle_shutdown(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down worker...")
    release_pid_lock()
    sys.exit(0)

if __name__ == "__main__":
    # Acquire PID lock first
    acquire_pid_lock()
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    logger.info("Starting dedicated pipeline worker...")
    
    async def verify_and_run():
        """Verify Redis connection and run worker."""
        # Verify Redis connection
        try:
            redis = await get_async_redis_client()
            await redis.ping()
            logger.info("Async Redis connection verified")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            logger.error("Please ensure Redis is running and accessible")
            release_pid_lock()
            sys.exit(1)
        
        # Initialize database connection (worker runs as separate process)
        try:
            from app.database.session import init_db
            await init_db()
            logger.info("Database connection initialized")
        except Exception as e:
            logger.error(f"Database initialization failed: {e}")
            logger.error("Please ensure PostgreSQL is running and accessible")
            release_pid_lock()
            sys.exit(1)
        
        # Run worker
        await start_pipeline_worker()
    
    # Run worker
    try:
        asyncio.run(verify_and_run())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.exception(f"Worker crashed: {e}")
    finally:
        release_pid_lock()






