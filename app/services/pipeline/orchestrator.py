"""
Pipeline orchestrator - executes all pipeline stages sequentially.
All status and lock operations are async for non-blocking Redis operations.
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
    get_stage_status,
    get_stage_error,
)
from app.models.pipeline_schemas import StageStatus
from app.services.pipeline.lock import check_cancellation, clear_cancellation, refresh_lock
from app.services.pipeline.upload_service import GCSUploader
from app.services.pipeline.concurrency import GlobalConcurrencyLimits
from app.services.ai.prompt_defaults import DEFAULT_REFINEMENT_PROMPT

# Import existing services
from app.services.audio_service import (
    get_audio_path,
    check_audio_exists,
    extract_audio_from_video,
)
from app.services.transcript_service import (
    check_transcript_exists,
    process_transcription,
)
from app.services.ai.generation_service import process_moments_generation
from app.services.ai.refinement_service import process_moment_refinement
from app.services.moments_service import load_moments, save_moments
from app.services.video_clipping_service import (
    get_clip_path,
    check_clip_exists,
    extract_clips_parallel,
)
from app.utils.video import get_video_by_filename

logger = logging.getLogger(__name__)


async def execute_video_download(video_id: str, config: dict) -> None:
    """
    Execute video download stage.
    
    Args:
        video_id: Video identifier
        config: Pipeline configuration with video_url
    
    Raises:
        Exception: If download fails
    """
    import json
    import subprocess
    from app.services.gcs_downloader import GCSDownloader
    from app.services.url_registry import URLRegistry
    from app.utils.video import get_videos_directory
    from app.database.session import get_session_factory
    from app.repositories import video_db_repository
    
    video_url = config.get("video_url")
    if not video_url:
        raise ValueError("video_url not found in config")
    
    # Destination path
    videos_dir = get_videos_directory()
    dest_path = videos_dir / f"{video_id}.mp4"
    
    # Get database session
    session_factory = get_session_factory()
    async with session_factory() as session:
        # Check database first - if video already registered with this URL, skip
        existing_video = await video_db_repository.get_by_source_url(session, video_url)
        if existing_video:
            logger.info(f"Video already in database (identifier={existing_video.identifier}), skipping download")
            return
        
        # Also check by identifier
        existing_by_id = await video_db_repository.get_by_identifier(session, video_id)
        if existing_by_id:
            logger.info(f"Video already in database with identifier {video_id}, skipping download")
            return
    
    # Check if already exists locally (double-check)
    if dest_path.exists():
        logger.info(f"Video already exists at {dest_path}, will upload to GCS and register in DB")
    else:
        logger.info(f"Starting video download: {video_url} -> {dest_path}")
        
        # Progress callback to update Redis - uses sync client
        def progress_callback(bytes_downloaded: int, total_bytes: int):
            """Update download progress in Redis."""
            try:
                from app.core.redis import get_redis_client
                redis_client = get_redis_client()
                percentage = int((bytes_downloaded / total_bytes) * 100) if total_bytes > 0 else 0
                status_key = f"pipeline:{video_id}:active"
                redis_client.hset(status_key, mapping={
                    "download_bytes": str(bytes_downloaded),
                    "download_total": str(total_bytes),
                    "download_percentage": str(percentage),
                })
            except Exception as e:
                logger.error(f"Failed to update download progress: {e}")
        
        # Download video
        downloader = GCSDownloader()
        
        try:
            success = await downloader.download(
                url=video_url,
                dest_path=dest_path,
                video_id=video_id,
                progress_callback=progress_callback
            )
            
            if not success:
                raise Exception("Download failed")
            
            # Verify file was created
            if not dest_path.exists():
                raise Exception("Download completed but file not found")
            
            file_size = dest_path.stat().st_size
            logger.info(f"Download completed: {dest_path} ({file_size / (1024**2):.2f} MB)")
            
        except Exception as e:
            # Cleanup on failure
            if dest_path.exists():
                logger.warning(f"Cleaning up partial download: {dest_path}")
                try:
                    dest_path.unlink()
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup partial download: {cleanup_error}")
            
            raise Exception(f"Video download failed: {e}")
    
    # Extract metadata via ffprobe
    logger.info(f"Extracting video metadata via ffprobe...")
    metadata = {}
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(dest_path)
            ],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            data = json.loads(result.stdout)
            
            # Extract duration and file size
            if "format" in data:
                format_data = data["format"]
                if "duration" in format_data:
                    metadata["duration_seconds"] = float(format_data["duration"])
                if "size" in format_data:
                    metadata["file_size_kb"] = int(format_data["size"]) // 1024
            
            # Extract codec info
            if "streams" in data:
                for stream in data["streams"]:
                    if stream.get("codec_type") == "video":
                        metadata["video_codec"] = stream.get("codec_name")
                        width = stream.get("width")
                        height = stream.get("height")
                        if width and height:
                            metadata["resolution"] = f"{width}x{height}"
                        r_frame_rate = stream.get("r_frame_rate")
                        if r_frame_rate:
                            try:
                                num, den = r_frame_rate.split("/")
                                metadata["frame_rate"] = float(num) / float(den)
                            except (ValueError, ZeroDivisionError):
                                pass
                    elif stream.get("codec_type") == "audio":
                        metadata["audio_codec"] = stream.get("codec_name")
            
            logger.info(f"Metadata extracted: duration={metadata.get('duration_seconds', 'N/A')}s, "
                       f"codec={metadata.get('video_codec', 'N/A')}, "
                       f"resolution={metadata.get('resolution', 'N/A')}")
    except Exception as e:
        logger.warning(f"Failed to extract metadata: {e}")
    
    # Upload to GCS
    logger.info(f"Uploading video to GCS...")
    uploader = GCSUploader()
    try:
        gcs_path, signed_url = await uploader.upload_video(dest_path, video_id)
        cloud_url = f"gs://{uploader.bucket_name}/{gcs_path}"
        logger.info(f"Video uploaded to GCS: {cloud_url}")
    except Exception as e:
        logger.error(f"Failed to upload to GCS: {e}")
        raise Exception(f"GCS upload failed: {e}")
    
    # Insert into database
    logger.info(f"Registering video in database...")
    async with session_factory() as session:
        try:
            title = video_id.replace("-", " ").replace("_", " ").title()
            video = await video_db_repository.create(
                session,
                identifier=video_id,
                cloud_url=cloud_url,
                source_url=video_url,
                title=title,
                **metadata
            )
            await session.commit()
            logger.info(f"Video registered in database (id={video.id})")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to insert into database: {e}")
            raise Exception(f"Database insert failed: {e}")
    
    # Register in URL registry (backward compatibility)
    try:
        registry = URLRegistry()
        file_size = dest_path.stat().st_size
        registry.register(
            url=video_url,
            video_id=video_id,
            file_size=file_size,
            force_downloaded=config.get("force_download", False)
        )
        logger.info(f"Video registered in URL registry")
    except Exception as e:
        logger.warning(f"Failed to register in URL registry: {e}")


# Stage sequences for each model
QWEN_STAGES = [
    PipelineStage.VIDEO_DOWNLOAD,
    PipelineStage.AUDIO_EXTRACTION,
    PipelineStage.AUDIO_UPLOAD,
    PipelineStage.TRANSCRIPTION,
    PipelineStage.MOMENT_GENERATION,
    PipelineStage.CLIP_EXTRACTION,
    PipelineStage.CLIP_UPLOAD,
    PipelineStage.MOMENT_REFINEMENT,
]

MINIMAX_STAGES = [
    PipelineStage.VIDEO_DOWNLOAD,
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
    
    if stage == PipelineStage.VIDEO_DOWNLOAD:
        # Check if video already exists in database
        from app.database.session import get_session_factory
        from app.repositories import video_db_repository
        
        session_factory = get_session_factory()
        async with session_factory() as session:
            existing = await video_db_repository.get_by_identifier(session, video_id)
            if existing:
                return True, "Video already exists in database"
        
        # Check if video already exists locally
        from app.utils.video import get_video_by_id
        video = get_video_by_id(video_id)
        if video and video.exists():
            # File exists locally but not in DB - will upload to GCS and register
            return False, ""
        
        # If no video exists, check if download URL is provided
        if not config.get("video_url"):
            raise ValueError("Video not found and no download URL provided")
        return False, ""
    
    elif stage == PipelineStage.AUDIO_EXTRACTION:
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
        # Check override flag first
        if config.get("override_existing_moments", False):
            # User explicitly wants to regenerate - don't skip
            return False, ""
        
        # Original logic - skip if moments exist
        moments = load_moments(video_filename)
        if moments and len(moments) > 0:
            return True, f"Moments already exist ({len(moments)} moments)"
    
    elif stage == PipelineStage.CLIP_EXTRACTION:
        # Always re-extract when moments are overridden (old clips will be deleted)
        if config.get("override_existing_moments", False):
            return False, ""
        
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
        # Always re-upload when moments are overridden (new clips will be generated)
        if config.get("override_existing_moments", False):
            return False, ""
        
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
        
        # Check override flag
        if config.get("override_existing_refinement", False):
            # User wants to re-refine everything - don't skip
            return False, ""
        
        # Original logic - skip if all already refined
        all_refined = all(m.get('is_refined', False) for m in moments)
        if all_refined:
            return True, "All moments already refined"
    
    return False, ""


async def execute_audio_extraction(video_id: str) -> None:
    """Execute audio extraction stage with global concurrency control."""
    video_filename = f"{video_id}.mp4"
    video_path = get_video_by_filename(video_filename)
    
    if not video_path:
        raise FileNotFoundError(f"Video file not found: {video_filename}")
    
    audio_path = get_audio_path(video_filename)
    
    # Acquire global semaphore for cross-pipeline coordination
    limits = GlobalConcurrencyLimits.get()
    async with limits.audio_extraction:
        logger.info(f"Acquired audio extraction slot for {video_id}")
        
        # Run extraction in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,  # Use default ThreadPoolExecutor
            extract_audio_from_video,
            video_path,
            audio_path
        )
    
    if not success:
        raise Exception("Audio extraction failed")
    
    logger.info(f"Extracted audio for {video_id}")


async def execute_audio_upload(video_id: str, progress_callback=None) -> str:
    """
    Execute audio upload stage to GCS with optional progress tracking.
    
    Args:
        video_id: Video identifier
        progress_callback: Optional callback(bytes_uploaded, total_bytes)
    
    Returns:
        Signed URL for the uploaded audio file
    """
    video_filename = f"{video_id}.mp4"
    audio_path = get_audio_path(video_filename)
    
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    
    uploader = GCSUploader()
    gcs_path, signed_url = await uploader.upload_audio(
        audio_path, 
        video_id,
        progress_callback=progress_callback
    )
    
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
    from app.services.transcript_service import process_transcription
    
    video_filename = f"{video_id}.mp4"
    
    logger.info(f"Starting transcription for {video_id} with GCS audio URL")
    
    try:
        # Call async transcription function with timeout
        result = await asyncio.wait_for(
            process_transcription(video_id, audio_signed_url),
            timeout=600  # 10 minutes
        )
        
        logger.info(f"Transcription completed for {video_id}")
        
    except asyncio.TimeoutError:
        error_msg = f"Transcription timed out after 600 seconds"
        logger.error(error_msg)
        raise TimeoutError(error_msg)
    except Exception as e:
        logger.error(f"Transcription failed for {video_id}: {str(e)}")
        raise


async def execute_moment_generation(video_id: str, config: dict) -> None:
    """Execute moment generation stage."""
    from app.services.ai.generation_service import process_moments_generation
    from app.services.moments_service import save_moments, generate_moment_id
    
    video_filename = f"{video_id}.mp4"
    
    logger.info(f"Starting moment generation for {video_id}")
    
    try:
        # Call async moment generation function with timeout
        validated_moments = await asyncio.wait_for(
            process_moments_generation(
                video_id=video_id,
                video_filename=video_filename,
                user_prompt=config.get("generation_prompt") or "Analyze the following video transcript and identify the most interesting, engaging, and shareable moments. These should be self-contained segments that can stand alone as short video clips.",
                min_moment_length=config.get("min_moment_length", 15),
                max_moment_length=config.get("max_moment_length", 60),
                min_moments=config.get("min_moments", 3),
                max_moments=config.get("max_moments", 10),
                model=config.get("generation_model", "qwen3_vl_fp8"),
                temperature=config.get("generation_temperature", 0.7),
            ),
            timeout=900  # 15 minutes
        )
        
        # Save moments (replaces existing) - handle empty list gracefully
        if validated_moments:
            # Ensure all required fields are present
            for moment in validated_moments:
                if 'id' not in moment or not moment['id']:
                    moment['id'] = generate_moment_id(moment['start_time'], moment['end_time'])
                if 'is_refined' not in moment:
                    moment['is_refined'] = False
                if 'parent_id' not in moment:
                    moment['parent_id'] = None
            
            logger.info(f"Saving {len(validated_moments)} moments for {video_id}")
            success = save_moments(video_filename, validated_moments)
            
            if not success:
                raise Exception("Failed to save moments to file")
        else:
            logger.warning(f"No moments to save for {video_filename}")
        
        logger.info(f"Moment generation completed for {video_id}")
        
    except asyncio.TimeoutError:
        error_msg = f"Moment generation timed out after 900 seconds"
        logger.error(error_msg)
        raise TimeoutError(error_msg)
    except Exception as e:
        logger.error(f"Moment generation failed for {video_id}: {str(e)}")
        raise


async def execute_clip_extraction(video_id: str, config: dict) -> None:
    """Execute clip extraction stage."""
    video_filename = f"{video_id}.mp4"
    video_path = get_video_by_filename(video_filename)
    
    if not video_path:
        raise FileNotFoundError(f"Video file not found: {video_filename}")
    
    moments = load_moments(video_filename)
    if not moments:
        raise Exception("No moments found for clip extraction")
    
    # Cleanup existing clips if moments were regenerated
    if config.get("override_existing_moments", False):
        from app.services.video_clipping_service import delete_all_clips_for_video
        
        logger.info(f"Cleaning up existing clips for {video_id} (moments were regenerated)")
        
        # Delete local clips
        deleted_local = await asyncio.to_thread(delete_all_clips_for_video, video_id)
        logger.info(f"Cleaned up {deleted_local} local clips for {video_id}")
        
        # Delete GCS clips
        try:
            uploader = GCSUploader()
            deleted_gcs = await uploader.delete_clips_for_video(video_id)
            logger.info(f"Cleaned up {deleted_gcs} GCS clips for {video_id}")
        except Exception as e:
            logger.warning(f"GCS clip cleanup failed for {video_id}: {e}")
    
    # Create progress callback for pipeline status
    def progress_callback(total: int, processed: int, failed: int):
        """Update pipeline status with clip extraction progress."""
        from app.core.redis import get_redis_client
        redis_client = get_redis_client()
        status_key = f"pipeline:{video_id}:active"
        redis_client.hset(status_key, mapping={
            "clips_total": str(total),
            "clips_processed": str(processed),
            "clips_failed": str(failed),
        })
    
    # Extract clips in parallel
    success = await extract_clips_parallel(
        video_path=video_path,
        video_filename=video_filename,
        moments=moments,
        override_existing=config.get("override_existing_clips", False),
        progress_callback=progress_callback
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
    
    # Create progress callback for Redis updates - uses sync client
    def clip_upload_progress_callback(clip_index: int, total_clips: int, bytes_uploaded: int, total_bytes: int):
        """
        Update clip upload progress in Redis.
        
        Note: bytes_uploaded and total_bytes are cumulative across all clips.
        """
        try:
            from app.core.redis import get_redis_client
            redis_client = get_redis_client()
            # Calculate overall percentage from cumulative bytes
            overall_percentage = int((bytes_uploaded / total_bytes) * 100) if total_bytes > 0 else 0
            status_key = f"pipeline:{video_id}:active"
            redis_client.hset(status_key, mapping={
                "clip_upload_current": str(clip_index),
                "clip_upload_total_clips": str(total_clips),
                "clip_upload_bytes": str(bytes_uploaded),
                "clip_upload_total_bytes": str(total_bytes),
                "clip_upload_percentage": str(overall_percentage),
            })
        except Exception as e:
            logger.error(f"Failed to update clip upload progress: {e}")
    
    uploader = GCSUploader()
    updated_moments = await uploader.upload_all_clips(
        video_id, 
        moments,
        progress_callback=clip_upload_progress_callback
    )
    
    # Save updated moments with GCS paths and signed URLs
    save_moments(video_filename, updated_moments)
    
    uploaded_count = sum(1 for m in updated_moments if m.get('gcs_clip_path'))
    logger.info(f"Uploaded {uploaded_count} clips for {video_id} to GCS")


async def execute_moment_refinement(video_id: str, config: dict) -> None:
    """
    Execute moment refinement stage using async/await.
    
    This function uses the new async process_moment_refinement() which:
    - Directly awaits AI model calls (no polling)
    - Uses asyncio.wait_for() for timeout handling
    - Has native exception propagation
    - No longer requires JobRepository
    """
    video_filename = f"{video_id}.mp4"
    moments = load_moments(video_filename)
    
    if not moments:
        raise Exception("No moments found for refinement")
    
    # Check override flag to determine which moments to refine
    if config.get("override_existing_refinement", False):
        # Re-refine ALL moments (including already refined ones)
        moments_to_refine = moments
    else:
        # Only refine moments that haven't been refined yet
        moments_to_refine = [m for m in moments if not m.get('is_refined', False)]
    
    if not moments_to_refine:
        logger.info(f"All moments already refined for {video_id}")
        return
    
    await update_refinement_progress(video_id, len(moments_to_refine), 0)
    
    # Use global semaphore for cross-pipeline coordination
    limits = GlobalConcurrencyLimits.get()
    
    async def refine_one_moment(moment):
        """Refine a single moment with global semaphore-controlled concurrency."""
        async with limits.refinement:
            moment_id = moment['id']
            
            # Generate GCS signed URL for video if needed
            video_clip_url = None
            if config.get("include_video_refinement", True):
                from app.services.video_clipping_service import get_clip_gcs_signed_url_async
                video_clip_url = await get_clip_gcs_signed_url_async(moment_id, video_filename)
                if video_clip_url:
                    logger.info(f"Generated GCS signed URL for clip refinement: {moment_id}")
                else:
                    logger.warning(f"Failed to generate GCS signed URL for clip: {moment_id}")
            
            try:
                # Use asyncio.wait_for() for timeout handling - no more polling!
                success = await asyncio.wait_for(
                    process_moment_refinement(
                        video_id=video_id,
                        moment_id=moment_id,
                        video_filename=video_filename,
                        user_prompt=DEFAULT_REFINEMENT_PROMPT,
                        model=config.get("refinement_model", "qwen3_vl_fp8"),
                        temperature=config.get("refinement_temperature", 0.7),
                        include_video=config.get("include_video_refinement", True),
                        video_clip_url=video_clip_url,
                    ),
                    timeout=600  # 10 minutes per moment
                )
                return success
            except asyncio.TimeoutError:
                logger.error(f"Refinement timed out for moment {moment_id}")
                return False
            except Exception as e:
                logger.error(f"Refinement failed for moment {moment_id}: {e}")
                return False
    
    # Run refinements with progress tracking
    tasks = [refine_one_moment(m) for m in moments_to_refine]
    results = []
    processed = 0
    successful = 0
    
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        processed += 1
        if result:
            successful += 1
        await update_refinement_progress(video_id, len(moments_to_refine), processed, successful)
    
    logger.info(f"Refined {successful}/{len(moments_to_refine)} moments for {video_id}")


async def execute_stage(stage: PipelineStage, video_id: str, config: dict) -> None:
    """
    Execute a single pipeline stage.
    
    Args:
        stage: Pipeline stage to execute
        video_id: Video identifier
        config: Pipeline configuration
    """
    if stage == PipelineStage.VIDEO_DOWNLOAD:
        await execute_video_download(video_id, config)
    
    elif stage == PipelineStage.AUDIO_EXTRACTION:
        await execute_audio_extraction(video_id)
    
    elif stage == PipelineStage.AUDIO_UPLOAD:
        # Create progress callback for Redis updates - uses sync client
        def upload_progress_callback(bytes_uploaded: int, total_bytes: int):
            """Update upload progress in Redis."""
            try:
                from app.core.redis import get_redis_client
                redis_client = get_redis_client()
                percentage = int((bytes_uploaded / total_bytes) * 100) if total_bytes > 0 else 0
                status_key = f"pipeline:{video_id}:active"
                redis_client.hset(status_key, mapping={
                    "upload_bytes": str(bytes_uploaded),
                    "upload_total": str(total_bytes),
                    "upload_percentage": str(percentage),
                })
            except Exception as e:
                logger.error(f"Failed to update upload progress: {e}")
        
        audio_signed_url = await execute_audio_upload(video_id, progress_callback=upload_progress_callback)
        # Store signed URL in Redis for next stage (uses async client)
        from app.core.redis import get_async_redis_client
        redis = await get_async_redis_client()
        status_key = f"pipeline:{video_id}:active"
        await redis.hset(status_key, "audio_signed_url", audio_signed_url)
        logger.info(f"Stored audio signed URL in pipeline state for {video_id}")
    
    elif stage == PipelineStage.TRANSCRIPTION:
        # Retrieve signed URL from Redis
        from app.core.redis import get_async_redis_client
        redis = await get_async_redis_client()
        status_key = f"pipeline:{video_id}:active"  # Use :active key (same as status.py)
        audio_signed_url = await redis.hget(status_key, "audio_signed_url")
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
    generation_model = config.get("generation_model", "qwen3_vl_fp8")
    refinement_model = config.get("refinement_model", "qwen3_vl_fp8")
    
    logger.info(f"Starting pipeline for {video_id} with generation_model={generation_model}, refinement_model={refinement_model}")
    
    # Use existing helper from model_config.py
    from app.utils.model_config import model_supports_video
    refinement_needs_video = await model_supports_video(refinement_model)
    
    # Select stages based on refinement model's video capability
    if refinement_needs_video:
        stages = QWEN_STAGES
    else:
        stages = MINIMAX_STAGES
        # Mark skipped stages for non-video models
        await mark_stage_skipped(video_id, PipelineStage.CLIP_EXTRACTION, 
                          "Refinement model does not support video")
        await mark_stage_skipped(video_id, PipelineStage.CLIP_UPLOAD, 
                          "Refinement model does not support video")
        # Force disable video refinement
        config["include_video_refinement"] = False
    
    await update_pipeline_status(video_id, "processing")
    
    for stage in stages:
        # Check for cancellation between stages
        if await check_cancellation(video_id):
            await update_pipeline_status(video_id, "cancelled")
            await clear_cancellation(video_id)
            logger.info(f"Pipeline cancelled for {video_id}")
            return {"success": False, "cancelled": True}
        
        # Check skip logic
        should_skip, reason = await should_skip_stage(stage, video_id, config)
        if should_skip:
            await mark_stage_skipped(video_id, stage, reason)
            logger.info(f"Skipping stage {stage.value} for {video_id}: {reason}")
            continue
        
        # Execute stage
        await update_current_stage(video_id, stage)
        await mark_stage_started(video_id, stage)
        
        try:
            logger.info(f"Executing stage {stage.value} for {video_id}")
            await execute_stage(stage, video_id, config)
            await mark_stage_completed(video_id, stage)
            logger.info(f"Completed stage {stage.value} for {video_id}")
        except Exception as e:
            logger.exception(f"Failed stage {stage.value} for {video_id}: {e}")
            await mark_stage_failed(video_id, stage, str(e))
            await update_pipeline_status(video_id, "failed")
            return {
                "success": False,
                "error": str(e),
                "failed_stage": stage.value
            }
        
        # Refresh lock after each stage
        await refresh_lock(video_id)
    
    await update_pipeline_status(video_id, "completed")
    logger.info(f"Pipeline completed successfully for {video_id}")
    return {"success": True}
