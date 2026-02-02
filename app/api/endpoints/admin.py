"""
Admin API endpoints for model configuration management.
"""
import logging
from fastapi import APIRouter, HTTPException, status
from typing import Dict

from app.models.admin_schemas import (
    ModelConfigCreate,
    ModelConfigUpdate,
    ModelConfigResponse,
    ModelConfigListResponse,
    SeedRequest,
    SeedResponse,
    DeleteResponse,
)
from app.services.config_registry import (
    get_config_registry,
    ModelConfigNotFoundError,
)
from app.utils.model_config import DEFAULT_MODELS, seed_default_configs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/models", response_model=ModelConfigListResponse)
async def list_model_configs():
    """
    List all model configurations.
    
    Returns:
        List of all configured models with their settings
    """
    try:
        registry = get_config_registry()
        configs = registry.list_configs()
        
        return ModelConfigListResponse(
            models=configs,
            count=len(configs)
        )
    except Exception as e:
        logger.error(f"Error listing model configs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list model configs: {str(e)}"
        )


@router.get("/models/{model_key}", response_model=ModelConfigResponse)
async def get_model_config(model_key: str):
    """
    Get configuration for a specific model.
    
    Args:
        model_key: Model identifier (e.g., "minimax", "qwen3_vl_fp8")
        
    Returns:
        Model configuration
    """
    try:
        registry = get_config_registry()
        config = registry.get_config(model_key)
        config["model_key"] = model_key
        
        return ModelConfigResponse(**config)
    except ModelConfigNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "model_not_found",
                "message": str(e),
                "model_key": model_key,
                "available_models": e.available_keys,
            }
        )
    except Exception as e:
        logger.error(f"Error getting model config for {model_key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get model config: {str(e)}"
        )


@router.post("/models/{model_key}", response_model=ModelConfigResponse)
async def create_or_update_model_config(
    model_key: str,
    config: ModelConfigCreate
):
    """
    Create or fully replace a model configuration.
    
    Args:
        model_key: Model identifier
        config: Complete model configuration
        
    Returns:
        Created/updated configuration
    """
    try:
        registry = get_config_registry()
        
        # Convert to dict and set
        config_dict = config.dict(exclude_none=False)
        registry.set_config(model_key, config_dict)
        
        # Get back with timestamp
        updated_config = registry.get_config(model_key)
        updated_config["model_key"] = model_key
        
        logger.info(f"Created/updated model config: {model_key}")
        return ModelConfigResponse(**updated_config)
    except Exception as e:
        logger.error(f"Error creating/updating model config for {model_key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create/update model config: {str(e)}"
        )


@router.patch("/models/{model_key}", response_model=ModelConfigResponse)
async def partial_update_model_config(
    model_key: str,
    updates: ModelConfigUpdate
):
    """
    Partially update a model configuration.
    
    Args:
        model_key: Model identifier
        updates: Fields to update
        
    Returns:
        Updated configuration
    """
    try:
        registry = get_config_registry()
        
        # Filter out None values for partial update
        update_dict = {
            k: v for k, v in updates.dict().items() 
            if v is not None
        }
        
        if not update_dict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields provided for update"
            )
        
        # Update config
        updated_config = registry.update_config(model_key, update_dict)
        updated_config["model_key"] = model_key
        
        logger.info(f"Partially updated model config: {model_key}, fields: {list(update_dict.keys())}")
        return ModelConfigResponse(**updated_config)
    except ModelConfigNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "model_not_found",
                "message": str(e),
                "model_key": model_key,
                "available_models": e.available_keys,
            }
        )
    except Exception as e:
        logger.error(f"Error updating model config for {model_key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update model config: {str(e)}"
        )


@router.delete("/models/{model_key}", response_model=DeleteResponse)
async def delete_model_config(model_key: str):
    """
    Delete a model configuration.
    
    Args:
        model_key: Model identifier
        
    Returns:
        Delete operation result
    """
    try:
        registry = get_config_registry()
        deleted = registry.delete_config(model_key)
        
        if deleted:
            logger.info(f"Deleted model config: {model_key}")
            return DeleteResponse(
                success=True,
                model_key=model_key,
                message=f"Model config '{model_key}' deleted successfully"
            )
        else:
            return DeleteResponse(
                success=False,
                model_key=model_key,
                message=f"Model config '{model_key}' not found"
            )
    except Exception as e:
        logger.error(f"Error deleting model config for {model_key}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete model config: {str(e)}"
        )


@router.post("/models/seed", response_model=SeedResponse)
async def seed_model_configs(request: SeedRequest = SeedRequest()):
    """
    Seed Redis with default model configurations.
    
    Args:
        request: Seed request with optional force flag
        
    Returns:
        Number of configs seeded
    """
    try:
        count = seed_default_configs(force=request.force)
        
        message = (
            f"Seeded {count} model configs"
            if not request.force
            else f"Force-seeded {count} model configs (overwrote existing)"
        )
        
        logger.info(message)
        return SeedResponse(
            seeded_count=count,
            message=message
        )
    except Exception as e:
        logger.error(f"Error seeding model configs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to seed model configs: {str(e)}"
        )


@router.get("/models/defaults/all", response_model=Dict)
async def get_default_configs():
    """
    Get default model configurations (for reference).
    
    Returns:
        Dictionary of default configurations
    """
    return DEFAULT_MODELS
