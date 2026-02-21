"""
Generate Moments API endpoint.
Unified endpoint for generating moments from existing videos or URLs.

Uses async Redis for non-blocking operations.
"""
import json
import time
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis import get_async_redis_client
from app.database.dependencies import get_db
from app.models.generate_moments_schemas import (
    GenerateMomentsRequest,
    GenerateMomentsResponse,
)
from app.repositories import video_db_repository
from app.services.pipeline.status import initialize_status
from app.services.pipeline.lock import is_locked
from app.utils.url import generate_video_id_from_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/generate_moments", tags=["generate_moments"])


@router.post("", response_model=GenerateMomentsResponse)
async def generate_moments(
    request: GenerateMomentsRequest,
    db: AsyncSession = Depends(get_db),
):
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
    settings = get_settings()
    generic_names = settings.video_download_generic_names

    video_id = None
    video_url = None
    download_required = False
    is_cached = False

    # Case 1: video_url provided -- download flow
    if request.video_url:
        video_url = request.video_url

        # Check database for an existing video downloaded from this URL
        existing_video = await video_db_repository.get_by_source_url(db, video_url)

        if existing_video and not request.force_download:
            # URL already in database -- reuse the existing video
            video_id = existing_video.identifier
            download_required = False
            is_cached = True

        elif existing_video and request.force_download:
            # Force re-download requested -- create a new record with a timestamped ID
            # to avoid colliding with the existing video
            base_id = generate_video_id_from_url(video_url, generic_names)
            video_id = f"{base_id}-{int(time.time())}"
            download_required = True
            is_cached = False

        else:
            # New URL -- generate a fresh identifier
            video_id = generate_video_id_from_url(video_url, generic_names)

            # Guard against identifier collision (different URL, same filename stem)
            existing_by_id = await video_db_repository.get_by_identifier(db, video_id)
            if existing_by_id:
                video_id = f"{video_id}-{int(time.time())}"

            download_required = True
            is_cached = False

        logger.info(
            f"URL resolution: {video_url[:50]}... -> {video_id} "
            f"(download_required={download_required}, cached={is_cached})"
        )

    # Case 2: video_id provided -- existing video flow
    elif request.video_id:
        video_id = request.video_id

        # Verify video exists in the database (covers both local and GCS-only videos)
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(
                status_code=404,
                detail=f"Video not found: {video_id}",
            )

        download_required = False
        is_cached = False

        logger.info(f"Using existing video: {video_id}")

    else:
        # Should not happen due to pydantic validation, but handle anyway
        raise HTTPException(
            status_code=400,
            detail="Either video_id or video_url must be provided",
        )

    # Check if pipeline already running for this video
    locked, lock_info = await is_locked(video_id)
    if locked:
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline already running for video '{video_id}'",
        )

    # Generate request ID
    request_id = f"pipeline:{video_id}:{int(time.time() * 1000)}"

    # Prepare configuration dictionary
    config = request.dict(exclude={"video_id", "video_url", "force_download"})

    # Add download-specific fields to config
    if video_url:
        config["video_url"] = video_url
        config["force_download"] = request.force_download

    # Initialize status in Redis
    await initialize_status(video_id, request_id, config)

    # Add to pipeline stream
    redis = await get_async_redis_client()
    message_id = await redis.xadd(
        "pipeline:requests",
        {
            "request_id": request_id,
            "video_id": video_id,
            "config": json.dumps(config),
            "requested_at": str(time.time()),
        },
    )

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
        is_cached=is_cached,
    )
