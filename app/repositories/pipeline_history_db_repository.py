"""
Pipeline History database repository - CRUD operations for the pipeline_history table.
Follows the module-level function pattern of other db repositories.
"""
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.pipeline_history import PipelineHistory
from app.database.models.video import Video


async def create(
    session: AsyncSession,
    identifier: str,
    video_id: int,
    pipeline_type: str,
    status: str,
    started_at: datetime,
    generation_config_id: Optional[int] = None,
) -> PipelineHistory:
    """
    Insert a new pipeline history record.

    Called at pipeline start with status='running'. The generation_config_id
    is NULL initially and set later after the MOMENT_GENERATION stage completes.

    Args:
        session: Async database session
        identifier: Unique pipeline run identifier (e.g. 'pipeline:motivation:1707610697193')
        video_id: Numeric database ID of the video (from videos table)
        pipeline_type: 'full', 'moments_only', or 'clips_only'
        status: Initial status, typically 'running'
        started_at: Pipeline start time
        generation_config_id: FK to generation_configs; None until moment generation runs

    Returns:
        Created PipelineHistory instance with id populated
    """
    record = PipelineHistory(
        identifier=identifier,
        video_id=video_id,
        pipeline_type=pipeline_type,
        status=status,
        started_at=started_at,
        generation_config_id=generation_config_id,
    )
    session.add(record)
    await session.flush()
    await session.refresh(record)
    return record


async def update_status(
    session: AsyncSession,
    history_id: int,
    status: str,
    completed_at: Optional[datetime] = None,
    duration_seconds: Optional[float] = None,
    total_moments_generated: Optional[int] = None,
    total_clips_created: Optional[int] = None,
    error_stage: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Optional[PipelineHistory]:
    """
    Partially update a pipeline history record.

    Only sets the columns that are explicitly provided (not None).
    Used at pipeline completion, failure, or cancellation.

    Args:
        session: Async database session
        history_id: Numeric DB id of the record to update
        status: New status ('completed', 'failed', 'cancelled')
        completed_at: When the pipeline finished
        duration_seconds: Total elapsed time in seconds
        total_moments_generated: Count of moments produced
        total_clips_created: Count of clips extracted
        error_stage: Name of the stage that failed (if any)
        error_message: Error details (if any)

    Returns:
        Updated PipelineHistory or None if not found
    """
    values: dict = {"status": status}
    if completed_at is not None:
        values["completed_at"] = completed_at
    if duration_seconds is not None:
        values["duration_seconds"] = duration_seconds
    if total_moments_generated is not None:
        values["total_moments_generated"] = total_moments_generated
    if total_clips_created is not None:
        values["total_clips_created"] = total_clips_created
    if error_stage is not None:
        values["error_stage"] = error_stage
    if error_message is not None:
        values["error_message"] = error_message

    stmt = (
        update(PipelineHistory)
        .where(PipelineHistory.id == history_id)
        .values(**values)
        .returning(PipelineHistory)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def set_generation_config_id(
    session: AsyncSession,
    history_id: int,
    generation_config_id: int,
) -> Optional[PipelineHistory]:
    """
    Set the generation_config_id on a pipeline history record.

    Called by the orchestrator after the MOMENT_GENERATION stage completes
    and the generation config ID becomes available from Phase 5.

    Args:
        session: Async database session
        history_id: Numeric DB id of the record
        generation_config_id: FK to generation_configs table

    Returns:
        Updated PipelineHistory or None if not found
    """
    stmt = (
        update(PipelineHistory)
        .where(PipelineHistory.id == history_id)
        .values(generation_config_id=generation_config_id)
        .returning(PipelineHistory)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_identifier(
    session: AsyncSession,
    identifier: str,
) -> Optional[PipelineHistory]:
    """
    Look up a pipeline history record by its string identifier.

    Used for idempotency checks (e.g. migration script).

    Args:
        session: Async database session
        identifier: Pipeline run identifier string

    Returns:
        PipelineHistory or None if not found
    """
    stmt = select(PipelineHistory).where(PipelineHistory.identifier == identifier)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_id(
    session: AsyncSession,
    history_id: int,
) -> Optional[PipelineHistory]:
    """
    Look up a pipeline history record by its numeric database ID.

    Args:
        session: Async database session
        history_id: Numeric database ID

    Returns:
        PipelineHistory or None if not found
    """
    stmt = select(PipelineHistory).where(PipelineHistory.id == history_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_video_identifier(
    session: AsyncSession,
    video_identifier: str,
    limit: int = 20,
    status_filter: Optional[str] = None,
) -> List[PipelineHistory]:
    """
    Return pipeline history records for a video, ordered newest first.

    Joins with the videos table internally so the caller can pass the string
    video identifier (what the frontend sends) rather than the numeric DB id.

    Args:
        session: Async database session
        video_identifier: Video string identifier (e.g. 'motivation')
        limit: Maximum number of records to return
        status_filter: Optional status to filter by ('completed', 'failed', etc.)

    Returns:
        List of PipelineHistory instances, newest first
    """
    stmt = (
        select(PipelineHistory)
        .join(Video, PipelineHistory.video_id == Video.id)
        .where(Video.identifier == video_identifier)
        .order_by(PipelineHistory.started_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(PipelineHistory.status == status_filter)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_recent(
    session: AsyncSession,
    limit: int = 20,
) -> List[PipelineHistory]:
    """
    Return the most recent pipeline runs across all videos.

    Useful for admin dashboards or system-wide monitoring.

    Args:
        session: Async database session
        limit: Maximum number of records to return

    Returns:
        List of PipelineHistory instances, newest first
    """
    stmt = (
        select(PipelineHistory)
        .order_by(PipelineHistory.started_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
