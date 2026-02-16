"""
Generation config database repository.

This module provides CRUD operations for the generation_configs table with SHA-256
hash-based deduplication to avoid storing duplicate configurations.
"""
import hashlib
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.database.models.generation_config import GenerationConfig

logger = logging.getLogger(__name__)


def compute_config_hash(
    prompt_id: int,
    model: str,
    operation_type: str,
    temperature: Optional[float],
    top_p: Optional[float],
    top_k: Optional[int],
    min_moment_length: Optional[float],
    max_moment_length: Optional[float],
    min_moments: Optional[int],
    max_moments: Optional[int]
) -> str:
    """
    Compute SHA-256 hash of configuration parameters for deduplication.
    
    CRITICAL: transcript_id is NOT included in the hash. This allows the same
    configuration to be reused across different videos/transcripts.
    
    Args:
        prompt_id: Foreign key to prompts table
        model: AI model identifier
        operation_type: "generation" or "refinement"
        temperature: Sampling temperature
        top_p: Top-p sampling parameter
        top_k: Top-k sampling parameter
        min_moment_length: Minimum moment duration
        max_moment_length: Maximum moment duration
        min_moments: Minimum number of moments
        max_moments: Maximum number of moments
    
    Returns:
        64-character hex string (SHA-256 hash)
    """
    # Build deterministic string representation
    # Use str() to handle None consistently ("None" string)
    parts = [
        str(prompt_id),
        model,
        operation_type,
        str(temperature),
        str(top_p),
        str(top_k),
        str(min_moment_length),
        str(max_moment_length),
        str(min_moments),
        str(max_moments)
    ]
    combined = "|".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


async def get_by_id(session: AsyncSession, id: int) -> Optional[GenerationConfig]:
    """
    Get generation config by its numeric database ID.
    
    Args:
        session: Database session
        id: Config ID
    
    Returns:
        GenerationConfig record or None if not found
    """
    stmt = select(GenerationConfig).where(GenerationConfig.id == id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_hash(session: AsyncSession, config_hash: str) -> Optional[GenerationConfig]:
    """
    Get generation config by its SHA-256 hash.
    
    Args:
        session: Database session
        config_hash: 64-character hex string
    
    Returns:
        GenerationConfig record or None if not found
    """
    stmt = select(GenerationConfig).where(GenerationConfig.config_hash == config_hash)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_or_get(
    session: AsyncSession,
    prompt_id: int,
    model: str,
    operation_type: str,
    transcript_id: Optional[int],
    temperature: Optional[float],
    top_p: Optional[float],
    top_k: Optional[int],
    min_moment_length: Optional[float],
    max_moment_length: Optional[float],
    min_moments: Optional[int],
    max_moments: Optional[int]
) -> GenerationConfig:
    """
    Create a new generation config or return existing one with the same parameters.
    
    This function implements hash-based deduplication: if a config with the same
    parameters (excluding transcript_id) already exists, it returns the existing record.
    Otherwise, it creates a new one.
    
    IMPORTANT: transcript_id is stored in the record but NOT used for deduplication.
    This allows the same config to be reused across different videos.
    
    Args:
        session: Database session
        prompt_id: Foreign key to prompts table
        model: AI model identifier
        operation_type: "generation" or "refinement"
        transcript_id: Foreign key to transcripts table (optional)
        temperature: Sampling temperature
        top_p: Top-p sampling parameter
        top_k: Top-k sampling parameter
        min_moment_length: Minimum moment duration
        max_moment_length: Maximum moment duration
        min_moments: Minimum number of moments
        max_moments: Maximum number of moments
    
    Returns:
        GenerationConfig record (either existing or newly created)
    """
    # Compute hash for deduplication (excludes transcript_id)
    config_hash = compute_config_hash(
        prompt_id=prompt_id,
        model=model,
        operation_type=operation_type,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        min_moment_length=min_moment_length,
        max_moment_length=max_moment_length,
        min_moments=min_moments,
        max_moments=max_moments
    )
    
    # Check if config already exists
    existing = await get_by_hash(session, config_hash)
    if existing:
        logger.debug(f"Found existing config with hash {config_hash[:8]}... (id={existing.id})")
        return existing
    
    # Create new config
    new_config = GenerationConfig(
        prompt_id=prompt_id,
        transcript_id=transcript_id,
        model=model,
        operation_type=operation_type,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
        min_moment_length=min_moment_length,
        max_moment_length=max_moment_length,
        min_moments=min_moments,
        max_moments=max_moments,
        config_hash=config_hash
    )
    session.add(new_config)
    
    try:
        await session.flush()
        await session.refresh(new_config)
        logger.info(f"Created new config with hash {config_hash[:8]}... (id={new_config.id})")
        return new_config
    except IntegrityError:
        # Race condition: another transaction created the same config
        await session.rollback()
        logger.debug(f"Integrity error on insert, retrying select for hash {config_hash[:8]}...")
        existing = await get_by_hash(session, config_hash)
        if existing:
            return existing
        else:
            # Very unlikely case: IntegrityError but record not found
            raise Exception(f"Failed to create or retrieve config with hash {config_hash}")
