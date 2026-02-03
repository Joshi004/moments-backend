"""
Generate Moments API endpoint.
Unified endpoint for generating moments from existing videos or URLs.

Uses async Redis for non-blocking operations.
"""
import json
import time
import logging
from fastapi import APIRouter, HTTPException
from pathlib import Path

from app.core.redis import get_async_redis_client
from app.models.generate_moments_schemas import (
    GenerateMomentsRequest,
    GenerateMomentsResponse,
)
from app.services.pipeline.status import initialize_status
from app.services.pipeline.lock import is_locked
from app.services.url_registry import URLRegistry
from app.utils.video import get_video_by_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generate_moments", tags=["generate_moments"])


@router.post("", response_model=GenerateMomentsResponse)
async def generate_moments(request: GenerateMomentsRequest):
    """
    Generate moments from video (existing or URL).
    
    This unified endpoint supports:
    - Generating moments from existing videos (provide video_id)
    - Downloading and processing videos from URLs (provide video_url)
    - Force re-downloading cached URLs (provide video_url + force_download)
    
    Args:
        request: Generate moments request with video source and config
    
    Returns:
        Generate moments response with request_id and status
    
    Raises:
        HTTPException 400: Invalid request
        HTTPException 404: Video not found
        HTTPException 409: Pipeline already running
    """
    video_id = None
    video_url = None
    download_required = False
    is_cached = False
    
    # Case 1: video_url provided - download flow
    if request.video_url:
        video_url = request.video_url
        
        # Use URL registry to resolve video_id
        registry = URLRegistry()
        
        # Check if video file already exists locally
        tentative_video_id = registry.generate_video_id_from_url(video_url)
        existing_video = get_video_by_id(tentative_video_id)
        
        local_file_size = None
        if existing_video and existing_video.exists():
            local_file_size = existing_video.stat().st_size
        
        # Get video_id and determine if download is needed
        video_id, needs_download = registry.get_video_id_for_url(
            video_url,
            force_download=request.force_download,
            local_file_size=local_file_size
        )
        
        download_required = needs_download
        is_cached = not needs_download
        
        logger.info(
            f"URL resolution: {video_url[:50]}... -> {video_id} "
            f"(download_required={download_required}, cached={is_cached})"
        )
    
    # Case 2: video_id provided - existing video flow
    elif request.video_id:
        video_id = request.video_id
        
        # Verify video exists locally
        video = get_video_by_id(video_id)
        if not video:
            raise HTTPException(
                status_code=404,
                detail=f"Video not found: {video_id}"
            )
        
        download_required = False
        is_cached = False
        
        logger.info(f"Using existing video: {video_id}")
    
    else:
        # Should not happen due to pydantic validation, but handle anyway
        raise HTTPException(
            status_code=400,
            detail="Either video_id or video_url must be provided"
        )
    
    # Check if pipeline already running for this video
    locked, lock_info = await is_locked(video_id)
    if locked:
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline already running for video '{video_id}'"
        )
    
    # Generate request ID
    request_id = f"pipeline:{video_id}:{int(time.time() * 1000)}"
    
    # Prepare configuration dictionary
    config = request.dict(exclude={'video_id', 'video_url', 'force_download'})
    
    # Add download-specific fields to config
    if video_url:
        config['video_url'] = video_url
        config['force_download'] = request.force_download
    
    # Initialize status in Redis
    await initialize_status(video_id, request_id, config)
    
    # Add to pipeline stream
    redis = await get_async_redis_client()
    message_id = await redis.xadd("pipeline:requests", {
        "request_id": request_id,
        "video_id": video_id,
        "config": json.dumps(config),
        "requested_at": str(time.time())
    })
    
    logger.info(
        f"Started pipeline for {video_id}: {request_id}, "
        f"stream message: {message_id}, download_required: {download_required}"
    )
    
    return GenerateMomentsResponse(
        request_id=request_id,
        video_id=video_id,
        status="queued",
        message="Pipeline started successfully",
        download_required=download_required,
        source_url=video_url,
        is_cached=is_cached
    )
