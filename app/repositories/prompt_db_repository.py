"""
Prompt database repository.

This module provides CRUD operations for the prompts table with SHA-256 hash-based
deduplication to avoid storing duplicate prompts.
"""
import hashlib
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database.models.prompt import Prompt

logger = logging.getLogger(__name__)


def compute_prompt_hash(user_prompt: str, system_prompt: str) -> str:
    """
    Compute SHA-256 hash of user_prompt + system_prompt for deduplication.
    
    Args:
        user_prompt: User's custom prompt text
        system_prompt: System/template prompt text
    
    Returns:
        64-character hex string (SHA-256 hash)
    """
    # Use || as delimiter to prevent collision (e.g., "ab"+"c" vs "a"+"bc")
    combined = f"{user_prompt}||{system_prompt}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


async def get_by_id(session: AsyncSession, id: int) -> Optional[Prompt]:
    """
    Get prompt by its numeric database ID.
    
    Args:
        session: Database session
        id: Prompt ID
    
    Returns:
        Prompt record or None if not found
    """
    stmt = select(Prompt).where(Prompt.id == id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_hash(session: AsyncSession, prompt_hash: str) -> Optional[Prompt]:
    """
    Get prompt by its SHA-256 hash.
    
    Args:
        session: Database session
        prompt_hash: 64-character hex string
    
    Returns:
        Prompt record or None if not found
    """
    stmt = select(Prompt).where(Prompt.prompt_hash == prompt_hash)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_or_get(
    session: AsyncSession,
    user_prompt: str,
    system_prompt: str
) -> Prompt:
    """
    Create a new prompt or return existing one with the same content.
    
    This function implements hash-based deduplication: if a prompt with the same
    user_prompt and system_prompt already exists, it returns the existing record.
    Otherwise, it creates a new one.
    
    Args:
        session: Database session
        user_prompt: User's custom prompt text
        system_prompt: System/template prompt text
    
    Returns:
        Prompt record (either existing or newly created)
    """
    # Compute hash for deduplication
    prompt_hash = compute_prompt_hash(user_prompt, system_prompt)
    
    # Check if prompt already exists
    existing = await get_by_hash(session, prompt_hash)
    if existing:
        logger.debug(f"Found existing prompt with hash {prompt_hash[:8]}...")
        return existing
    
    # Create new prompt
    new_prompt = Prompt(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        prompt_hash=prompt_hash
    )
    session.add(new_prompt)
    
    try:
        await session.flush()
        await session.refresh(new_prompt)
        logger.info(f"Created new prompt with hash {prompt_hash[:8]}... (id={new_prompt.id})")
        return new_prompt
    except IntegrityError:
        # Race condition: another transaction created the same prompt
        await session.rollback()
        logger.debug(f"Integrity error on insert, retrying select for hash {prompt_hash[:8]}...")
        existing = await get_by_hash(session, prompt_hash)
        if existing:
            return existing
        else:
            # Very unlikely case: IntegrityError but record not found
            raise Exception(f"Failed to create or retrieve prompt with hash {prompt_hash}")
