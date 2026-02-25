"""
Moment database repository - CRUD operations for the moments table.
Follows the same module-level async function pattern as video_db_repository.py.
"""
import logging
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.moment import Moment
from app.database.models.video import Video

logger = logging.getLogger(__name__)


async def create(
    session: AsyncSession,
    identifier: str,
    video_id: int,
    start_time: float,
    end_time: float,
    title: str,
    is_refined: bool = False,
    parent_id: Optional[int] = None,
    generation_config_id: Optional[int] = None,
) -> Moment:
    """
    Insert a single moment row.

    Returns the Moment instance with its auto-generated id populated.
    """
    moment = Moment(
        identifier=identifier,
        video_id=video_id,
        start_time=start_time,
        end_time=end_time,
        title=title,
        is_refined=is_refined,
        parent_id=parent_id,
        generation_config_id=generation_config_id,
    )
    session.add(moment)
    await session.flush()
    await session.refresh(moment)
    return moment


async def bulk_create(
    session: AsyncSession,
    moments_data: list[dict],
) -> list[Moment]:
    """
    Insert multiple moments in a single operation.

    Each dict in moments_data must contain: identifier, video_id, start_time,
    end_time, title.  Optional: is_refined, parent_id, generation_config_id.
    """
    instances = []
    for data in moments_data:
        m = Moment(
            identifier=data["identifier"],
            video_id=data["video_id"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            title=data["title"],
            is_refined=data.get("is_refined", False),
            parent_id=data.get("parent_id"),
            generation_config_id=data.get("generation_config_id"),
        )
        instances.append(m)

    session.add_all(instances)
    await session.flush()
    for m in instances:
        await session.refresh(m)
    return instances


async def get_by_identifier(session: AsyncSession, identifier: str) -> Optional[Moment]:
    """Look up a moment by its hex business identifier."""
    stmt = (
        select(Moment)
        .where(Moment.identifier == identifier)
        .options(
            selectinload(Moment.generation_config),
            selectinload(Moment.parent),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, id: int) -> Optional[Moment]:
    """Look up a moment by its numeric database ID."""
    stmt = (
        select(Moment)
        .where(Moment.id == id)
        .options(
            selectinload(Moment.generation_config),
            selectinload(Moment.parent),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_video_id(session: AsyncSession, video_id: int) -> list[Moment]:
    """Return all moments for a video (by numeric video id), ordered by start_time."""
    stmt = (
        select(Moment)
        .where(Moment.video_id == video_id)
        .options(
            selectinload(Moment.generation_config),
            selectinload(Moment.parent),
        )
        .order_by(Moment.start_time.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_by_video_identifier(session: AsyncSession, video_identifier: str) -> list[Moment]:
    """
    Return all moments for a video (by string identifier), ordered by start_time.
    Joins with the videos table internally.
    """
    stmt = (
        select(Moment)
        .join(Video, Moment.video_id == Video.id)
        .where(Video.identifier == video_identifier)
        .options(
            selectinload(Moment.generation_config),
            selectinload(Moment.parent),
            selectinload(Moment.clip),
        )
        .order_by(Moment.start_time.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_originals_for_video(session: AsyncSession, video_id: int) -> list[Moment]:
    """Return only non-refined moments for a video, ordered by start_time."""
    stmt = (
        select(Moment)
        .where(Moment.video_id == video_id, Moment.is_refined == False)  # noqa: E712
        .options(
            selectinload(Moment.generation_config),
            selectinload(Moment.parent),
        )
        .order_by(Moment.start_time.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_originals_for_video_identifier(session: AsyncSession, video_identifier: str) -> list[Moment]:
    """Return only non-refined moments for a video (by string identifier)."""
    stmt = (
        select(Moment)
        .join(Video, Moment.video_id == Video.id)
        .where(Video.identifier == video_identifier, Moment.is_refined == False)  # noqa: E712
        .options(
            selectinload(Moment.generation_config),
            selectinload(Moment.parent),
        )
        .order_by(Moment.start_time.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_refined_for_parent(session: AsyncSession, parent_db_id: int) -> Optional[Moment]:
    """
    Return the refined moment for a given parent moment (single or None).
    Enforces the one-refined-per-parent invariant at the query level.
    """
    stmt = (
        select(Moment)
        .where(Moment.parent_id == parent_db_id, Moment.is_refined == True)  # noqa: E712
        .options(
            selectinload(Moment.generation_config),
            selectinload(Moment.parent),
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_or_update_refined(
    session: AsyncSession,
    video_id: int,
    parent_db_id: int,
    identifier: str,
    start_time: float,
    end_time: float,
    title: str,
    generation_config_id: Optional[int] = None,
) -> Moment:
    """
    Upsert a refined moment: one refined copy per parent, always.

    If a refined moment already exists for this parent, update it in place.
    Otherwise insert a new one.
    """
    existing = await get_refined_for_parent(session, parent_db_id)
    if existing:
        logger.info(f"Updating existing refined moment {existing.id} (identifier={existing.identifier}) for parent {parent_db_id}")
        existing.identifier = identifier
        existing.start_time = start_time
        existing.end_time = end_time
        existing.title = title
        existing.generation_config_id = generation_config_id
        await session.flush()
        await session.refresh(existing)
        return existing

    logger.info(f"Creating new refined moment (identifier={identifier}) for parent {parent_db_id}, video_id={video_id}")
    return await create(
        session,
        identifier=identifier,
        video_id=video_id,
        start_time=start_time,
        end_time=end_time,
        title=title,
        is_refined=True,
        parent_id=parent_db_id,
        generation_config_id=generation_config_id,
    )


async def delete_by_identifier(session: AsyncSession, identifier: str) -> bool:
    """Delete a single moment by its string identifier. Returns True if deleted."""
    stmt = delete(Moment).where(Moment.identifier == identifier)
    result = await session.execute(stmt)
    return result.rowcount > 0


async def delete_all_for_video(session: AsyncSession, video_id: int) -> int:
    """Delete all moments for a video. Returns the count of deleted rows."""
    stmt = delete(Moment).where(Moment.video_id == video_id)
    result = await session.execute(stmt)
    return result.rowcount


async def delete_all_for_video_identifier(session: AsyncSession, video_identifier: str) -> int:
    """
    Delete all moments for a video by its string identifier.
    Uses a sub-query to resolve the video id.
    """
    video_subq = select(Video.id).where(Video.identifier == video_identifier).scalar_subquery()
    stmt = delete(Moment).where(Moment.video_id == video_subq)
    result = await session.execute(stmt)
    return result.rowcount
