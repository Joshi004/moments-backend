import asyncio
import json
import subprocess
import platform
from pathlib import Path
from typing import Optional, Dict, List
import logging
import cv2
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error
)
from app.utils.timestamp import calculate_padded_boundaries

logger = logging.getLogger(__name__)


def get_temp_clips_directory(video_identifier: str = "") -> Path:
    """
    Get the temp directory for clip extraction.

    Args:
        video_identifier: Video identifier stem (e.g. "motivation"). When
                          provided, returns the per-video subdirectory.
                          When empty, returns the clips root directory.

    Returns:
        Path to temp/clips/{video_identifier}/ (created if absent)
    """
    from app.services.temp_file_manager import get_temp_dir, _get_temp_base
    if video_identifier:
        return get_temp_dir("clips", video_identifier)
    # Root-level access -- return base clips dir without creating an identifier subdir
    base = _get_temp_base() / "clips"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_clip_path(moment_id: str, video_filename: str) -> Path:
    """
    Get the temporary path for a clip file during extraction.

    The clip is extracted here, uploaded to GCS, then deleted.

    Args:
        moment_id: Unique identifier for the moment
        video_filename: Original video filename (e.g., "motivation.mp4")

    Returns:
        Path object inside temp/clips/{video_stem}/
    """
    from app.services.temp_file_manager import get_temp_file_path
    video_stem = Path(video_filename).stem
    clip_filename = f"{video_stem}_{moment_id}_clip.mp4"
    return get_temp_file_path("clips", video_stem, clip_filename)


async def check_clip_exists(moment_identifier: str) -> bool:
    """
    Check if a clip exists for a given moment by querying the database.

    Args:
        moment_identifier: The moment's string identifier

    Returns:
        True if a clip record exists in the database, False otherwise
    """
    from app.database.session import get_session_factory
    from app.repositories import moment_db_repository, clip_db_repository

    session_factory = get_session_factory()
    async with session_factory() as session:
        moment = await moment_db_repository.get_by_identifier(session, moment_identifier)
        if not moment:
            return False
        return await clip_db_repository.exists_for_moment(session, moment.id)


async def delete_all_clips_for_video(video_id: str) -> int:
    """
    Delete all clips for a video from GCS and the database.

    Args:
        video_id: Video identifier (stem, e.g., "motivation")

    Returns:
        Number of database records deleted
    """
    from app.database.session import get_session_factory
    from app.repositories import video_db_repository, clip_db_repository
    from app.services.pipeline.upload_service import GCSUploader

    try:
        uploader = GCSUploader()
        deleted_gcs = await uploader.delete_clips_for_video(video_id)
        logger.info(f"Deleted {deleted_gcs} GCS clips for {video_id}")
    except Exception as e:
        logger.warning(f"GCS clip deletion failed for {video_id}: {e}")

    session_factory = get_session_factory()
    async with session_factory() as session:
        video = await video_db_repository.get_by_identifier(session, video_id)
        if not video:
            logger.warning(f"Video '{video_id}' not found in database, skipping DB clip deletion")
            return 0
        deleted_db = await clip_db_repository.delete_all_for_video(session, video.id)
        await session.commit()
        logger.info(f"Deleted {deleted_db} clip records from database for {video_id}")
        return deleted_db


async def get_clip_signed_url(moment_identifier: str) -> Optional[str]:
    """
    Get a fresh GCS signed URL for a moment's clip by querying the database.

    Args:
        moment_identifier: The moment's string identifier

    Returns:
        A freshly generated GCS signed URL, or None if no clip exists
    """
    from app.database.session import get_session_factory
    from app.repositories import clip_db_repository
    from app.services.pipeline.upload_service import GCSUploader

    session_factory = get_session_factory()
    async with session_factory() as session:
        clip = await clip_db_repository.get_by_moment_identifier(session, moment_identifier)
        if not clip:
            return None

    uploader = GCSUploader()
    return uploader.generate_signed_url(clip.cloud_url)


async def get_clip_gcs_signed_url_async(moment_id: str, video_filename: str) -> Optional[str]:
    """
    Get a fresh GCS signed URL for a clip by querying the database.

    Args:
        moment_id: The moment's string identifier
        video_filename: Unused (kept for backward compatibility)

    Returns:
        A freshly generated GCS signed URL, or None if no clip exists
    """
    return await get_clip_signed_url(moment_id)


def _extract_clip_metadata(clip_path: Path) -> Dict:
    """
    Extract codec and resolution metadata from a clip file using ffprobe.

    Args:
        clip_path: Path to the clip file

    Returns:
        Dict with keys: file_size_kb, video_codec, audio_codec, resolution
    """
    metadata = {
        "file_size_kb": None,
        "video_codec": None,
        "audio_codec": None,
        "resolution": None,
    }

    try:
        metadata["file_size_kb"] = clip_path.stat().st_size // 1024
    except Exception as e:
        logger.warning(f"Could not read file size for {clip_path}: {e}")

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(clip_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            data = json.loads(result.stdout)
            for stream in data.get("streams", []):
                codec_type = stream.get("codec_type")
                if codec_type == "video" and metadata["video_codec"] is None:
                    metadata["video_codec"] = stream.get("codec_name")
                    width = stream.get("width")
                    height = stream.get("height")
                    if width and height:
                        metadata["resolution"] = f"{width}x{height}"
                elif codec_type == "audio" and metadata["audio_codec"] is None:
                    metadata["audio_codec"] = stream.get("codec_name")
        else:
            logger.warning(f"ffprobe returned non-zero exit for {clip_path}: {result.stderr}")

    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timed out for {clip_path}")
    except Exception as e:
        logger.warning(f"ffprobe error for {clip_path}: {e}")

    return metadata


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using OpenCV."""
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return 0.0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0.0
        cap.release()
        return duration
    except Exception as e:
        logger.error(f"Error getting video duration: {e}")
        return 0.0


def get_clip_duration(moment_id: str, video_filename: str) -> Optional[float]:
    """
    Get the duration of a clip file in seconds.

    Checks the temp clip path for duration.

    Args:
        moment_id: Unique identifier for the moment
        video_filename: Original video filename (e.g., "ProjectUpdateVideo.mp4")

    Returns:
        Duration in seconds if clip file is accessible, None otherwise
    """
    clip_path = get_clip_path(moment_id, video_filename)

    if not clip_path.exists() or not clip_path.is_file():
        logger.debug(f"Clip file not found: {clip_path}")
        return None

    try:
        cap = cv2.VideoCapture(str(clip_path))
        if not cap.isOpened():
            logger.error(f"Could not open clip file: {clip_path}")
            return None

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0.0
        cap.release()

        logger.debug(f"Clip duration for {moment_id}: {duration:.2f}s (fps={fps}, frames={frame_count})")
        return duration
    except Exception as e:
        logger.error(f"Error getting clip duration for {moment_id}: {e}")
        return None


# ---------------------------------------------------------------------------
# FFmpeg extraction (pure sync -- offloaded to a thread via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _run_ffmpeg_extract(
    video_path: Path,
    moment_id: str,
    start_time: float,
    end_time: float,
    video_filename: str,
) -> Optional[Path]:
    """
    Run FFmpeg to extract a clip segment. Pure sync, no DB or GCS.

    This is the only function offloaded to a thread during clip extraction.
    It builds the FFmpeg command, runs the subprocess, and returns the path
    to the output file on success.

    Args:
        video_path: Path to the source video file
        moment_id: Moment identifier (for output filename)
        start_time: Clip start timestamp in seconds (padded)
        end_time: Clip end timestamp in seconds (padded)
        video_filename: Original video filename (for output naming)

    Returns:
        Path to the extracted clip file, or None on failure
    """
    if not video_path.exists():
        logger.error(f"Video file not found: {video_path}")
        return None

    if start_time < 0 or end_time <= start_time:
        logger.error(f"Invalid timestamps: start={start_time}, end={end_time}")
        return None

    output_path = get_clip_path(moment_id, video_filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    duration = end_time - start_time

    from app.utils.model_config import get_encoding_config
    encoding_config = get_encoding_config()

    is_macos = platform.system() == "Darwin"

    if is_macos:
        cmd = [
            "ffmpeg",
            "-ss", str(start_time),
            "-i", str(video_path),
            "-t", str(duration),
            "-c:v", encoding_config["macos_encoder"],
            "-q:v", str(encoding_config["macos_quality"]),
            "-c:a", encoding_config["audio_codec"],
            "-b:a", encoding_config["audio_bitrate"],
            "-avoid_negative_ts", "make_zero",
            "-y",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-ss", str(start_time),
            "-i", str(video_path),
            "-t", str(duration),
            "-c:v", encoding_config["linux_encoder"],
            "-preset", encoding_config["linux_preset"],
            "-c:a", encoding_config["audio_codec"],
            "-b:a", encoding_config["audio_bitrate"],
            "-avoid_negative_ts", "make_zero",
            "-y",
            str(output_path),
        ]

    logger.info(
        f"FFmpeg extracting clip for moment {moment_id}: "
        f"{start_time:.1f}s-{end_time:.1f}s ({duration:.1f}s) "
        f"[{'macOS HW' if is_macos else 'Linux SW'}]"
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        logger.error(f"FFmpeg timed out for moment {moment_id}")
        return None

    if result.returncode != 0:
        logger.error(f"FFmpeg failed for moment {moment_id} (rc={result.returncode}): {result.stderr[:500]}")
        return None

    if not output_path.exists():
        logger.error(f"FFmpeg did not create output file for moment {moment_id}: {output_path}")
        return None

    logger.info(f"FFmpeg complete for moment {moment_id}: {output_path.stat().st_size} bytes")
    return output_path


# ---------------------------------------------------------------------------
# Single-clip lifecycle (async coroutine -- runs on the main event loop)
# ---------------------------------------------------------------------------

async def _process_single_clip(
    video_path: Path,
    moment: Dict,
    video_id: str,
    video_filename: str,
    word_timestamps: Optional[List],
    padding: float,
    margin: float,
    video_duration: float,
    override_existing: bool,
    idx: int,
    total: int,
) -> Dict:
    """
    Full lifecycle for one clip: check DB, FFmpeg extract, upload GCS, insert DB, cleanup.

    Each step uses the correct execution model:
    - DB queries: native await (async, main event loop)
    - FFmpeg subprocess: asyncio.to_thread (offloaded to thread pool)
    - GCS upload: native await (async, main event loop)
    - ffprobe metadata: asyncio.to_thread (fast sync subprocess)

    Args:
        video_path: Path to the source video
        moment: Moment dict with 'id', 'start_time', 'end_time'
        video_id: Video identifier stem
        video_filename: Original video filename
        word_timestamps: Transcript word timestamps for boundary alignment (or None)
        padding: Seconds of padding around moment boundaries
        margin: Margin for word-aligned boundaries
        video_duration: Total video duration in seconds
        override_existing: If False, skip moments that already have clips in the DB
        idx: 0-based index of this clip in the batch
        total: Total number of clips in the batch

    Returns:
        Result dict with 'moment_id' and 'status' ("success", "skipped", or "failed")
    """
    moment_id = moment.get("id")
    original_start = moment.get("start_time")
    original_end = moment.get("end_time")

    if not all([moment_id, original_start is not None, original_end is not None]):
        logger.warning(f"Skipping moment with missing data: {moment}")
        return {"moment_id": moment_id, "status": "failed", "reason": "missing_data"}

    # --- Step 1: Check if clip already exists in DB (async, main loop) ---
    if not override_existing:
        if await check_clip_exists(moment_id):
            logger.info(f"[{idx + 1}/{total}] Clip already exists for moment {moment_id} -- skipping")
            return {"moment_id": moment_id, "status": "skipped"}

    # --- Step 2: Calculate padded clip boundaries ---
    if word_timestamps:
        try:
            clip_start, clip_end = calculate_padded_boundaries(
                word_timestamps=word_timestamps,
                moment_start=original_start,
                moment_end=original_end,
                padding=padding,
                margin=margin,
            )
        except Exception as e:
            logger.warning(
                f"Word-aligned boundary error for moment {moment_id}: {e}. "
                "Falling back to simple padding."
            )
            clip_start = max(0, original_start - padding)
            clip_end = min(video_duration, original_end + padding)
    else:
        clip_start = max(0, original_start - padding)
        clip_end = min(video_duration, original_end + padding)

    clip_start = max(0, clip_start)
    clip_end = min(video_duration, clip_end)
    padding_left = original_start - clip_start
    padding_right = clip_end - original_end

    logger.info(f"[{idx + 1}/{total}] Extracting clip for moment {moment_id}: {clip_start:.1f}s-{clip_end:.1f}s")

    try:
        # --- Step 3: FFmpeg extraction (offloaded to thread -- CPU/IO bound) ---
        output_path = await asyncio.to_thread(
            _run_ffmpeg_extract,
            video_path, moment_id, clip_start, clip_end, video_filename,
        )

        if not output_path:
            return {"moment_id": moment_id, "status": "failed", "reason": "ffmpeg_error"}

        # --- Step 4: Extract metadata via ffprobe (offloaded -- fast sync) ---
        metadata = await asyncio.to_thread(_extract_clip_metadata, output_path)

        # --- Step 5: Upload to GCS (async, main loop) ---
        from app.services.pipeline.upload_service import GCSUploader
        uploader = GCSUploader()
        gcs_path, _ = await uploader.upload_clip(output_path, video_id, moment_id)

        # --- Step 6: Insert DB record (async, main loop) ---
        from app.database.session import get_session_factory
        from app.repositories import video_db_repository, moment_db_repository, clip_db_repository

        session_factory = get_session_factory()
        async with session_factory() as session:
            video = await video_db_repository.get_by_identifier(session, video_id)
            moment_record = await moment_db_repository.get_by_identifier(session, moment_id)

            if not video or not moment_record:
                logger.error(f"Video '{video_id}' or moment '{moment_id}' not found in DB after extraction")
                return {"moment_id": moment_id, "status": "failed", "reason": "db_lookup_error"}

            clip = await clip_db_repository.create(
                session,
                moment_id=moment_record.id,
                video_id=video.id,
                cloud_url=gcs_path,
                start_time=clip_start,
                end_time=clip_end,
                padding_left=padding_left,
                padding_right=padding_right,
                file_size_kb=metadata.get("file_size_kb"),
                format="mp4",
                video_codec=metadata.get("video_codec"),
                audio_codec=metadata.get("audio_codec"),
                resolution=metadata.get("resolution"),
            )
            await session.commit()

        # --- Step 7: Delete temp file ---
        try:
            output_path.unlink()
            logger.debug(f"Deleted temp clip: {output_path}")
        except Exception as e:
            logger.warning(f"Could not delete temp clip {output_path}: {e}")

        logger.info(
            f"[{idx + 1}/{total}] Clip for moment {moment_id} registered "
            f"(clip.id={clip.id}, {metadata.get('resolution')}, {metadata.get('file_size_kb')}KB)"
        )
        return {
            "moment_id": moment_id,
            "status": "success",
            "cloud_url": gcs_path,
            "clip_start": clip_start,
            "clip_end": clip_end,
        }

    except Exception as e:
        logger.error(f"[{idx + 1}/{total}] Failed to process clip for moment {moment_id}: {type(e).__name__}: {e}")
        return {"moment_id": moment_id, "status": "failed", "reason": str(e)}


# ---------------------------------------------------------------------------
# Batch extraction entry point (async -- called by the pipeline orchestrator)
# ---------------------------------------------------------------------------

async def extract_clips_parallel(
    video_path: Path,
    video_filename: str,
    moments: List[Dict],
    override_existing: bool = False,
    progress_callback: Optional[callable] = None,
    cloud_url: Optional[str] = None,
) -> bool:
    """
    Extract clips for all original moments in a video using async concurrency.

    Uses asyncio.Semaphore to limit the number of concurrent clip extractions
    to the configured parallel worker count. Each clip's full lifecycle
    (DB check, FFmpeg, GCS upload, DB insert) runs as an async coroutine on
    the main event loop, with only FFmpeg offloaded to a thread.

    Args:
        video_path: Path to the source video file
        video_filename: Original video filename
        moments: List of moment dicts
        override_existing: Whether to re-extract clips that already exist in DB
        progress_callback: Optional callback(total, processed, failed) for progress
        cloud_url: Optional GCS URL for downloading video if local is missing

    Returns:
        True if successful (at least one clip created or all skipped), False otherwise
    """
    video_id = Path(video_filename).stem

    operation = log_operation_start(
        logger="app.services.video_clipping_service",
        function="extract_clips_parallel",
        operation="batch_clip_extraction",
        context={
            "video_id": video_id,
            "video_filename": video_filename,
            "num_moments": len(moments),
            "override_existing": override_existing,
        },
    )

    try:
        # --- Ensure local video exists ---
        if not video_path.exists() and cloud_url:
            logger.info(f"Local video not found for {video_id}, downloading from cloud")
            from app.utils.video import ensure_local_video_async
            video_path = await ensure_local_video_async(video_id, cloud_url)
            logger.info(f"Downloaded video to {video_path}")

        # --- Load clipping config ---
        from app.utils.model_config import get_clipping_config, get_parallel_workers
        clipping_config = get_clipping_config()
        padding = clipping_config["padding"]
        margin = clipping_config["margin"]
        max_workers = get_parallel_workers()

        # --- Load transcript (async -- runs on main loop, no asyncio.run needed) ---
        from app.services.transcript_service import load_transcript
        audio_filename = video_filename.rsplit(".", 1)[0] + ".wav"
        transcript_data = await load_transcript(audio_filename)

        word_timestamps = None
        if transcript_data and "word_timestamps" in transcript_data:
            word_timestamps = transcript_data["word_timestamps"]
            logger.info(f"Loaded transcript with {len(word_timestamps)} words for precise clipping")
        else:
            logger.warning(f"Transcript not available for {audio_filename}, using simple padding")

        # --- Get video duration ---
        video_duration = await asyncio.to_thread(get_video_duration, video_path)
        if video_duration <= 0:
            raise ValueError(f"Could not determine video duration for {video_filename}")

        # --- Filter to original moments ---
        original_moments = [m for m in moments if not m.get("is_refined", False)]

        log_event(
            level="INFO",
            logger="app.services.video_clipping_service",
            function="extract_clips_parallel",
            operation=operation,
            event="operation_start",
            message="Starting async clip extraction",
            context={
                "video_duration": video_duration,
                "total_moments": len(moments),
                "original_moments": len(original_moments),
                "max_workers": max_workers,
                "padding": padding,
                "has_transcript": word_timestamps is not None,
            },
        )

        if not original_moments:
            logger.info(f"No original moments to extract clips for {video_id}")
            return True

        # --- Semaphore-controlled concurrent extraction ---
        semaphore = asyncio.Semaphore(max_workers)
        total = len(original_moments)

        results = {
            "total": total,
            "successful": 0,
            "skipped": 0,
            "failed": 0,
            "clips": [],
        }

        async def _limited_process(moment, idx):
            async with semaphore:
                result = await _process_single_clip(
                    video_path=video_path,
                    moment=moment,
                    video_id=video_id,
                    video_filename=video_filename,
                    word_timestamps=word_timestamps,
                    padding=padding,
                    margin=margin,
                    video_duration=video_duration,
                    override_existing=override_existing,
                    idx=idx,
                    total=total,
                )

                # Update counters and call progress callback
                if result["status"] == "success":
                    results["successful"] += 1
                elif result["status"] == "skipped":
                    results["skipped"] += 1
                else:
                    results["failed"] += 1
                results["clips"].append(result)

                if progress_callback:
                    processed = results["successful"] + results["skipped"] + results["failed"]
                    await asyncio.to_thread(progress_callback, total, processed, results["failed"])

                return result

        tasks = [_limited_process(moment, idx) for idx, moment in enumerate(original_moments)]
        await asyncio.gather(*tasks)

        log_operation_complete(
            logger="app.services.video_clipping_service",
            function="extract_clips_parallel",
            operation=operation,
            message="Batch clip extraction completed",
            context={
                "total_moments": results["total"],
                "successful_clips": results["successful"],
                "skipped_clips": results["skipped"],
                "failed_clips": results["failed"],
            },
        )

        return results["failed"] == 0 or results["successful"] > 0

    except Exception as e:
        log_operation_error(
            logger="app.services.video_clipping_service",
            function="extract_clips_parallel",
            operation=operation,
            error=e,
            message="Batch clip extraction failed",
        )
        return False
