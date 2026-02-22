"""
Moment-related API endpoints.
Handles moment CRUD, generation, and refinement operations.
"""
from fastapi import APIRouter, HTTPException, Depends
import time
import asyncio
import logging

logger = logging.getLogger(__name__)

from sqlalchemy.ext.asyncio import AsyncSession
from app.database.dependencies import get_db
from app.repositories import video_db_repository
from app.models.schemas import (
    MomentResponse,
    GenerateMomentsRequest,
    RefineMomentRequest
)
from app.services.moments_service import load_moments, add_moment, get_moment_by_id
from app.services.audio_service import check_audio_exists
from app.services.transcript_service import check_transcript_exists
from app.services.ai.generation_service import (
    process_moments_generation
)
from app.services.ai.refinement_service import (
    process_moment_refinement
)
from app.services.ai.prompt_defaults import DEFAULT_REFINEMENT_PROMPT
from app.services import job_tracker
from app.services.video_clipping_service import check_clip_exists, get_clip_gcs_signed_url_async
from app.utils.model_config import model_supports_video
from app.core.logging import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()


@router.get("/videos/{video_id}/moments", response_model=list[MomentResponse])
async def get_moments(video_id: str, db: AsyncSession = Depends(get_db)):
    """Get all moments for a video."""
    start_time = time.time()
    operation = "get_moments"

    log_event(
        level="DEBUG",
        logger="app.api.endpoints.moments",
        function="get_moments",
        operation=operation,
        event="operation_start",
        message=f"Getting moments for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )

    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            log_event(
                level="WARNING",
                logger="app.api.endpoints.moments",
                function="get_moments",
                operation=operation,
                event="validation_error",
                message="Video not found",
                context={"video_id": video_id}
            )
            raise HTTPException(status_code=404, detail="Video not found")

        moments = await load_moments(f"{video_id}.mp4")

        duration = time.time() - start_time
        log_event(
            level="DEBUG",
            logger="app.api.endpoints.moments",
            function="get_moments",
            operation=operation,
            event="operation_complete",
            message="Successfully retrieved moments",
            context={
                "video_id": video_id,
                "moment_count": len(moments),
                "duration_seconds": duration
            }
        )

        return [MomentResponse(**moment) for moment in moments]

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.moments",
            function="get_moments",
            operation=operation,
            error=e,
            message="Error getting moments",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.post("/videos/{video_id}/moments", response_model=MomentResponse, status_code=201)
async def create_moment(video_id: str, moment: MomentResponse, db: AsyncSession = Depends(get_db)):
    """Add a new moment to a video."""
    start_time = time.time()
    operation = "create_moment"

    log_operation_start(
        logger="app.api.endpoints.moments",
        function="create_moment",
        operation=operation,
        message=f"Creating moment for {video_id}",
        context={
            "video_id": video_id,
            "moment": {
                "start_time": moment.start_time,
                "end_time": moment.end_time,
                "title": moment.title
            },
            "request_id": get_request_id()
        }
    )

    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # Use duration from database; fall back to 0 if not available
        video_duration = video.duration_seconds if video.duration_seconds else 0.0
        if video_duration <= 0:
            raise HTTPException(status_code=500, detail="Could not determine video duration")

        moment_dict = {
            "start_time": moment.start_time,
            "end_time": moment.end_time,
            "title": moment.title
        }

        # Add moment with validation (async -- saves to database)
        success, error_message, created_moment = await add_moment(f"{video_id}.mp4", moment_dict, video_duration)

        if not success:
            raise HTTPException(status_code=400, detail=error_message)

        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.moments",
            function="create_moment",
            operation=operation,
            message="Successfully created moment",
            context={
                "video_id": video_id,
                "moment_id": created_moment.get("id"),
                "duration_seconds": duration
            }
        )

        return MomentResponse(**created_moment)

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.moments",
            function="create_moment",
            operation=operation,
            error=e,
            message="Error creating moment",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.post("/videos/{video_id}/generate-moments")
async def generate_moments(video_id: str, request: GenerateMomentsRequest, db: AsyncSession = Depends(get_db)):
    """Start moment generation process for a video."""
    start_time = time.time()
    operation = "generate_moments"

    log_operation_start(
        logger="app.api.endpoints.moments",
        function="generate_moments",
        operation=operation,
        message=f"Starting moment generation for {video_id}",
        context={
            "video_id": video_id,
            "request_params": {
                "model": request.model,
                "temperature": request.temperature,
                "min_moment_length": request.min_moment_length,
                "max_moment_length": request.max_moment_length,
                "min_moments": request.min_moments,
                "max_moments": request.max_moments,
                "has_user_prompt": request.user_prompt is not None
            },
            "request_id": get_request_id()
        }
    )

    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        video_filename = f"{video_id}.mp4"
        audio_filename = f"{video_id}.wav"

        if not check_audio_exists(video_filename):
            raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")

        if not await check_transcript_exists(audio_filename):
            raise HTTPException(status_code=400, detail="Transcript not found. Please generate transcript first.")

        # Default prompt
        default_prompt = """Analyze the following video transcript and identify the most important, engaging, or valuable moments. Each moment should represent a distinct topic, insight, or highlight that would be meaningful to viewers.

Generate moments that:
- Capture key insights, turning points, or memorable segments
- Have clear, descriptive titles (5-15 words)
- Represent complete thoughts or concepts
- Are non-overlapping and well-spaced throughout the video"""

        user_prompt = request.user_prompt if request.user_prompt else default_prompt

        # Phase 6: generation service saves moments to DB directly
        try:
            # Delete existing moments before regeneration
            try:
                from app.database.session import get_session_factory
                from app.repositories import moment_db_repository as moment_db_repo
                sf = get_session_factory()
                async with sf() as session:
                    deleted = await moment_db_repo.delete_all_for_video_identifier(session, video_id)
                    await session.commit()
                    if deleted:
                        logger.info(f"Deleted {deleted} existing moments for {video_id}")
            except Exception:
                pass

            result = await asyncio.wait_for(
                process_moments_generation(
                    video_id=video_id,
                    video_filename=video_filename,
                    user_prompt=user_prompt,
                    min_moment_length=request.min_moment_length,
                    max_moment_length=request.max_moment_length,
                    min_moments=request.min_moments,
                    max_moments=request.max_moments,
                    model=request.model,
                    temperature=request.temperature
                ),
                timeout=900  # 15 minutes
            )

            if isinstance(result, dict):
                validated_moments = result.get("moments", [])
            else:
                validated_moments = result if result else []

            if validated_moments:
                log_event(
                    level="INFO",
                    logger="app.api.endpoints.moments",
                    function="generate_moments",
                    operation=operation,
                    event="moments_saved",
                    message=f"Generated {len(validated_moments)} moments (saved to DB by generation service)",
                    context={"video_id": video_id, "moment_count": len(validated_moments)}
                )

            duration = time.time() - start_time
            log_operation_complete(
                logger="app.api.endpoints.moments",
                function="generate_moments",
                operation=operation,
                message="Moment generation completed successfully",
                context={"video_id": video_id, "model": request.model, "duration_seconds": duration}
            )

            return {"message": "Moment generation completed", "video_id": video_id, "moment_count": len(validated_moments)}

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            log_operation_error(
                logger="app.api.endpoints.moments",
                function="generate_moments",
                operation=operation,
                error=Exception("Timeout"),
                message="Moment generation timed out",
                context={"video_id": video_id, "duration_seconds": duration}
            )
            raise HTTPException(status_code=504, detail="Moment generation timed out after 900 seconds")

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.moments",
            function="generate_moments",
            operation=operation,
            error=e,
            message="Error starting moment generation",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/generation-status")
async def get_generation_status_endpoint(video_id: str, db: AsyncSession = Depends(get_db)):
    """Get moment generation status for a video."""
    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        job = await job_tracker.get_job("moment_generation", video_id)

        if job is None:
            return {"status": "not_started", "started_at": None}

        return {
            "status": job.get("status"),
            "started_at": job.get("started_at")
        }

    except HTTPException:
        raise


@router.post("/videos/{video_id}/moments/{moment_id}/refine")
async def refine_moment(video_id: str, moment_id: str, request: RefineMomentRequest, db: AsyncSession = Depends(get_db)):
    """Start moment refinement process."""
    start_time = time.time()
    operation = "refine_moment"

    log_operation_start(
        logger="app.api.endpoints.moments",
        function="refine_moment",
        operation=operation,
        message=f"Starting moment refinement for {video_id}/{moment_id}",
        context={
            "video_id": video_id,
            "moment_id": moment_id,
            "request_params": {
                "model": request.model,
                "temperature": request.temperature,
                "has_user_prompt": request.user_prompt is not None,
                "include_video": request.include_video
            },
            "request_id": get_request_id()
        }
    )

    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        video_filename = f"{video_id}.mp4"
        audio_filename = f"{video_id}.wav"

        # Check if moment exists (async -- queries database)
        moment = await get_moment_by_id(video_filename, moment_id)
        if moment is None:
            raise HTTPException(status_code=404, detail="Moment not found")

        if not check_audio_exists(video_filename):
            raise HTTPException(status_code=400, detail="Audio file not found. Please process audio first.")

        if not await check_transcript_exists(audio_filename):
            raise HTTPException(status_code=400, detail="Transcript not found. Please generate transcript first.")

        # Always use the centralized refinement prompt (user_prompt is ignored)
        user_prompt = DEFAULT_REFINEMENT_PROMPT

        include_video = request.include_video
        video_clip_url = None

        if include_video:
            if not model_supports_video(request.model):
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{request.model}' does not support video. Use 'qwen3_vl_fp8' for video refinement."
                )

            if not await check_clip_exists(moment_id):
                raise HTTPException(
                    status_code=400,
                    detail="Video clip not available. Extract clips first or disable video refinement."
                )

            video_clip_url = await get_clip_gcs_signed_url_async(moment_id, video_filename)

        try:
            success = await asyncio.wait_for(
                process_moment_refinement(
                    video_id=video_id,
                    moment_id=moment_id,
                    video_filename=video_filename,
                    user_prompt=user_prompt,
                    model=request.model,
                    temperature=request.temperature,
                    include_video=include_video,
                    video_clip_url=video_clip_url
                ),
                timeout=300  # 5 minutes
            )

            if not success:
                raise HTTPException(status_code=500, detail="Moment refinement failed")

            duration = time.time() - start_time
            log_operation_complete(
                logger="app.api.endpoints.moments",
                function="refine_moment",
                operation=operation,
                message="Moment refinement completed successfully",
                context={
                    "video_id": video_id,
                    "moment_id": moment_id,
                    "model": request.model,
                    "duration_seconds": duration
                }
            )

            return {
                "message": "Moment refinement completed",
                "video_id": video_id,
                "moment_id": moment_id,
                "include_video": include_video
            }

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            log_operation_error(
                logger="app.api.endpoints.moments",
                function="refine_moment",
                operation=operation,
                error=Exception("Timeout"),
                message="Moment refinement timed out",
                context={"video_id": video_id, "moment_id": moment_id, "duration_seconds": duration}
            )
            raise HTTPException(status_code=504, detail="Moment refinement timed out after 300 seconds")

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.moments",
            function="refine_moment",
            operation=operation,
            error=e,
            message="Error starting moment refinement",
            context={"video_id": video_id, "moment_id": moment_id, "duration_seconds": duration}
        )
        raise


@router.get("/videos/{video_id}/refinement-status/{moment_id}")
async def get_refinement_status_endpoint(video_id: str, moment_id: str, db: AsyncSession = Depends(get_db)):
    """Get moment refinement status."""
    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        job = await job_tracker.get_job("moment_refinement", video_id, sub_id=moment_id)

        if job is None:
            return {"status": "not_started", "started_at": None}

        return {
            "status": job.get("status"),
            "started_at": job.get("started_at")
        }

    except HTTPException:
        raise
