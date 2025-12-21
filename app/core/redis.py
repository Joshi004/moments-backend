"""
Redis client singleton for distributed job tracking.
Provides connection pooling and health checking.
"""
import redis
from typing import Optional
import logging
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Global Redis client instance
_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """
    Get or create the Redis client singleton.
    Uses connection pooling for better performance.
    
    Returns:
        Redis client instance
        
    Raises:
        redis.ConnectionError: If unable to connect to Redis
    """
    global _redis_client
    
    if _redis_client is None:
        settings = get_settings()
        
        try:
            logger.info(
                f"Initializing Redis connection to {settings.redis_host}:{settings.redis_port}"
            )
            
            # Create connection pool
            pool = redis.ConnectionPool(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=settings.redis_password,
                decode_responses=True,  # Automatically decode bytes to strings
                max_connections=10,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            
            # Create Redis client with connection pool
            _redis_client = redis.Redis(connection_pool=pool)
            
            # Test connection
            _redis_client.ping()
            
            logger.info("Redis connection established successfully")
            
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error initializing Redis: {e}")
            raise
    
    return _redis_client


def close_redis_client():
    """
    Close the Redis client connection and cleanup resources.
    Should be called on application shutdown.
    """
    global _redis_client
    
    if _redis_client is not None:
        try:
            logger.info("Closing Redis connection")
            _redis_client.close()
            _redis_client = None
            logger.info("Redis connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")


def health_check() -> bool:
    """
    Check if Redis connection is healthy.
    
    Returns:
        True if Redis is accessible, False otherwise
    """
    try:
        client = get_redis_client()
        client.ping()
        return True
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        return False

