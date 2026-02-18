"""
Moments service - database-backed moment operations.

Phase 6: All moment storage now goes through PostgreSQL via moment_db_repository.
FileLock and JSON file I/O have been removed.
"""
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import logging
import hashlib
import time

from app.database.session import get_session_factory
from app.repositories import moment_db_repository as moment_db_repo
from app.repositories import video_db_repository as video_db_repo
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)

logger = logging.getLogger(__name__)


def generate_moment_id(start_time: float, end_time: float, prefix: str = "") -> str:
    """
    Generate a unique ID for a moment based on its timestamps.

    Args:
        start_time: Start time in seconds
        end_time: End time in seconds
        prefix: Optional prefix to namespace the ID (e.g. "refined_")

    Returns:
        Hash-based unique identifier
    """
    id_string = f"{prefix}{start_time:.2f}_{end_time:.2f}"
    return hashlib.sha256(id_string.encode()).hexdigest()[:16]


def _moment_to_dict(moment) -> dict:
    """
    Convert a SQLAlchemy Moment model instance to the dictionary format
    expected by the API and existing callers.

    Mapping:
        moment.identifier  -> dict["id"]
        moment.parent.identifier -> dict["parent_id"]  (string, not numeric)
        moment.generation_config.model -> dict["model_name"]
    """
    parent_identifier = None
    if moment.parent is not None:
        parent_identifier = moment.parent.identifier

    model_name = None
    generation_config_dict = None
    if moment.generation_config is not None:
        model_name = moment.generation_config.model
        generation_config_dict = _config_to_dict(moment.generation_config)

    return {
        "id": moment.identifier,
        "start_time": moment.start_time,
        "end_time": moment.end_time,
        "title": moment.title,
        "is_refined": moment.is_refined,
        "parent_id": parent_identifier,
        "model_name": model_name,
        "generation_config": generation_config_dict,
    }


def _config_to_dict(config) -> dict:
    """Convert a GenerationConfig model to a dictionary for API responses."""
    return {
        "model": config.model,
        "operation_type": config.operation_type,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "top_k": config.top_k,
        "min_moment_length": config.min_moment_length,
        "max_moment_length": config.max_moment_length,
        "min_moments": config.min_moments,
        "max_moments": config.max_moments,
    }


async def load_moments(video_filename: str) -> List[Dict]:
    """
    Load moments for a video from the database.

    Args:
        video_filename: Name of the video file (e.g. "motivation.mp4")

    Returns:
        List of moment dictionaries, or empty list if none found
    """
    identifier = Path(video_filename).stem
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            moments = await moment_db_repo.get_by_video_identifier(session, identifier)
            return [_moment_to_dict(m) for m in moments]
    except Exception as e:
        logger.error(f"Error loading moments for {video_filename}: {e}")
        return []


async def save_moments(video_filename: str, moments: List[Dict]) -> bool:
    """
    Replace all moments for a video (delete existing + bulk insert).
    Used during regeneration.

    Args:
        video_filename: Name of the video file
        moments: List of moment dictionaries

    Returns:
        True if successful, False otherwise
    """
    operation = "save_moments"
    start_time_op = time.time()

    log_operation_start(
        logger="app.services.moments_service",
        function="save_moments",
        operation=operation,
        message="Saving moments to database",
        context={
            "video_filename": video_filename,
            "moment_count": len(moments),
            "request_id": get_request_id()
        }
    )

    identifier = Path(video_filename).stem

    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            video = await video_db_repo.get_by_identifier(session, identifier)
            if not video:
                logger.error(f"Video '{identifier}' not found in database")
                return False

            await moment_db_repo.delete_all_for_video(session, video.id)

            moments_data = []
            for m in moments:
                m_id = m.get("id") or generate_moment_id(m["start_time"], m["end_time"])
                moments_data.append({
                    "identifier": m_id,
                    "video_id": video.id,
                    "start_time": m["start_time"],
                    "end_time": m["end_time"],
                    "title": m["title"],
                    "is_refined": m.get("is_refined", False),
                    "parent_id": None,
                    "generation_config_id": m.get("generation_config_id"),
                })

            if moments_data:
                await moment_db_repo.bulk_create(session, moments_data)

            await session.commit()

        duration = time.time() - start_time_op
        log_operation_complete(
            logger="app.services.moments_service",
            function="save_moments",
            operation=operation,
            message="Successfully saved moments to database",
            context={
                "video_filename": video_filename,
                "moment_count": len(moments),
            },
            duration=duration
        )
        return True

    except Exception as e:
        duration = time.time() - start_time_op
        log_operation_error(
            logger="app.services.moments_service",
            function="save_moments",
            operation=operation,
            error=e,
            message="Error saving moments to database",
            context={
                "video_filename": video_filename,
                "duration_seconds": duration
            }
        )
        return False


def validate_moment(moment: Dict, existing_moments: List[Dict], video_duration: float) -> Tuple[bool, Optional[str]]:
    """
    Validate a moment against rules.

    Args:
        moment: Dictionary with start_time, end_time, and title
        existing_moments: List of existing moments
        video_duration: Total duration of the video in seconds

    Returns:
        Tuple of (is_valid, error_message)
    """
    operation = "validate_moment"

    log_event(
        level="DEBUG",
        logger="app.services.moments_service",
        function="validate_moment",
        operation=operation,
        event="validation_start",
        message="Validating moment",
        context={
            "moment": {
                "start_time": moment.get("start_time"),
                "end_time": moment.get("end_time"),
                "title": moment.get("title", "")[:50]
            },
            "video_duration": video_duration,
            "existing_moments_count": len(existing_moments)
        }
    )
    if 'start_time' not in moment or 'end_time' not in moment or 'title' not in moment:
        return False, "Missing required fields: start_time, end_time, and title are required"

    start_time = moment['start_time']
    end_time = moment['end_time']
    title = moment['title']
    is_refined = moment.get('is_refined', False)

    try:
        start_time = float(start_time)
        end_time = float(end_time)
    except (ValueError, TypeError):
        return False, "start_time and end_time must be numbers"

    if not isinstance(title, str) or not title.strip():
        return False, "title must be a non-empty string"

    if start_time < 0:
        return False, "start_time must be >= 0"

    if end_time > video_duration:
        return False, f"end_time must be <= video duration ({video_duration} seconds)"

    if end_time <= start_time:
        return False, "end_time must be greater than start_time"

    if not is_refined:
        duration = end_time - start_time
        if duration > 120:
            return False, f"Moment duration ({duration} seconds) exceeds maximum of 120 seconds (2 minutes)"

    if not is_refined:
        for existing in existing_moments:
            existing_start = existing.get('start_time', 0)
            existing_end = existing.get('end_time', 0)

            if start_time < existing_end and end_time > existing_start:
                log_event(
                    level="DEBUG",
                    logger="app.services.moments_service",
                    function="validate_moment",
                    operation=operation,
                    event="validation_error",
                    message="Moment overlaps with existing moment",
                    context={
                        "start_time": start_time,
                        "end_time": end_time,
                        "existing_start": existing_start,
                        "existing_end": existing_end,
                        "existing_title": existing.get('title', 'Untitled')
                    }
                )
                return False, f"Moment overlaps with existing moment '{existing.get('title', 'Untitled')}' ({existing_start}s - {existing_end}s)"

    log_event(
        level="DEBUG",
        logger="app.services.moments_service",
        function="validate_moment",
        operation=operation,
        event="validation_complete",
        message="Moment validation passed",
        context={
            "start_time": start_time,
            "end_time": end_time,
            "duration": end_time - start_time
        }
    )

    return True, None


async def get_moment_by_id(video_filename: str, moment_id: str) -> Optional[Dict]:
    """
    Get a moment by its identifier.

    Args:
        video_filename: Name of the video file (unused but kept for API compatibility)
        moment_id: Hex identifier of the moment

    Returns:
        Moment dictionary or None if not found
    """
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            moment = await moment_db_repo.get_by_identifier(session, moment_id)
            if moment is None:
                return None
            return _moment_to_dict(moment)
    except Exception as e:
        logger.error(f"Error getting moment {moment_id}: {e}")
        return None


async def add_moment(video_filename: str, moment: Dict, video_duration: float) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Add a moment to a video after validation.

    Args:
        video_filename: Name of the video file
        moment: Dictionary with start_time, end_time, and title
        video_duration: Total duration of the video in seconds

    Returns:
        Tuple of (success, error_message, created_moment)
    """
    existing_moments = await load_moments(video_filename)

    is_valid, error_message = validate_moment(moment, existing_moments, video_duration)
    if not is_valid:
        return False, error_message, None

    if 'id' not in moment or not moment['id']:
        moment['id'] = generate_moment_id(moment['start_time'], moment['end_time'])

    if 'is_refined' not in moment:
        moment['is_refined'] = False
    if 'parent_id' not in moment:
        moment['parent_id'] = None
    if 'generation_config' not in moment:
        moment['generation_config'] = None

    identifier_str = Path(video_filename).stem

    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            video = await video_db_repo.get_by_identifier(session, identifier_str)
            if not video:
                return False, f"Video '{identifier_str}' not found in database", None

            parent_db_id = None
            if moment.get('parent_id') and moment.get('is_refined'):
                parent_moment = await moment_db_repo.get_by_identifier(session, moment['parent_id'])
                if parent_moment:
                    parent_db_id = parent_moment.id

            db_moment = await moment_db_repo.create(
                session,
                identifier=moment['id'],
                video_id=video.id,
                start_time=moment['start_time'],
                end_time=moment['end_time'],
                title=moment['title'],
                is_refined=moment.get('is_refined', False),
                parent_id=parent_db_id,
                generation_config_id=moment.get('generation_config_id'),
            )
            await session.commit()

            moment_dict = _moment_to_dict(db_moment)
            return True, None, moment_dict

    except Exception as e:
        logger.error(f"Error adding moment: {e}")
        return False, f"Failed to save moment: {e}", None
