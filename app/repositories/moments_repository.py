"""
Repository for managing moment data persistence.

Phase 6: All methods now delegate to moment_db_repository (database-backed).
This class is kept for backward compatibility with any code that instantiates
MomentsRepository. New code should use moment_db_repository directly.
"""
import hashlib
import logging
from pathlib import Path
from typing import Optional, List, Dict

from app.database.session import get_session_factory
from app.repositories import moment_db_repository as moment_db_repo
from app.repositories import video_db_repository as video_db_repo
from app.services.moments_service import _moment_to_dict

logger = logging.getLogger(__name__)


class MomentsRepository:
    """Repository for moment operations -- delegates to database."""

    def __init__(self, base_path: Path = None):
        pass

    @staticmethod
    def generate_moment_id(start_time: float, end_time: float) -> str:
        id_string = f"{start_time:.2f}_{end_time:.2f}"
        return hashlib.sha256(id_string.encode()).hexdigest()[:16]

    async def get_by_video(self, video_filename: str) -> List[Dict]:
        """Load all moments for a video from the database."""
        identifier = Path(video_filename).stem
        session_factory = get_session_factory()
        async with session_factory() as session:
            moments = await moment_db_repo.get_by_video_identifier(session, identifier)
            return [_moment_to_dict(m) for m in moments]

    async def get_by_id(self, video_filename: str, moment_id: str) -> Optional[Dict]:
        """Get a specific moment by its identifier."""
        session_factory = get_session_factory()
        async with session_factory() as session:
            moment = await moment_db_repo.get_by_identifier(session, moment_id)
            if moment is None:
                return None
            return _moment_to_dict(moment)

    async def save(self, video_filename: str, moments: List[Dict]) -> bool:
        """Replace all moments for a video (delete + bulk insert)."""
        from app.services.moments_service import save_moments
        return await save_moments(video_filename, moments)

    async def add(self, video_filename: str, moment: Dict) -> Optional[Dict]:
        """Add a moment to a video."""
        from app.services.moments_service import add_moment
        success, _, created = await add_moment(video_filename, moment, float('inf'))
        return created if success else None

    async def delete(self, video_filename: str, moment_id: str) -> bool:
        """Delete a moment by identifier."""
        session_factory = get_session_factory()
        async with session_factory() as session:
            deleted = await moment_db_repo.delete_by_identifier(session, moment_id)
            await session.commit()
            return deleted

    async def exists(self, video_filename: str) -> bool:
        """Check if any moments exist for a video."""
        identifier = Path(video_filename).stem
        session_factory = get_session_factory()
        async with session_factory() as session:
            moments = await moment_db_repo.get_by_video_identifier(session, identifier)
            return len(moments) > 0
