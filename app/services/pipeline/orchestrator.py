"""
Pipeline orchestrator - executes all pipeline stages sequentially.
All status and lock operations are async for non-blocking Redis operations.
"""
import asyncio
import json
import logging
import time
from typing import Optional, Tuple, Dict, Any

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
)
from app.services.ai.generation_service import process_moments_generation
from app.services.ai.refinement_service import process_moment_refinement
from app.services.moments_service import load_moments
from app.services.video_clipping_service import (
    extract_clips_parallel,
)
from app.utils.video import ensure_local_video_async

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
    from app.database.session import get_session_factory
    from app.repositories import video_db_repository
    
    video_url = config.get("video_url")
    if not video_url:
        raise ValueError("video_url not found in config")
    
    # Destination path -- write to managed temp directory
    from app.services.temp_file_manager import get_temp_file_path
    dest_path = get_temp_file_path("videos", video_id, f"{video_id}.mp4")
    
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
        # Check if video already exists in database (source of truth after Phase 11)
        from app.database.session import get_session_factory
        from app.repositories import video_db_repository

        session_factory = get_session_factory()
        async with session_factory() as session:
            existing = await video_db_repository.get_by_identifier(session, video_id)
            if existing:
                return True, "Video already exists in database"

        # Video not in database -- a download URL is required
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
        if await check_transcript_exists(video_filename):
            return True, "Transcript already exists"
    
    elif stage == PipelineStage.MOMENT_GENERATION:
        # Check override flag first
        if config.get("override_existing_moments", False):
            # User explicitly wants to regenerate - don't skip
            return False, ""
        
        # Original logic - skip if moments exist
        moments = await load_moments(video_filename)
        if moments and len(moments) > 0:
            return True, f"Moments already exist ({len(moments)} moments)"
    
    elif stage == PipelineStage.CLIP_EXTRACTION:
        # Always re-extract when moments are overridden (old clips will be deleted)
        if config.get("override_existing_moments", False):
            return False, ""

        moments = await load_moments(video_filename)
        if not moments:
            return True, "No moments to extract clips from"

        # Check DB for existing clip records (Phase 7: DB is source of truth)
        original_moments = [m for m in moments if not m.get("is_refined", False)]
        if not original_moments:
            return True, "No original moments to extract clips from"

        from app.database.session import get_session_factory
        from app.repositories import moment_db_repository, clip_db_repository

        session_factory = get_session_factory()
        async with session_factory() as session:
            all_clips_exist = True
            for m in original_moments:
                moment = await moment_db_repository.get_by_identifier(session, m["id"])
                if not moment:
                    all_clips_exist = False
                    break
                exists = await clip_db_repository.exists_for_moment(session, moment.id)
                if not exists:
                    all_clips_exist = False
                    break

        if all_clips_exist:
            return True, "All clips already in database"

    elif stage == PipelineStage.CLIP_UPLOAD:
        # CLIP_UPLOAD is now a pass-through verification stage (Phase 7).
        # Upload happens during CLIP_EXTRACTION. Always skip this stage.
        return True, "Clip upload is handled during extraction (Phase 7)"
    
    elif stage == PipelineStage.MOMENT_REFINEMENT:
        moments = await load_moments(video_filename)
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

    # Look up cloud_url from database and ensure video is available locally
    from app.database.session import get_session_factory
    from app.repositories import video_db_repository

    session_factory = get_session_factory()
    async with session_factory() as session:
        video = await video_db_repository.get_by_identifier(session, video_id)
        if not video or not video.cloud_url:
            raise FileNotFoundError(f"Video not found in database: {video_filename}")
        cloud_url = video.cloud_url

    logger.info(f"Ensuring local video for audio extraction: {video_id}")
    video_path = await ensure_local_video_async(video_id, cloud_url)
    logger.info(f"Video available at: {video_path}")
    
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
            audio_path,
            None  # cloud_url not needed -- video is already local after pre-download
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
    from app.database.session import get_session_factory
    from app.repositories import transcript_db_repository
    from app.core.redis import get_async_redis_client
    
    video_filename = f"{video_id}.mp4"
    
    logger.info(f"Starting transcription for {video_id} with GCS audio URL")
    
    try:
        # Call async transcription function with timeout
        result = await asyncio.wait_for(
            process_transcription(video_id, audio_signed_url),
            timeout=600  # 10 minutes
        )
        
        logger.info(f"Transcription completed for {video_id}")
        
        # Look up the transcript record and store its ID in Redis for Phase 5
        session_factory = get_session_factory()
        async with session_factory() as session:
            transcript = await transcript_db_repository.get_by_video_identifier(session, video_id)
            if transcript:
                redis = await get_async_redis_client()
                status_key = f"pipeline:{video_id}:active"
                await redis.hset(status_key, "transcript_id", transcript.id)
                logger.info(f"Stored transcript_id={transcript.id} in Redis for video {video_id}")
        
    except asyncio.TimeoutError:
        error_msg = f"Transcription timed out after 600 seconds"
        logger.error(error_msg)
        raise TimeoutError(error_msg)
    except Exception as e:
        logger.error(f"Transcription failed for {video_id}: {str(e)}")
        raise


async def execute_moment_generation(video_id: str, config: dict) -> Optional[int]:
    """Execute moment generation stage.

    Phase 6: Moments are saved to the database by the generation service.
    Before generating, existing moments for the video are deleted (regeneration).

    Returns:
        generation_config_id if one was created, otherwise None
    """
    video_filename = f"{video_id}.mp4"

    logger.info(f"Starting moment generation for {video_id}")

    try:
        # Phase 6: Delete existing moments before regenerating
        try:
            from app.database.session import get_session_factory
            from app.repositories import moment_db_repository as moment_db_repo

            session_factory = get_session_factory()
            async with session_factory() as session:
                deleted = await moment_db_repo.delete_all_for_video_identifier(session, video_id)
                await session.commit()
                if deleted:
                    logger.info(f"Deleted {deleted} existing moments for {video_id} before regeneration")
        except Exception as del_err:
            logger.warning(f"Failed to delete existing moments before regeneration: {del_err}")

        result = await asyncio.wait_for(
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

        if isinstance(result, dict):
            validated_moments = result.get("moments", [])
            generation_config_id = result.get("generation_config_id")
        else:
            validated_moments = result
            generation_config_id = None

        if generation_config_id:
            logger.info(f"Generation config ID: {generation_config_id}")
            # Store in Redis active hash so it's available to the worker and archive function
            from app.core.redis import get_async_redis_client
            _redis = await get_async_redis_client()
            status_key = f"pipeline:{video_id}:active"
            await _redis.hset(status_key, "generation_config_id", str(generation_config_id))
            logger.info(f"Stored generation_config_id={generation_config_id} in Redis for {video_id}")

        if validated_moments:
            logger.info(f"Generated {len(validated_moments)} moments for {video_id} (saved to database by generation service)")
        else:
            logger.warning(f"No moments generated for {video_filename}")

        logger.info(f"Moment generation completed for {video_id}")
        return generation_config_id

    except asyncio.TimeoutError:
        error_msg = f"Moment generation timed out after 900 seconds"
        logger.error(error_msg)
        raise TimeoutError(error_msg)
    except Exception as e:
        logger.error(f"Moment generation failed for {video_id}: {str(e)}")
        raise


async def execute_clip_extraction(video_id: str, config: dict) -> None:
    """
    Execute clip extraction stage.

    Phase 7: Extraction, GCS upload, and database registration happen atomically
    per clip inside extract_clips_parallel(). No separate CLIP_UPLOAD stage needed.
    """
    video_filename = f"{video_id}.mp4"

    # Look up cloud_url from database (extract_clips_parallel handles local download)
    from app.database.session import get_session_factory
    from app.repositories import video_db_repository

    session_factory = get_session_factory()
    async with session_factory() as session:
        video = await video_db_repository.get_by_identifier(session, video_id)
        if not video or not video.cloud_url:
            raise FileNotFoundError(f"Video not found in database: {video_filename}")
        cloud_url = video.cloud_url

    # Use temp path as a placeholder -- extract_clips_parallel will download from cloud_url if needed
    from pathlib import Path
    video_path = Path(f"temp/videos/{video_id}/{video_id}.mp4")

    moments = await load_moments(video_filename)
    if not moments:
        raise Exception("No moments found for clip extraction")

    # Delete existing GCS clips + DB records when moments are being regenerated
    if config.get("override_existing_moments", False):
        from app.services.video_clipping_service import delete_all_clips_for_video

        logger.info(f"Cleaning up existing clips for {video_id} (moments were regenerated)")
        deleted_db = await delete_all_clips_for_video(video_id)
        logger.info(f"Cleaned up {deleted_db} clip records for {video_id}")

    # Progress callback updates Redis with current extraction counts
    def progress_callback(total: int, processed: int, failed: int):
        from app.core.redis import get_redis_client
        redis_client = get_redis_client()
        status_key = f"pipeline:{video_id}:active"
        redis_client.hset(status_key, mapping={
            "clips_total": str(total),
            "clips_processed": str(processed),
            "clips_failed": str(failed),
        })

    # Each clip: FFmpeg extract → GCS upload → DB insert → delete temp file
    success = await extract_clips_parallel(
        video_path=video_path,
        video_filename=video_filename,
        moments=moments,
        override_existing=config.get("override_existing_clips", False),
        progress_callback=progress_callback,
        cloud_url=cloud_url,
    )

    if not success:
        raise Exception("Clip extraction failed")

    original_count = sum(1 for m in moments if not m.get("is_refined", False))
    logger.info(f"Clip extraction complete: {original_count} original moments processed for {video_id}")


async def execute_clip_upload(video_id: str) -> None:
    """
    Phase 7: Clip upload is now handled atomically during CLIP_EXTRACTION.

    This stage is kept as a pass-through to avoid breaking existing pipeline
    stage definitions stored in Redis. It verifies that clips are in the
    database and logs a warning for any original moments that are missing clips.
    """
    logger.info(
        f"CLIP_UPLOAD stage reached for {video_id} - "
        "clips are uploaded during extraction (Phase 7). Verifying DB state."
    )

    video_filename = f"{video_id}.mp4"
    moments = await load_moments(video_filename)
    if not moments:
        logger.warning(f"No moments found for {video_id} during CLIP_UPLOAD verification")
        return

    original_moments = [m for m in moments if not m.get("is_refined", False)]

    from app.database.session import get_session_factory
    from app.repositories import moment_db_repository, clip_db_repository

    session_factory = get_session_factory()
    missing = []
    async with session_factory() as session:
        for m in original_moments:
            moment = await moment_db_repository.get_by_identifier(session, m["id"])
            if not moment:
                missing.append(m["id"])
                continue
            exists = await clip_db_repository.exists_for_moment(session, moment.id)
            if not exists:
                missing.append(m["id"])

    if missing:
        logger.warning(
            f"CLIP_UPLOAD verification: {len(missing)} moment(s) are missing clips in the DB "
            f"for {video_id}: {missing}"
        )
    else:
        logger.info(
            f"CLIP_UPLOAD verification: all {len(original_moments)} original moment clips "
            f"are registered in the database for {video_id}"
        )


async def execute_moment_refinement(video_id: str, config: dict) -> None:
    """
    Execute moment refinement stage using async/await.

    Phase 6: Loads moments from database. Only refines original (non-refined)
    moments. The refinement service uses create_or_update_refined to ensure
    exactly one refined copy per parent.
    """
    video_filename = f"{video_id}.mp4"
    moments = await load_moments(video_filename)

    if not moments:
        raise Exception("No moments found for refinement")

    # Only refine original (non-refined) moments.
    # The create_or_update_refined upsert in the refinement service handles
    # re-refinement automatically (updates the existing refined moment).
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
            
            # Generate fresh GCS signed URL from the clips table (Phase 7)
            video_clip_url = None
            if config.get("include_video_refinement", True):
                from app.database.session import get_session_factory
                from app.repositories import moment_db_repository, clip_db_repository
                from app.services.pipeline.upload_service import GCSUploader

                session_factory = get_session_factory()
                async with session_factory() as session:
                    moment_record = await moment_db_repository.get_by_identifier(session, moment_id)
                    if moment_record:
                        clip_record = await clip_db_repository.get_by_moment_id(session, moment_record.id)
                        if clip_record:
                            uploader = GCSUploader()
                            video_clip_url = uploader.generate_signed_url(clip_record.cloud_url)
                            logger.info(f"Generated fresh GCS signed URL for clip refinement: {moment_id}")
                        else:
                            logger.warning(f"No clip found in database for moment: {moment_id}")
                    else:
                        logger.warning(f"Moment not found in database: {moment_id}")
            
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
                return (True, moment_id, None)
            except asyncio.TimeoutError:
                error_msg = f"Refinement timed out after 600s for moment {moment_id}"
                logger.error(error_msg)
                return (False, moment_id, error_msg)
            except Exception as e:
                error_msg = f"Refinement failed for moment {moment_id}: {e}"
                logger.error(error_msg)
                return (False, moment_id, error_msg)
    
    # Run refinements with progress tracking
    tasks = [refine_one_moment(m) for m in moments_to_refine]
    results = []
    errors = []
    processed = 0
    successful = 0
    
    for coro in asyncio.as_completed(tasks):
        success, completed_moment_id, error_msg = await coro
        results.append(success)
        processed += 1
        if success:
            successful += 1
        else:
            errors.append(error_msg)
        await update_refinement_progress(video_id, len(moments_to_refine), processed, successful)
    
    logger.info(f"Refined {successful}/{len(moments_to_refine)} moments for {video_id}")

    # Store per-moment errors in Redis so the UI can surface them
    if errors:
        from app.core.redis import get_async_redis_client
        _redis = await get_async_redis_client()
        status_key = f"pipeline:{video_id}:active"
        await _redis.hset(status_key, "refinement_errors", json.dumps(errors))
        # Individual errors were already logged inside refine_one_moment(); just log the count here.
        logger.warning(f"{len(errors)} of {len(moments_to_refine)} refinement(s) failed for {video_id}. See individual errors above.")

    # If every single moment failed, raise so execute_pipeline marks the stage failed
    if successful == 0 and len(moments_to_refine) > 0:
        raise Exception(f"All {len(moments_to_refine)} refinement(s) failed for {video_id}. See individual errors above.")


async def execute_stage(stage: PipelineStage, video_id: str, config: dict) -> Any:
    """
    Execute a single pipeline stage.

    Args:
        stage: Pipeline stage to execute
        video_id: Video identifier
        config: Pipeline configuration

    Returns:
        Stage-specific return value (e.g. generation_config_id for MOMENT_GENERATION)
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
        return await execute_moment_generation(video_id, config)
    
    elif stage == PipelineStage.CLIP_EXTRACTION:
        await execute_clip_extraction(video_id, config)
    
    elif stage == PipelineStage.CLIP_UPLOAD:
        await execute_clip_upload(video_id)
    
    elif stage == PipelineStage.MOMENT_REFINEMENT:
        await execute_moment_refinement(video_id, config)

    return None


def determine_pipeline_type(skipped_stages: set) -> str:
    """
    Classify the pipeline run based on which stages were skipped.

    Args:
        skipped_stages: Set of PipelineStage values that were skipped

    Returns:
        'full', 'moments_only', or 'clips_only'
    """
    if PipelineStage.MOMENT_GENERATION in skipped_stages:
        return "clips_only"
    if PipelineStage.TRANSCRIPTION in skipped_stages:
        return "moments_only"
    if PipelineStage.VIDEO_DOWNLOAD in skipped_stages:
        return "moments_only"
    return "full"


async def execute_pipeline(
    video_id: str,
    config: dict,
    pipeline_history_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Execute complete pipeline based on model selection.

    Args:
        video_id: Video identifier
        config: Pipeline configuration dictionary
        pipeline_history_id: Optional numeric DB id of the pipeline_history record.
                             When provided, the orchestrator updates it with the
                             generation_config_id after moment generation.

    Returns:
        Result dictionary with success status, timing, counts, and error details.
    """
    generation_model = config.get("generation_model", "qwen3_vl_fp8")
    refinement_model = config.get("refinement_model", "qwen3_vl_fp8")

    logger.info(
        f"Starting pipeline for {video_id} with generation_model={generation_model}, "
        f"refinement_model={refinement_model}, pipeline_history_id={pipeline_history_id}"
    )

    pipeline_start = time.time()
    skipped_stages: set = set()
    generation_config_id: Optional[int] = None

    # Use existing helper from model_config.py
    from app.utils.model_config import model_supports_video
    refinement_needs_video = await model_supports_video(refinement_model)

    # Select stages based on refinement model's video capability
    if refinement_needs_video:
        stages = QWEN_STAGES
    else:
        stages = MINIMAX_STAGES
        skipped_stages.add(PipelineStage.CLIP_EXTRACTION)
        skipped_stages.add(PipelineStage.CLIP_UPLOAD)
        await mark_stage_skipped(video_id, PipelineStage.CLIP_EXTRACTION,
                                 "Refinement model does not support video")
        await mark_stage_skipped(video_id, PipelineStage.CLIP_UPLOAD,
                                 "Refinement model does not support video")
        config["include_video_refinement"] = False

    await update_pipeline_status(video_id, "processing")

    try:
        for stage in stages:
            # Check for cancellation between stages
            if await check_cancellation(video_id):
                await update_pipeline_status(video_id, "cancelled")
                await clear_cancellation(video_id)
                logger.info(f"Pipeline cancelled for {video_id}")
                return {
                    "success": False,
                    "cancelled": True,
                    "duration_seconds": time.time() - pipeline_start,
                    "pipeline_type": determine_pipeline_type(skipped_stages),
                    "generation_config_id": generation_config_id,
                }

            # Check skip logic
            should_skip, reason = await should_skip_stage(stage, video_id, config)
            if should_skip:
                await mark_stage_skipped(video_id, stage, reason)
                skipped_stages.add(stage)
                logger.info(f"Skipping stage {stage.value} for {video_id}: {reason}")
                continue

            # Execute stage
            await update_current_stage(video_id, stage)
            await mark_stage_started(video_id, stage)

            try:
                logger.info(f"Executing stage {stage.value} for {video_id}")
                stage_result = await execute_stage(stage, video_id, config)
                await mark_stage_completed(video_id, stage)
                logger.info(f"Completed stage {stage.value} for {video_id}")

                # After MOMENT_GENERATION, capture config_id and update DB record
                if stage == PipelineStage.MOMENT_GENERATION and stage_result is not None:
                    generation_config_id = stage_result
                    if pipeline_history_id and generation_config_id:
                        try:
                            from app.database.session import get_session_factory
                            from app.repositories import pipeline_history_db_repository
                            session_factory = get_session_factory()
                            async with session_factory() as session:
                                await pipeline_history_db_repository.set_generation_config_id(
                                    session, pipeline_history_id, generation_config_id
                                )
                                await session.commit()
                            logger.info(
                                f"Updated pipeline_history id={pipeline_history_id} "
                                f"with generation_config_id={generation_config_id}"
                            )
                        except Exception as db_err:
                            logger.error(f"Failed to update generation_config_id in DB: {db_err}")

            except Exception as e:
                logger.exception(f"Failed stage {stage.value} for {video_id}: {e}")
                await mark_stage_failed(video_id, stage, str(e))
                await update_pipeline_status(video_id, "failed")
                return {
                    "success": False,
                    "error_stage": stage.value,
                    "error_message": str(e),
                    "duration_seconds": time.time() - pipeline_start,
                    "pipeline_type": determine_pipeline_type(skipped_stages),
                    "generation_config_id": generation_config_id,
                    "total_moments_generated": 0,
                    "total_clips_created": 0,
                }

            # Refresh lock after each stage
            await refresh_lock(video_id)

    except Exception as e:
        logger.exception(f"Unexpected pipeline error for {video_id}: {e}")
        await update_pipeline_status(video_id, "failed")
        return {
            "success": False,
            "error_stage": "pipeline_error",
            "error_message": str(e),
            "duration_seconds": time.time() - pipeline_start,
            "pipeline_type": determine_pipeline_type(skipped_stages),
            "generation_config_id": generation_config_id,
            "total_moments_generated": 0,
            "total_clips_created": 0,
        }

    await update_pipeline_status(video_id, "completed")
    logger.info(f"Pipeline completed successfully for {video_id}")

    # Collect outcome counts from Redis
    total_moments = 0
    total_clips = 0
    try:
        from app.core.redis import get_async_redis_client
        _redis = await get_async_redis_client()
        status_key = f"pipeline:{video_id}:active"
        status_data = await _redis.hgetall(status_key)
        clips_processed_str = status_data.get("clips_processed", b"0")
        if isinstance(clips_processed_str, bytes):
            clips_processed_str = clips_processed_str.decode()
        total_clips = int(clips_processed_str) if clips_processed_str else 0

        # Count moments from DB (most accurate after Phase 6)
        from app.database.session import get_session_factory
        from app.repositories import moment_db_repository
        session_factory = get_session_factory()
        async with session_factory() as session:
            moments = await moment_db_repository.get_by_video_identifier(session, video_id)
            total_moments = sum(1 for m in moments if not m.is_refined)
    except Exception as count_err:
        logger.warning(f"Could not collect outcome counts: {count_err}")

    return {
        "success": True,
        "duration_seconds": time.time() - pipeline_start,
        "pipeline_type": determine_pipeline_type(skipped_stages),
        "generation_config_id": generation_config_id,
        "total_moments_generated": total_moments,
        "total_clips_created": total_clips,
    }
