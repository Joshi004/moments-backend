"""
Transcript-related API endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
import time

from app.database.dependencies import get_db
from app.repositories import video_db_repository
from app.services.transcript_service import load_transcript
from app.core.logging import (
    log_event,
    log_operation_error,
    get_request_id
)

router = APIRouter()


@router.get("/videos/{video_id}/transcript")
async def get_transcript(video_id: str, db: AsyncSession = Depends(get_db)):
    """Get transcript for a video."""
    start_time = time.time()
    operation = "get_transcript"

    log_event(
        level="DEBUG",
        logger="app.api.endpoints.transcripts",
        function="get_transcript",
        operation=operation,
        event="operation_start",
        message=f"Getting transcript for {video_id}",
        context={"video_id": video_id, "request_id": get_request_id()}
    )

    try:
        # Validate video exists in database
        video = await video_db_repository.get_by_identifier(db, video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        # Load transcript from database
        audio_filename = f"{video_id}.wav"
        transcript_data = await load_transcript(audio_filename)

        if transcript_data is None:
            raise HTTPException(status_code=404, detail="Transcript not found for this video")

        duration = time.time() - start_time
        log_event(
            level="DEBUG",
            logger="app.api.endpoints.transcripts",
            function="get_transcript",
            operation=operation,
            event="operation_complete",
            message="Successfully retrieved transcript",
            context={
                "video_id": video_id,
                "has_segments": "segment_timestamps" in transcript_data if transcript_data else False,
                "duration_seconds": duration
            }
        )

        return transcript_data

    except HTTPException:
        raise
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.api.endpoints.transcripts",
            function="get_transcript",
            operation=operation,
            error=e,
            message="Error getting transcript",
            context={"video_id": video_id, "duration_seconds": duration}
        )
        raise
