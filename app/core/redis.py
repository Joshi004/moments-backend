"""
Redis client singleton for distributed job tracking.
Provides async connection pooling and health checking.
"""
import redis
import redis.asyncio as aioredis
from typing import Optional
import logging
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Global async Redis client instance
_async_redis_client: Optional[aioredis.Redis] = None


async def get_async_redis_client() -> aioredis.Redis:
    """
    Get or create the async Redis client singleton.
    Uses connection pooling for better performance.
    
    This is the recommended way to access Redis in async contexts.
    It does not block the event loop.
    
    Returns:
        Async Redis client instance
        
    Raises:
        redis.ConnectionError: If unable to connect to Redis
    """
    global _async_redis_client
    
    if _async_redis_client is None:
        settings = get_settings()
        
        try:
            logger.info(
                f"Initializing async Redis connection to {settings.redis_host}:{settings.redis_port}"
            )
            
            # Create async connection pool
            pool = aioredis.ConnectionPool(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password,
                decode_responses=True,  # Automatically decode bytes to strings
                max_connections=10,
                socket_connect_timeout=5,
                socket_timeout=10,  # Must be > block timeout (5s) to prevent socket timeout during blocking reads
            )
            
            # Create async Redis client with connection pool
            _async_redis_client = aioredis.Redis(connection_pool=pool)
            
            # Test connection
            await _async_redis_client.ping()
            
            logger.info("Async Redis connection established successfully")
            
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to async Redis: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error initializing async Redis: {e}")
            raise
    
    return _async_redis_client


async def close_async_redis_client():
    """
    Close the async Redis client connection and cleanup resources.
    Should be called on application shutdown.
    """
    global _async_redis_client
    
    if _async_redis_client is not None:
        try:
            logger.info("Closing async Redis connection")
            await _async_redis_client.aclose()
            _async_redis_client = None
            logger.info("Async Redis connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing async Redis connection: {e}")


async def async_health_check() -> bool:
    """
    Check if async Redis connection is healthy.
    
    Returns:
        True if Redis is accessible, False otherwise
    """
    try:
        client = await get_async_redis_client()
        await client.ping()
        return True
    except Exception as e:
        logger.error(f"Async Redis health check failed: {e}")
        return False

