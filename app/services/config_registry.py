"""
Redis-backed model configuration registry.
Provides dynamic configuration management for AI models.

All methods are async for non-blocking Redis operations.
"""
import logging
from datetime import datetime
from typing import Optional, Dict, List
from app.core.redis import get_async_redis_client

logger = logging.getLogger(__name__)


class ModelConfigNotFoundError(Exception):
    """Raised when model config is not found in Redis."""
    
    def __init__(self, model_key: str, available_keys: List[str]):
        self.model_key = model_key
        self.available_keys = available_keys
        super().__init__(
            f"Model '{model_key}' not configured in Redis. "
            f"Available models: {available_keys}. "
            f"Use Admin UI (/admin) or CLI to configure: "
            f"python -m app.cli.model_config seed"
        )


class ConfigRegistry:
    """Redis-backed model configuration registry with async operations."""
    
    KEY_PREFIX = "model:config:"
    KEYS_SET = "model:config:_keys"
    
    def __init__(self):
        """Initialize registry. Redis client is fetched async in each method."""
        self._redis = None
    
    async def _get_redis(self):
        """Get async Redis client."""
        if self._redis is None:
            self._redis = await get_async_redis_client()
        return self._redis
    
    def _get_key(self, model_key: str) -> str:
        """Get Redis key for a model config."""
        return f"{self.KEY_PREFIX}{model_key}"
    
    def _serialize_value(self, value) -> str:
        """Serialize a value for Redis storage."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    
    def _deserialize_value(self, key: str, value: str):
        """Deserialize a value from Redis."""
        if value == "":
            return None
        
        # Boolean fields
        if key in ["supports_video"]:
            return value.lower() == "true"
        
        # Integer fields
        if key in ["ssh_local_port", "ssh_remote_port", "top_k"]:
            return int(value) if value else None
        
        # Float fields
        if key in ["top_p"]:
            return float(value) if value else None
        
        # String fields
        return value
    
    async def get_config(self, model_key: str) -> Dict:
        """
        Get full config for a model.
        
        Args:
            model_key: Model identifier (e.g., "minimax", "qwen3_vl_fp8")
            
        Returns:
            Dictionary with model configuration
            
        Raises:
            ModelConfigNotFoundError: If model not configured in Redis
        """
        redis = await self._get_redis()
        redis_key = self._get_key(model_key)
        config_data = await redis.hgetall(redis_key)
        
        if not config_data:
            available_keys = await self.get_registered_keys()
            logger.error(
                f"Model config not found in Redis: {model_key}. "
                f"Available: {available_keys}"
            )
            raise ModelConfigNotFoundError(model_key, available_keys)
        
        # Deserialize values
        config = {}
        for key, value in config_data.items():
            config[key] = self._deserialize_value(key, value)
        
        logger.debug(f"Retrieved config for {model_key} from Redis")
        return config
    
    async def set_config(self, model_key: str, config: Dict) -> None:
        """
        Set/update full config for a model.
        
        Args:
            model_key: Model identifier
            config: Dictionary with configuration fields
        """
        redis = await self._get_redis()
        redis_key = self._get_key(model_key)
        
        # Add timestamp
        config_with_timestamp = config.copy()
        config_with_timestamp["updated_at"] = datetime.utcnow().isoformat()
        
        # Serialize all values
        serialized_config = {
            k: self._serialize_value(v) 
            for k, v in config_with_timestamp.items()
        }
        
        # Store in Redis hash
        await redis.hset(redis_key, mapping=serialized_config)
        
        # Add to keys set
        await redis.sadd(self.KEYS_SET, model_key)
        
        logger.info(f"Stored config for {model_key} in Redis")
    
    async def update_config(self, model_key: str, updates: Dict) -> Dict:
        """
        Partially update a model config.
        
        Args:
            model_key: Model identifier
            updates: Dictionary with fields to update
            
        Returns:
            Updated configuration
            
        Raises:
            ModelConfigNotFoundError: If model not found
        """
        # Get existing config
        existing_config = await self.get_config(model_key)
        
        # Apply updates
        existing_config.update(updates)
        
        # Save back
        await self.set_config(model_key, existing_config)
        
        return existing_config
    
    async def delete_config(self, model_key: str) -> bool:
        """
        Delete a model config.
        
        Args:
            model_key: Model identifier
            
        Returns:
            True if config existed and was deleted, False otherwise
        """
        redis = await self._get_redis()
        redis_key = self._get_key(model_key)
        
        # Check if exists
        exists = await redis.exists(redis_key)
        
        if exists:
            # Delete hash
            await redis.delete(redis_key)
            
            # Remove from keys set
            await redis.srem(self.KEYS_SET, model_key)
            
            logger.info(f"Deleted config for {model_key} from Redis")
            return True
        
        return False
    
    async def list_configs(self) -> List[Dict]:
        """
        List all configured models with their settings.
        
        Returns:
            List of dictionaries with model_key and config fields
        """
        model_keys = await self.get_registered_keys()
        configs = []
        
        for model_key in model_keys:
            try:
                config = await self.get_config(model_key)
                config["model_key"] = model_key
                configs.append(config)
            except ModelConfigNotFoundError:
                # Key in set but no config (shouldn't happen, but handle it)
                logger.warning(f"Model key {model_key} in set but no config found")
                continue
        
        return configs
    
    async def get_registered_keys(self) -> List[str]:
        """
        Get list of all registered model keys.
        
        Returns:
            List of model key strings
        """
        redis = await self._get_redis()
        keys = await redis.smembers(self.KEYS_SET)
        return sorted(list(keys))
    
    async def seed_from_defaults(self, defaults: Dict, force: bool = False) -> int:
        """
        Seed Redis from default config dictionary.
        
        Args:
            defaults: Dictionary mapping model_key -> config
            force: If True, overwrite existing configs
            
        Returns:
            Number of configs seeded
        """
        redis = await self._get_redis()
        count = 0
        
        for model_key, config in defaults.items():
            redis_key = self._get_key(model_key)
            exists = await redis.exists(redis_key)
            
            if not exists or force:
                await self.set_config(model_key, config)
                count += 1
                logger.info(
                    f"Seeded config for {model_key} "
                    f"({'overwritten' if exists else 'created'})"
                )
            else:
                logger.debug(f"Config for {model_key} already exists, skipping")
        
        return count
    
    async def clear_all(self) -> int:
        """
        Clear all model configs from Redis.
        WARNING: This is destructive!
        
        Returns:
            Number of configs cleared
        """
        keys = await self.get_registered_keys()
        count = 0
        
        for model_key in keys:
            if await self.delete_config(model_key):
                count += 1
        
        logger.warning(f"Cleared all {count} model configs from Redis")
        return count


# Singleton instance
_registry: Optional[ConfigRegistry] = None


def get_config_registry() -> ConfigRegistry:
    """
    Get or create the config registry singleton.
    
    Note: The registry methods are async, but the factory itself is sync
    since it just creates the object. Redis connection is established
    lazily when async methods are called.
    
    Returns:
        ConfigRegistry instance
    """
    global _registry
    
    if _registry is None:
        _registry = ConfigRegistry()
        logger.debug("Initialized ConfigRegistry singleton")
    
    return _registry
