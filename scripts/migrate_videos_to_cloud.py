"""
One-time migration script: Upload existing videos to GCS and register in database.

This script:
1. Scans static/videos/ for all video files
2. For each video:
   - Skips if already in database
   - Extracts metadata via ffprobe
   - Uploads to GCS
   - Inserts into videos table
3. Prints progress and summary

Usage:
    cd moments-backend
    python -m scripts.migrate_videos_to_cloud
"""
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent directory to path to import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.config import get_settings
from app.database.session import init_db, close_db, get_session_factory
from app.services.pipeline.upload_service import GCSUploader
from app.repositories import video_db_repository
from app.utils.video import get_videos_directory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def extract_video_metadata(video_path: Path) -> Dict[str, Any]:
    """
    Extract video metadata using ffprobe.
    
    Args:
        video_path: Path to video file
    
    Returns:
        Dictionary with metadata fields
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(video_path)
            ],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            logger.error(f"ffprobe failed for {video_path}: {result.stderr}")
            return {}
        
        data = json.loads(result.stdout)
        
        # Extract metadata
        metadata = {
            "duration_seconds": None,
            "file_size_kb": None,
            "video_codec": None,
            "audio_codec": None,
            "resolution": None,
            "frame_rate": None,
        }
        
        # Duration and file size from format
        if "format" in data:
            format_data = data["format"]
            if "duration" in format_data:
                metadata["duration_seconds"] = float(format_data["duration"])
            if "size" in format_data:
                metadata["file_size_kb"] = int(format_data["size"]) // 1024
        
        # Video and audio codec from streams
        if "streams" in data:
            for stream in data["streams"]:
                if stream.get("codec_type") == "video":
                    metadata["video_codec"] = stream.get("codec_name")
                    
                    # Resolution
                    width = stream.get("width")
                    height = stream.get("height")
                    if width and height:
                        metadata["resolution"] = f"{width}x{height}"
                    
                    # Frame rate
                    r_frame_rate = stream.get("r_frame_rate")
                    if r_frame_rate:
                        try:
                            num, den = r_frame_rate.split("/")
                            metadata["frame_rate"] = float(num) / float(den)
                        except (ValueError, ZeroDivisionError):
                            pass
                
                elif stream.get("codec_type") == "audio":
                    metadata["audio_codec"] = stream.get("codec_name")
        
        return metadata
    
    except subprocess.TimeoutExpired:
        logger.error(f"ffprobe timeout for {video_path}")
        return {}
    except Exception as e:
        logger.error(f"Error extracting metadata for {video_path}: {e}")
        return {}


def load_url_registry() -> Dict[str, Any]:
    """
    Load the URL registry to recover source_url for videos.
    
    Returns:
        Dictionary mapping video_id to registry entry
    """
    settings = get_settings()
    registry_path = Path(settings.url_registry_file)
    
    if not registry_path.exists():
        logger.warning(f"URL registry not found: {registry_path}")
        return {}
    
    try:
        with open(registry_path, 'r') as f:
            data = json.load(f)
            # Convert to dict keyed by video_id for easy lookup
            return {entry["video_id"]: entry for entry in data.get("entries", [])}
    except Exception as e:
        logger.error(f"Error loading URL registry: {e}")
        return {}


async def migrate_single_video(
    video_path: Path,
    uploader: GCSUploader,
    url_registry: Dict[str, Any]
) -> bool:
    """
    Migrate a single video to GCS and database.
    
    Args:
        video_path: Path to video file
        uploader: GCS uploader instance
        url_registry: URL registry data
    
    Returns:
        True if successful, False otherwise
    """
    identifier = video_path.stem
    
    # Get async session
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            # Check if already in database
            existing = await video_db_repository.get_by_identifier(session, identifier)
            if existing:
                logger.info(f"⏭️  Skipping {identifier} (already in database)")
                return True
            
            logger.info(f"📹 Migrating {identifier} ({video_path.name})")
            
            # Extract metadata
            logger.info(f"  ⚙️  Extracting metadata via ffprobe...")
            metadata = extract_video_metadata(video_path)
            
            # Log metadata
            if metadata.get("duration_seconds"):
                logger.info(f"  ⏱️  Duration: {metadata['duration_seconds']:.1f}s")
            if metadata.get("video_codec"):
                logger.info(f"  🎬 Video: {metadata['video_codec']} @ {metadata.get('resolution', 'unknown')}")
            if metadata.get("audio_codec"):
                logger.info(f"  🔊 Audio: {metadata['audio_codec']}")
            if metadata.get("file_size_kb"):
                file_size_mb = metadata["file_size_kb"] / 1024
                logger.info(f"  💾 Size: {file_size_mb:.1f} MB")
            
            # Upload to GCS
            logger.info(f"  ☁️  Uploading to GCS...")
            gcs_path, signed_url = await uploader.upload_video(
                video_path,
                identifier
            )
            logger.info(f"  ✅ Uploaded: gs://{uploader.bucket_name}/{gcs_path}")
            
            # Get source URL from registry if available
            source_url = None
            if identifier in url_registry:
                source_url = url_registry[identifier].get("url")
                if source_url:
                    logger.info(f"  🔗 Source URL: {source_url}")
            
            # Generate title from identifier
            title = identifier.replace("-", " ").replace("_", " ").title()
            
            # Insert into database
            cloud_url = f"gs://{uploader.bucket_name}/{gcs_path}"
            video = await video_db_repository.create(
                session,
                identifier=identifier,
                cloud_url=cloud_url,
                source_url=source_url,
                title=title,
                **metadata
            )
            await session.commit()
            
            logger.info(f"  💾 Inserted into database (id={video.id})")
            logger.info(f"✅ Successfully migrated {identifier}\n")
            
            return True
        
        except Exception as e:
            await session.rollback()
            logger.error(f"❌ Failed to migrate {identifier}: {e}\n")
            return False


async def main():
    """Main migration function."""
    logger.info("=" * 80)
    logger.info("Video Migration to Cloud + Database")
    logger.info("=" * 80)
    logger.info("")
    
    # Initialize database
    logger.info("🔌 Initializing database connection...")
    await init_db()
    logger.info("✅ Database connected\n")
    
    # Initialize GCS uploader
    logger.info("☁️  Initializing GCS uploader...")
    uploader = GCSUploader()
    logger.info("✅ GCS uploader ready\n")
    
    # Load URL registry
    logger.info("📋 Loading URL registry...")
    url_registry = load_url_registry()
    logger.info(f"✅ Loaded {len(url_registry)} entries from URL registry\n")
    
    # Get all video files
    logger.info("📂 Scanning videos directory...")
    videos_dir = get_videos_directory()
    video_files = list(videos_dir.glob("*.mp4"))
    logger.info(f"✅ Found {len(video_files)} video files\n")
    
    if not video_files:
        logger.warning("⚠️  No video files found. Exiting.")
        await close_db()
        return
    
    # Migrate each video
    logger.info("🚀 Starting migration...\n")
    total = len(video_files)
    migrated = 0
    skipped = 0
    failed = 0
    
    for idx, video_path in enumerate(video_files, start=1):
        logger.info(f"[{idx}/{total}] Processing {video_path.name}...")
        
        # Check if already in database first (quick check)
        identifier = video_path.stem
        session_factory = get_session_factory()
        async with session_factory() as session:
            existing = await video_db_repository.get_by_identifier(session, identifier)
            if existing:
                logger.info(f"⏭️  Skipping (already in database)\n")
                skipped += 1
                continue
        
        success = await migrate_single_video(video_path, uploader, url_registry)
        
        if success:
            migrated += 1
        else:
            failed += 1
    
    # Print summary
    logger.info("=" * 80)
    logger.info("Migration Summary")
    logger.info("=" * 80)
    logger.info(f"Total videos:     {total}")
    logger.info(f"✅ Migrated:       {migrated}")
    logger.info(f"⏭️  Skipped:        {skipped} (already in database)")
    logger.info(f"❌ Failed:         {failed}")
    logger.info("=" * 80)
    
    # Close database
    await close_db()
    
    if failed > 0:
        logger.warning("⚠️  Some migrations failed. Check the logs above.")
        sys.exit(1)
    else:
        logger.info("🎉 Migration completed successfully!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
