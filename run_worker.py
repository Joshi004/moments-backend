#!/usr/bin/env python3
"""
Dedicated pipeline worker script.
Runs the pipeline worker independently of the API server.
"""
import asyncio
import signal
import sys
import logging

# Setup logging first
from app.core.logging import setup_logging
setup_logging(log_level="INFO")

from app.core.redis import get_redis_client
from app.workers.pipeline_worker import start_pipeline_worker

logger = logging.getLogger(__name__)

def handle_shutdown(signum, frame):
    """Handle shutdown signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down worker...")
    sys.exit(0)

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    logger.info("Starting dedicated pipeline worker...")
    
    # Verify Redis connection
    try:
        redis = get_redis_client()
        redis.ping()
        logger.info("Redis connection verified")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        logger.error("Please ensure Redis is running and accessible")
        sys.exit(1)
    
    # Run worker
    try:
        asyncio.run(start_pipeline_worker())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.exception(f"Worker crashed: {e}")
        sys.exit(1)





