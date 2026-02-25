"""
Clip extraction and availability API endpoints.
Handles video clip extraction for moments and clip serving via GCS signed URLs.
"""
import logging

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
import time

logger = logging.getLogger(__name__)

from app.models.schemas import VideoAvailabilityResponse
from app.database.dependencies import get_db
from app.services.moments_service import get_moment_by_id
from app.services.video_clipping_service import (
    get_clip_duration,
)
from app.repositories import clip_db_repository, moment_db_repository, video_db_repository
from app.services.transcript_service import load_transcript
from app.utils.model_config import model_supports_video, get_duration_tolerance, get_clipping_config
from app.utils.timestamp import calculate_padded_boundaries, extract_words_in_range, normalize_word_timestamps
from app.core.logging import (
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

router = APIRouter()


@router.get("/videos/{video_id}/moments/{moment_id}/video-availability", response_model=VideoAvailabilityResponse)
async def check_video_availability(
    video_id: str,
    moment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Check if a video clip is available for a moment and validate alignment with transcript.
    Clip existence is determined by querying the database.
    """
    start_time = time.time()
    operation = "check_video_availability"

    log_operation_start(
        logger="app.api.endpoints.clips",
        function="check_video_availability",
        operation=operation,
        message=f"Checking video availability for {video_id}/{moment_id}",
        context={"video_id": video_id, "moment_id": moment_id, "request_id": get_request_id()},
    )

    try:
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        moment = await get_moment_by_id(f"{video_id}.mp4", moment_id)
        if moment is None:
            raise HTTPException(status_code=404, detail="Moment not found")

        supports_video = await model_supports_video("qwen3_vl_fp8")
        result = VideoAvailabilityResponse(
            available=False,
            clip_url=None,
            clip_duration=None,
            transcript_duration=None,
            duration_match=False,
            warning=None,
            model_supports_video=supports_video,
        )

        # Check clip existence via database
        clip = await clip_db_repository.get_by_moment_identifier(db, moment_id)

        if not clip:
            result.warning = "Video clip not available. Extract clips first to enable video refinement."
            return result

        # Generate fresh signed URL from clip's stored cloud_url
        from app.services.pipeline.upload_service import GCSUploader
        uploader = GCSUploader()
        clip_url = uploader.generate_signed_url(clip.cloud_url)
        result.clip_url = clip_url

        # Try to get clip duration (checks temp directory)
        clip_duration = get_clip_duration(moment_id, f"{video_id}.mp4")
        if clip_duration is None or clip_duration <= 0:
            # Mark as available even if we can't determine duration
            result.available = True
            result.warning = "Could not determine video clip duration."
            return result

        result.clip_duration = clip_duration

        # Load transcript for validation
        audio_filename = f"{video_id}.wav"
        transcript_data = await load_transcript(audio_filename)

        if transcript_data is None or "word_timestamps" not in transcript_data:
            result.warning = "Transcript not available. Cannot validate alignment."
            result.available = True
            return result

        clipping_config = get_clipping_config()
        padding = clipping_config["padding"]
        margin = clipping_config.get("margin", 2.0)

        word_timestamps = transcript_data["word_timestamps"]

        try:
            padded_start, padded_end = calculate_padded_boundaries(
                word_timestamps,
                moment["start_time"],
                moment["end_time"],
                padding,
                margin,
            )

            words_in_range = extract_words_in_range(word_timestamps, padded_start, padded_end)

            if words_in_range:
                first_word_start = words_in_range[0]["start"]
                last_word_end = words_in_range[-1]["end"]
                transcript_duration = last_word_end - first_word_start

                result.transcript_duration = transcript_duration

                duration_diff = abs(clip_duration - transcript_duration)
                tolerance = get_duration_tolerance()

                result.duration_match = duration_diff <= tolerance
                result.available = True

                if not result.duration_match:
                    result.warning = (
                        f"Duration mismatch: clip={clip_duration:.2f}s, "
                        f"transcript={transcript_duration:.2f}s (diff={duration_diff:.2f}s)"
                    )
            else:
                result.warning = "No words found in transcript range"
                result.available = True

        except Exception as e:
            result.warning = f"Could not validate alignment: {str(e)}"
            result.available = True

        duration = time.time() - start_time
        log_operation_complete(
            logger="app.api.endpoints.clips",
            function="check_video_availability",
            operation=operation,
            message="Video availability check complete",
            context={
                "video_id": video_id,
                "moment_id": moment_id,
                "available": result.available,
                "duration_match": result.duration_match,
                "duration_seconds": duration,
            },
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.clips",
            function="check_video_availability",
            operation=operation,
            error=e,
            message="Error checking video availability",
            context={"video_id": video_id, "moment_id": moment_id, "duration_seconds": duration},
        )
        raise


@router.get("/clips/{moment_identifier}/stream")
async def stream_clip(
    moment_identifier: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Stream a clip by redirecting to a fresh GCS signed URL (HTTP 302).

    The browser or video player follows the redirect and loads the clip
    directly from GCS, which natively supports range requests for seeking.
    """
    clip = await clip_db_repository.get_by_moment_identifier(db, moment_identifier)
    if not clip:
        raise HTTPException(status_code=404, detail=f"Clip not found for moment '{moment_identifier}'")

    from app.services.pipeline.upload_service import GCSUploader
    uploader = GCSUploader()
    signed_url = uploader.generate_signed_url(clip.cloud_url)

    return RedirectResponse(url=signed_url, status_code=302)


@router.get("/clips/{moment_identifier}/url")
async def get_clip_url(
    moment_identifier: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get a fresh GCS signed URL for a clip as JSON (for programmatic access).

    Returns:
        JSON with url, expires_in_seconds, moment_identifier, video identifier
    """
    clip = await clip_db_repository.get_by_moment_identifier(db, moment_identifier)
    if not clip:
        raise HTTPException(status_code=404, detail=f"Clip not found for moment '{moment_identifier}'")

    from app.services.pipeline.upload_service import GCSUploader

    uploader = GCSUploader()
    signed_url = uploader.generate_signed_url(clip.cloud_url)

    # Expiry comes from the uploader's configured value (set from settings)
    expires_in_seconds = int(uploader.expiry_hours * 3600)

    # Retrieve video identifier from the moment record
    moment = await moment_db_repository.get_by_identifier(db, moment_identifier)
    video_identifier = None
    if moment:
        from app.repositories import video_db_repository
        video = await video_db_repository.get_by_id(db, moment.video_id)
        if video:
            video_identifier = video.identifier

    return {
        "url": signed_url,
        "expires_in_seconds": expires_in_seconds,
        "moment_identifier": moment_identifier,
        "video_identifier": video_identifier,
    }


@router.get("/clips/{moment_identifier}/transcript")
async def get_clip_transcript(
    moment_identifier: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return the normalized word-level transcript for a clip's exact boundaries.

    Timestamps in the returned words list are relative to the clip start (i.e.
    the first word starts at or near 0.0), matching the clip's video playback
    timeline.
    """
    clip = await clip_db_repository.get_by_moment_identifier(db, moment_identifier)
    if not clip:
        raise HTTPException(status_code=404, detail=f"Clip not found for moment '{moment_identifier}'")

    video = await video_db_repository.get_by_id(db, clip.video_id)
    if not video:
        raise HTTPException(status_code=404, detail=f"Video not found for clip '{moment_identifier}'")

    video_identifier = video.identifier

    transcript_data = await load_transcript(f"{video_identifier}.wav")

    if transcript_data is None or "word_timestamps" not in transcript_data:
        logger.warning(f"No transcript available for video '{video_identifier}' (moment '{moment_identifier}')")
        return {
            "moment_identifier": moment_identifier,
            "clip_start": clip.start_time,
            "clip_end": clip.end_time,
            "moment_start": clip.moment.start_time,
            "moment_end": clip.moment.end_time,
            "padding_left": clip.padding_left,
            "padding_right": clip.padding_right,
            "transcript_text": "",
            "words": [],
            "message": "No transcript available for this video.",
        }

    word_timestamps = transcript_data["word_timestamps"]

    extracted_words = extract_words_in_range(word_timestamps, clip.start_time, clip.end_time)
    normalized_words = normalize_word_timestamps(extracted_words, clip.start_time)
    transcript_text = " ".join(w["word"] for w in normalized_words)

    logger.info(
        f"Returning transcript for moment '{moment_identifier}': "
        f"{len(normalized_words)} words, clip [{clip.start_time:.2f}s - {clip.end_time:.2f}s]"
    )

    return {
        "moment_identifier": moment_identifier,
        "clip_start": clip.start_time,
        "clip_end": clip.end_time,
        "moment_start": clip.moment.start_time,
        "moment_end": clip.moment.end_time,
        "padding_left": clip.padding_left,
        "padding_right": clip.padding_right,
        "transcript_text": transcript_text,
        "words": normalized_words,
    }


@router.get("/videos/{video_id}/clips")
async def list_clips_for_video(
    video_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    List all clips for a video with metadata and fresh signed URLs.

    Returns a JSON array of clip objects ordered by start_time.
    """
    clips = await clip_db_repository.get_by_video_identifier(db, video_id)

    if not clips:
        return []

    from app.services.pipeline.upload_service import GCSUploader
    uploader = GCSUploader()

    result = []
    for clip in clips:
        signed_url = uploader.generate_signed_url(clip.cloud_url)

        # Get moment identifier from the relationship
        moment_identifier = None
        if clip.moment:
            moment_identifier = clip.moment.identifier

        result.append({
            "id": clip.id,
            "moment_identifier": moment_identifier,
            "cloud_url": clip.cloud_url,
            "url": signed_url,
            "start_time": clip.start_time,
            "end_time": clip.end_time,
            "padding_left": clip.padding_left,
            "padding_right": clip.padding_right,
            "file_size_kb": clip.file_size_kb,
            "format": clip.format,
            "video_codec": clip.video_codec,
            "audio_codec": clip.audio_codec,
            "resolution": clip.resolution,
            "created_at": clip.created_at.isoformat() if clip.created_at else None,
        })

    return result
