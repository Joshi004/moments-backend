"""
Database session management.
Provides async SQLAlchemy engine, session factory, and lifecycle functions.
"""
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import get_settings

# Global engine instance (initialized lazily)
_engine: Optional[AsyncEngine] = None
_async_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


async def init_db() -> None:
    """
    Initialize the database engine and session factory.
    Called once at application startup.
    """
    global _engine, _async_session_factory
    
    settings = get_settings()
    
    _engine = create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        echo=settings.database_echo,
    )
    
    _async_session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def close_db() -> None:
    """
    Close the database engine and cleanup resources.
    Called once at application shutdown.
    """
    global _engine
    
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async generator that yields a database session.
    Ensures session is properly closed after use.
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    async with _async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get the async session factory.
    Used by dependencies.py.
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _async_session_factory
