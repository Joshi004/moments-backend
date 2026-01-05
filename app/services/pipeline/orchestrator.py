"""
Pipeline orchestrator - executes all pipeline stages sequentially.
"""
import asyncio
import logging
from typing import Tuple, Dict, Any
from pathlib import Path

from app.models.pipeline_schemas import PipelineStage
from app.services.pipeline.status import (
    mark_stage_started,
    mark_stage_completed,
    mark_stage_skipped,
    mark_stage_failed,
    update_pipeline_status,
    update_current_stage,
    update_refinement_progress,
)
from app.services.pipeline.lock import check_cancellation, clear_cancellation, refresh_lock
from app.services.pipeline.upload_service import GCSUploader

# Import existing services
from app.services.audio_service import (
    get_audio_path,
    check_audio_exists,
    extract_audio_from_video,
)
from app.services.transcript_service import (
    check_transcript_exists,
    process_transcription_async,
)
from app.services.ai.generation_service import process_moments_generation_async
from app.services.ai.refinement_service import process_moment_refinement_async
from app.services.moments_service import load_moments, save_moments
from app.services.video_clipping_service import (
    get_clip_path,
    check_clip_exists,
    extract_clips_parallel,
)
from app.utils.video import get_video_by_filename

logger = logging.getLogger(__name__)

# Stage sequences for each model
QWEN_STAGES = [
    PipelineStage.AUDIO_EXTRACTION,
    PipelineStage.AUDIO_UPLOAD,
    PipelineStage.TRANSCRIPTION,
    PipelineStage.MOMENT_GENERATION,
    PipelineStage.CLIP_EXTRACTION,
    PipelineStage.CLIP_UPLOAD,
    PipelineStage.MOMENT_REFINEMENT,
]

MINIMAX_STAGES = [
    PipelineStage.AUDIO_EXTRACTION,
    PipelineStage.AUDIO_UPLOAD,
    PipelineStage.TRANSCRIPTION,
    PipelineStage.MOMENT_GENERATION,
    PipelineStage.MOMENT_REFINEMENT,
]


async def should_skip_stage(stage: PipelineStage, video_id: str, config: dict) -> Tuple[bool, str]:
    """
    Determine if a stage should be skipped.
    
    Args:
        stage: Pipeline stage
        video_id: Video identifier
        config: Pipeline configuration
    
    Returns:
        Tuple of (should_skip, reason)
    """
    video_filename = f"{video_id}.mp4"
    
    if stage == PipelineStage.AUDIO_EXTRACTION:
        if check_audio_exists(video_filename):
            return True, "Audio file already exists"
    
    elif stage == PipelineStage.AUDIO_UPLOAD:
        # Always upload audio to ensure remote has latest
        # Could add SSH check here if needed
        return False, ""
    
    elif stage == PipelineStage.TRANSCRIPTION:
        if check_transcript_exists(video_filename):
            return True, "Transcript already exists"
    
    elif stage == PipelineStage.MOMENT_GENERATION:
        moments = load_moments(video_filename)
        if moments and len(moments) > 0:
            return True, f"Moments already exist ({len(moments)} moments)"
    
    elif stage == PipelineStage.CLIP_EXTRACTION:
        moments = load_moments(video_filename)
        if not moments:
            return True, "No moments to extract clips from"
        all_clips_exist = all(
            check_clip_exists(m['id'], video_filename) 
            for m in moments
        )
        if all_clips_exist:
            return True, "All clips already extracted"
    
    elif stage == PipelineStage.CLIP_UPLOAD:
        moments = load_moments(video_filename)
        if not moments:
            return True, "No moments to upload clips for"
        all_uploaded = all(m.get('remote_clip_path') for m in moments)
        if all_uploaded:
            return True, "All clips already uploaded"
    
    elif stage == PipelineStage.MOMENT_REFINEMENT:
        moments = load_moments(video_filename)
        if not moments:
            return True, "No moments to refine"
        all_refined = all(m.get('is_refined', False) for m in moments)
        if all_refined:
            return True, "All moments already refined"
    
    return False, ""


async def execute_audio_extraction(video_id: str) -> None:
    """Execute audio extraction stage."""
    video_filename = f"{video_id}.mp4"
    video_path = get_video_by_filename(video_filename)
    
    if not video_path:
        raise FileNotFoundError(f"Video file not found: {video_filename}")
    
    audio_path = get_audio_path(video_filename)
    
    # Run extraction synchronously
    success = extract_audio_from_video(video_path, audio_path)
    
    if not success:
        raise Exception("Audio extraction failed")
    
    logger.info(f"Extracted audio for {video_id}")


async def execute_audio_upload(video_id: str) -> str:
    """
    Execute audio upload stage to GCS.
    
    Returns:
        Signed URL for the uploaded audio file
    """
    video_filename = f"{video_id}.mp4"
    audio_path = get_audio_path(video_filename)
    
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    
    uploader = GCSUploader()
    gcs_path, signed_url = await uploader.upload_audio(audio_path, video_id)
    
    logger.info(f"Uploaded audio for {video_id} to gs://{uploader.bucket_name}/{gcs_path}")
    logger.info(f"Generated signed URL (expires in 1 hour)")
    
    return signed_url


async def execute_transcription(video_id: str, audio_signed_url: str) -> None:
    """
    Execute transcription stage using GCS signed URL.
    
    Args:
        video_id: Video identifier
        audio_signed_url: GCS signed URL for the audio file
    """
    video_filename = f"{video_id}.mp4"
    
    # The transcription service runs async in a thread
    # We need to wait for it to complete
    from app.repositories.job_repository import JobRepository, JobType, JobStatus
    job_repo = JobRepository()
    
    logger.info(f"Starting transcription for {video_id} with GCS audio URL")
    
    # Start transcription with signed URL
    process_transcription_async(video_id, audio_signed_url)
    
    # Wait for completion
    max_wait = 600  # 10 minutes
    wait_interval = 2  # 2 seconds
    elapsed = 0
    
    while elapsed < max_wait:
        await asyncio.sleep(wait_interval)
        elapsed += wait_interval
        
        job = job_repo.get(JobType.TRANSCRIPTION, video_id)
        if job:
            if job["status"] == JobStatus.COMPLETED.value:
                logger.info(f"Transcription completed for {video_id}")
                return
            elif job["status"] == JobStatus.FAILED.value:
                error = job.get("error", "Unknown error")
                raise Exception(f"Transcription failed: {error}")
    
    raise TimeoutError(f"Transcription timed out after {max_wait} seconds")


async def execute_moment_generation(video_id: str, config: dict) -> None:
    """Execute moment generation stage."""
    video_filename = f"{video_id}.mp4"
    
    from app.repositories.job_repository import JobRepository, JobType, JobStatus
    job_repo = JobRepository()
    
    # Start moment generation
    process_moments_generation_async(
        video_id=video_id,
        video_filename=video_filename,
        user_prompt=config.get("generation_prompt") or "Analyze the following video transcript and identify the most interesting, engaging, and shareable moments. These should be self-contained segments that can stand alone as short video clips.",
        min_moment_length=config.get("min_moment_length", 60),
        max_moment_length=config.get("max_moment_length", 120),
        min_moments=config.get("min_moments", 3),
        max_moments=config.get("max_moments", 10),
        model=config.get("model", "qwen3_vl_fp8"),
        temperature=config.get("temperature", 0.7),
    )
    
    # Wait for completion
    max_wait = 900  # 15 minutes
    wait_interval = 3  # 3 seconds
    elapsed = 0
    
    while elapsed < max_wait:
        await asyncio.sleep(wait_interval)
        elapsed += wait_interval
        
        job = job_repo.get(JobType.MOMENT_GENERATION, video_id)
        if job:
            if job["status"] == JobStatus.COMPLETED.value:
                logger.info(f"Moment generation completed for {video_id}")
                return
            elif job["status"] == JobStatus.FAILED.value:
                error = job.get("error", "Unknown error")
                raise Exception(f"Moment generation failed: {error}")
    
    raise TimeoutError(f"Moment generation timed out after {max_wait} seconds")


async def execute_clip_extraction(video_id: str, config: dict) -> None:
    """Execute clip extraction stage."""
    video_filename = f"{video_id}.mp4"
    video_path = get_video_by_filename(video_filename)
    
    if not video_path:
        raise FileNotFoundError(f"Video file not found: {video_filename}")
    
    moments = load_moments(video_filename)
    if not moments:
        raise Exception("No moments found for clip extraction")
    
    # Extract clips in parallel
    success = await extract_clips_parallel(
        video_path=video_path,
        video_filename=video_filename,
        moments=moments,
        override_existing=config.get("override_existing_clips", False),
    )
    
    if not success:
        raise Exception("Clip extraction failed")
    
    logger.info(f"Extracted {len(moments)} clips for {video_id}")


async def execute_clip_upload(video_id: str) -> None:
    """Execute clip upload stage to GCS."""
    video_filename = f"{video_id}.mp4"
    moments = load_moments(video_filename)
    
    if not moments:
        raise Exception("No moments found for clip upload")
    
    uploader = GCSUploader()
    updated_moments = await uploader.upload_all_clips(video_id, moments)
    
    # Save updated moments with GCS paths and signed URLs
    save_moments(video_filename, updated_moments)
    
    uploaded_count = sum(1 for m in updated_moments if m.get('gcs_clip_path'))
    logger.info(f"Uploaded {uploaded_count} clips for {video_id} to GCS")


async def execute_moment_refinement(video_id: str, config: dict) -> None:
    """Execute moment refinement stage."""
    video_filename = f"{video_id}.mp4"
    moments = load_moments(video_filename)
    
    if not moments:
        raise Exception("No moments found for refinement")
    
    moments_to_refine = [m for m in moments if not m.get('is_refined', False)]
    
    if not moments_to_refine:
        logger.info(f"All moments already refined for {video_id}")
        return
    
    update_refinement_progress(video_id, len(moments_to_refine), 0)
    
    from app.repositories.job_repository import JobRepository, JobType, JobStatus
    job_repo = JobRepository()
    
    # Refine moments with configured parallelism
    parallel_workers = config.get("refinement_parallel_workers", 2)
    semaphore = asyncio.Semaphore(parallel_workers)
    
    async def refine_one_moment(moment):
        async with semaphore:
            moment_id = moment['id']
            
            # Generate GCS signed URL for video if needed
            video_clip_url = None
            if config.get("include_video_refinement", True):
                from app.services.video_clipping_service import get_clip_gcs_signed_url
                video_clip_url = get_clip_gcs_signed_url(moment_id, video_filename)
                if video_clip_url:
                    logger.info(f"Generated GCS signed URL for clip refinement: {moment_id}")
                else:
                    logger.warning(f"Failed to generate GCS signed URL for clip: {moment_id}")
            
            # Start refinement
            process_moment_refinement_async(
                video_id=video_id,
                moment_id=moment_id,
                video_filename=video_filename,
                user_prompt=config.get("refinement_prompt"),
                model=config.get("model", "qwen3_vl_fp8"),
                temperature=config.get("temperature", 0.7),
                include_video=config.get("include_video_refinement", True),
                video_clip_url=video_clip_url,
            )
            
            # Wait for completion
            max_wait = 600  # 10 minutes per moment
            wait_interval = 2
            elapsed = 0
            
            while elapsed < max_wait:
                await asyncio.sleep(wait_interval)
                elapsed += wait_interval
                
                job = job_repo.get(JobType.MOMENT_REFINEMENT, video_id, moment_id)
                if job:
                    if job["status"] == JobStatus.COMPLETED.value:
                        return True
                    elif job["status"] == JobStatus.FAILED.value:
                        logger.error(f"Refinement failed for moment {moment_id}")
                        return False
            
            logger.error(f"Refinement timed out for moment {moment_id}")
            return False
    
    # Run refinements with progress tracking
    tasks = [refine_one_moment(m) for m in moments_to_refine]
    results = []
    processed = 0
    
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        processed += 1
        update_refinement_progress(video_id, len(moments_to_refine), processed)
    
    successful = sum(1 for r in results if r)
    logger.info(f"Refined {successful}/{len(moments_to_refine)} moments for {video_id}")


async def execute_stage(stage: PipelineStage, video_id: str, config: dict) -> None:
    """
    Execute a single pipeline stage.
    
    Args:
        stage: Pipeline stage to execute
        video_id: Video identifier
        config: Pipeline configuration
    """
    if stage == PipelineStage.AUDIO_EXTRACTION:
        await execute_audio_extraction(video_id)
    
    elif stage == PipelineStage.AUDIO_UPLOAD:
        audio_signed_url = await execute_audio_upload(video_id)
        # Store signed URL in Redis for next stage
        from app.core.redis import get_redis_client
        redis = get_redis_client()
        status_key = f"pipeline:{video_id}:status"
        redis.hset(status_key, "audio_signed_url", audio_signed_url)
        logger.info(f"Stored audio signed URL in pipeline state for {video_id}")
    
    elif stage == PipelineStage.TRANSCRIPTION:
        # Retrieve signed URL from Redis
        from app.core.redis import get_redis_client
        redis = get_redis_client()
        status_key = f"pipeline:{video_id}:status"
        audio_signed_url = redis.hget(status_key, "audio_signed_url")
        if audio_signed_url:
            audio_signed_url = audio_signed_url.decode('utf-8') if isinstance(audio_signed_url, bytes) else audio_signed_url
        await execute_transcription(video_id, audio_signed_url)
    
    elif stage == PipelineStage.MOMENT_GENERATION:
        await execute_moment_generation(video_id, config)
    
    elif stage == PipelineStage.CLIP_EXTRACTION:
        await execute_clip_extraction(video_id, config)
    
    elif stage == PipelineStage.CLIP_UPLOAD:
        await execute_clip_upload(video_id)
    
    elif stage == PipelineStage.MOMENT_REFINEMENT:
        await execute_moment_refinement(video_id, config)


async def execute_pipeline(video_id: str, config: dict) -> Dict[str, Any]:
    """
    Execute complete pipeline based on model selection.
    
    Args:
        video_id: Video identifier
        config: Pipeline configuration dictionary
    
    Returns:
        Result dictionary with success status and details
    """
    model = config.get("model", "qwen3_vl_fp8")
    model_supports_video = (model == "qwen3_vl_fp8")
    
    logger.info(f"Starting pipeline for {video_id} with model {model}")
    
    # Select stages based on model
    if model_supports_video:
        stages = QWEN_STAGES
    else:
        stages = MINIMAX_STAGES
        # Mark skipped stages for MiniMax
        mark_stage_skipped(video_id, PipelineStage.CLIP_EXTRACTION, 
                          "Model does not support video")
        mark_stage_skipped(video_id, PipelineStage.CLIP_UPLOAD, 
                          "Model does not support video")
    
    update_pipeline_status(video_id, "processing")
    
    for stage in stages:
        # Check for cancellation between stages
        if check_cancellation(video_id):
            update_pipeline_status(video_id, "cancelled")
            clear_cancellation(video_id)
            logger.info(f"Pipeline cancelled for {video_id}")
            return {"success": False, "cancelled": True}
        
        # Check skip logic
        should_skip, reason = await should_skip_stage(stage, video_id, config)
        if should_skip:
            mark_stage_skipped(video_id, stage, reason)
            logger.info(f"Skipping stage {stage.value} for {video_id}: {reason}")
            continue
        
        # Execute stage
        update_current_stage(video_id, stage)
        mark_stage_started(video_id, stage)
        
        try:
            logger.info(f"Executing stage {stage.value} for {video_id}")
            await execute_stage(stage, video_id, config)
            mark_stage_completed(video_id, stage)
            logger.info(f"Completed stage {stage.value} for {video_id}")
        except Exception as e:
            logger.exception(f"Failed stage {stage.value} for {video_id}: {e}")
            mark_stage_failed(video_id, stage, str(e))
            update_pipeline_status(video_id, "failed")
            return {
                "success": False,
                "error": str(e),
                "failed_stage": stage.value
            }
        
        # Refresh lock after each stage
        refresh_lock(video_id)
    
    update_pipeline_status(video_id, "completed")
    logger.info(f"Pipeline completed successfully for {video_id}")
    return {"success": True}



