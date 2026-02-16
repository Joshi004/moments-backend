"""
FastAPI dependencies for database session injection.
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a database session.
    
    Usage in endpoints:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Item))
            return result.scalars().all()
    
    The session is automatically committed on success or rolled back on error.
    """
    session_factory = get_session_factory()
    
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
