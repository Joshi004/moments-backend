"""
One-time migration script: Migrate existing transcript JSON files to database.

This script:
1. Scans static/transcripts/ for all .json files
2. For each JSON file:
   - Parses JSON content
   - Extracts identifier from filename stem
   - Looks up video in database by identifier
   - Skips if no matching video found (e.g., clip transcripts)
   - Skips if transcript already exists in database
   - Maps JSON fields to database columns
   - Inserts into transcripts table
3. Prints progress and summary

Usage:
    cd moments-backend
    python -m scripts.migrate_transcripts_to_db
"""
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database.session import init_db, close_db, get_session_factory
from app.repositories import video_db_repository
from app.repositories import transcript_db_repository

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_transcripts_directory() -> Path:
    """Get the path to the transcripts directory."""
    backend_dir = Path(__file__).parent.parent
    return backend_dir / "static" / "transcripts"


def parse_transcript_json(json_path: Path) -> Optional[Dict[str, Any]]:
    """
    Parse a transcript JSON file.
    
    Args:
        json_path: Path to JSON file
    
    Returns:
        Dictionary with transcript data or None if parsing fails
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON file {json_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading file {json_path}: {e}")
        return None


async def migrate_transcript(
    json_path: Path,
    index: int,
    total: int
) -> tuple[bool, str]:
    """
    Migrate a single transcript JSON file to the database.
    
    Args:
        json_path: Path to transcript JSON file
        index: Current file index (for progress display)
        total: Total number of files
    
    Returns:
        Tuple of (success: bool, status_message: str)
    """
    logger.info(f"\nMigrating {index}/{total}: {json_path.name}")
    
    # Parse JSON
    transcript_data = parse_transcript_json(json_path)
    if transcript_data is None:
        return False, "Failed to parse JSON"
    
    # Extract identifier from filename stem
    identifier = json_path.stem
    logger.info(f"  - Identifier: {identifier}")
    
    # Get database session
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            # Look up video in database
            video = await video_db_repository.get_by_identifier(session, identifier)
            if not video:
                logger.warning(f"  - Video '{identifier}' NOT found in database")
                return False, "No matching video"
            
            logger.info(f"  - Video found in database (id={video.id})")
            
            # Check if transcript already exists
            exists = await transcript_db_repository.exists_for_video(session, video.id)
            if exists:
                logger.warning(f"  - Transcript already exists in database")
                return False, "Already exists"
            
            # Map JSON fields to database columns
            # Note: JSON uses "transcription" key, DB uses "full_text" column
            full_text = transcript_data.get("transcription", "")
            word_timestamps = transcript_data.get("word_timestamps", [])
            segment_timestamps = transcript_data.get("segment_timestamps", [])
            processing_time = transcript_data.get("processing_time")
            
            # Compute counts
            number_of_words = len(word_timestamps) if word_timestamps else 0
            number_of_segments = len(segment_timestamps) if segment_timestamps else 0
            
            logger.info(f"  - Words: {number_of_words}, Segments: {number_of_segments}")
            
            # Insert into database
            transcript = await transcript_db_repository.create(
                session=session,
                video_id=video.id,
                full_text=full_text,
                word_timestamps=word_timestamps,
                segment_timestamps=segment_timestamps,
                language="en",  # Default for existing transcripts
                number_of_words=number_of_words,
                number_of_segments=number_of_segments,
                transcription_service="parakeet",  # Default for existing transcripts
                processing_time_seconds=processing_time,
            )
            
            await session.commit()
            
            logger.info(f"  - Database: inserted transcript id={transcript.id}")
            logger.info(f"  ✓ Done")
            
            return True, "Migrated successfully"
            
        except Exception as e:
            await session.rollback()
            logger.error(f"  - Error during migration: {e}")
            return False, f"Error: {str(e)}"


async def main():
    """Main migration function."""
    logger.info("Starting transcript migration...")
    
    # Initialize database
    await init_db()
    
    try:
        # Get transcripts directory
        transcripts_dir = get_transcripts_directory()
        if not transcripts_dir.exists():
            logger.error(f"Transcripts directory not found: {transcripts_dir}")
            return
        
        # Find all JSON files
        json_files = sorted(transcripts_dir.glob("*.json"))
        total_files = len(json_files)
        
        logger.info(f"Found {total_files} JSON files in {transcripts_dir}\n")
        
        if total_files == 0:
            logger.warning("No transcript files found. Nothing to migrate.")
            return
        
        # Counters
        migrated = 0
        skipped_no_video = 0
        skipped_already_exists = 0
        failed = 0
        
        # Migrate each file
        for index, json_path in enumerate(json_files, start=1):
            success, status = await migrate_transcript(json_path, index, total_files)
            
            if success:
                migrated += 1
            elif "No matching video" in status:
                skipped_no_video += 1
            elif "Already exists" in status:
                skipped_already_exists += 1
            else:
                failed += 1
        
        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("Migration complete:")
        logger.info(f"  Total files: {total_files}")
        logger.info(f"  Migrated: {migrated}")
        logger.info(f"  Skipped (no matching video): {skipped_no_video}")
        logger.info(f"  Skipped (already in DB): {skipped_already_exists}")
        logger.info(f"  Failed: {failed}")
        logger.info("=" * 60)
        
    finally:
        # Close database
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
