"""
Temp File Manager -- centralized management of temporary processing files.

Every pipeline stage that writes local files (video download, audio extraction,
clip extraction, thumbnail generation) uses this module to get paths and
directories under a single, structured temp tree:

    temp/
    ├── videos/{identifier}/{identifier}.mp4
    ├── audio/{identifier}/{identifier}.wav
    ├── clips/{identifier}/{identifier}_{moment_id}_clip.mp4
    └── thumbnails/{identifier}/{identifier}.jpg

Files are cleaned up automatically by the background scheduler (every 6 hours
by default, deleting files older than 24 hours). They can also be cleaned up
immediately when a video is deleted via cleanup_video().
"""
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Valid purpose directories within the temp tree
VALID_PURPOSES = {"videos", "audio", "clips", "thumbnails"}


def _get_temp_base() -> Path:
    """
    Return the absolute path to the temp base directory.

    Resolves temp_base_dir from settings relative to the backend root
    (the directory containing the `app/` package).
    """
    from app.core.config import get_settings
    settings = get_settings()
    backend_root = Path(__file__).parent.parent.parent
    base = backend_root / settings.temp_base_dir
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_temp_dir(purpose: str, identifier: str) -> Path:
    """
    Return (and create) the temp subdirectory for a specific purpose and video.

    Args:
        purpose: One of "videos", "audio", "clips", "thumbnails"
        identifier: Video identifier (e.g. "motivation")

    Returns:
        Absolute Path to temp/{purpose}/{identifier}/, created if not present.
    """
    if purpose not in VALID_PURPOSES:
        raise ValueError(f"Invalid temp purpose '{purpose}'. Must be one of: {VALID_PURPOSES}")

    target = _get_temp_base() / purpose / identifier
    target.mkdir(parents=True, exist_ok=True)
    return target


def get_temp_file_path(purpose: str, identifier: str, filename: str) -> Path:
    """
    Return the full path for a temp file, creating parent directories.

    Args:
        purpose: One of "videos", "audio", "clips", "thumbnails"
        identifier: Video identifier (e.g. "motivation")
        filename: The file name (e.g. "motivation.wav")

    Returns:
        Absolute Path to temp/{purpose}/{identifier}/{filename}
    """
    return get_temp_dir(purpose, identifier) / filename


async def cleanup_old_files(max_age_hours: float = 24.0) -> dict:
    """
    Delete all files in the temp tree that are older than max_age_hours.

    Uses file mtime (modification time) as the age indicator. After deleting
    files, performs a second bottom-up pass to remove empty directories.

    Args:
        max_age_hours: Files older than this many hours are deleted.

    Returns:
        Dict with keys: files_deleted, bytes_freed, dirs_removed, duration_ms
    """
    start = time.monotonic()
    base = _get_temp_base()

    files_deleted = 0
    bytes_freed = 0
    dirs_removed = 0

    if not base.exists():
        return {
            "files_deleted": 0,
            "bytes_freed": 0,
            "dirs_removed": 0,
            "duration_ms": 0,
        }

    cutoff = time.time() - (max_age_hours * 3600)

    # First pass: delete old files
    for file_path in base.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            stat = file_path.stat()
            if stat.st_mtime < cutoff:
                size = stat.st_size
                file_path.unlink()
                files_deleted += 1
                bytes_freed += size
                logger.debug(f"Deleted old temp file: {file_path} (age={(time.time() - stat.st_mtime) / 3600:.1f}h)")
        except FileNotFoundError:
            pass  # Already deleted by a concurrent cleanup
        except Exception as e:
            logger.warning(f"Could not delete temp file {file_path}: {e}")

    # Second pass: remove empty directories (bottom-up)
    for dir_path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not dir_path.is_dir():
            continue
        if dir_path == base:
            continue
        try:
            dir_path.rmdir()  # Only succeeds if empty
            dirs_removed += 1
            logger.debug(f"Removed empty temp dir: {dir_path}")
        except OSError:
            pass  # Directory not empty -- expected

    duration_ms = int((time.monotonic() - start) * 1000)

    logger.info(
        f"Temp cleanup complete: deleted {files_deleted} files, "
        f"freed {bytes_freed / (1024 ** 3):.2f} GB, "
        f"removed {dirs_removed} dirs in {duration_ms}ms"
    )

    return {
        "files_deleted": files_deleted,
        "bytes_freed": bytes_freed,
        "dirs_removed": dirs_removed,
        "duration_ms": duration_ms,
    }


async def cleanup_all() -> dict:
    """
    Delete ALL files in the temp directory regardless of age.

    Use only in emergency situations (e.g. disk critically low). This will
    delete files belonging to active pipelines.

    Returns:
        Dict with keys: files_deleted, bytes_freed, dirs_removed, duration_ms
    """
    start = time.monotonic()
    base = _get_temp_base()

    files_deleted = 0
    bytes_freed = 0
    dirs_removed = 0

    if not base.exists():
        return {
            "files_deleted": 0,
            "bytes_freed": 0,
            "dirs_removed": 0,
            "duration_ms": 0,
        }

    # Delete all files
    for file_path in base.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            size = file_path.stat().st_size
            file_path.unlink()
            files_deleted += 1
            bytes_freed += size
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Could not delete temp file {file_path}: {e}")

    # Remove empty directories bottom-up
    for dir_path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not dir_path.is_dir() or dir_path == base:
            continue
        try:
            dir_path.rmdir()
            dirs_removed += 1
        except OSError:
            pass

    duration_ms = int((time.monotonic() - start) * 1000)

    logger.warning(
        f"Emergency temp cleanup: deleted {files_deleted} files, "
        f"freed {bytes_freed / (1024 ** 3):.2f} GB in {duration_ms}ms"
    )

    return {
        "files_deleted": files_deleted,
        "bytes_freed": bytes_freed,
        "dirs_removed": dirs_removed,
        "duration_ms": duration_ms,
    }


async def cleanup_video(identifier: str) -> dict:
    """
    Delete all temp files for a specific video across all purpose directories.

    Called during video deletion to immediately reclaim disk space without
    waiting for the scheduled cleanup.

    Args:
        identifier: Video identifier (e.g. "motivation")

    Returns:
        Dict with keys: files_deleted, bytes_freed, dirs_removed
    """
    files_deleted = 0
    bytes_freed = 0
    dirs_removed = 0

    for purpose in VALID_PURPOSES:
        try:
            dir_path = _get_temp_base() / purpose / identifier
            if not dir_path.exists():
                continue

            for file_path in dir_path.iterdir():
                if file_path.is_file():
                    try:
                        size = file_path.stat().st_size
                        file_path.unlink()
                        files_deleted += 1
                        bytes_freed += size
                    except Exception as e:
                        logger.warning(f"Could not delete {file_path}: {e}")

            try:
                dir_path.rmdir()
                dirs_removed += 1
            except OSError:
                pass  # Not empty (shouldn't happen, but safe to ignore)

        except Exception as e:
            logger.warning(f"Error cleaning up temp/{purpose}/{identifier}: {e}")

    if files_deleted > 0 or dirs_removed > 0:
        logger.info(
            f"Temp cleanup for video '{identifier}': "
            f"deleted {files_deleted} files, freed {bytes_freed / (1024 ** 2):.1f} MB, "
            f"removed {dirs_removed} dirs"
        )

    return {
        "files_deleted": files_deleted,
        "bytes_freed": bytes_freed,
        "dirs_removed": dirs_removed,
    }


def _human_size(size_bytes: int) -> str:
    """Format byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


async def get_temp_stats() -> dict:
    """
    Return disk usage statistics for the temp directory, grouped by purpose.

    Returns:
        Dict with total counts/sizes and a per-purpose breakdown.
    """
    from app.core.config import get_settings
    settings = get_settings()

    base = _get_temp_base()
    now = time.time()

    total_files = 0
    total_size = 0
    oldest_mtime: Optional[float] = None

    by_purpose: dict = {}
    for purpose in VALID_PURPOSES:
        by_purpose[purpose] = {"files": 0, "size_bytes": 0, "size_human": "0 B"}

    if not base.exists():
        return {
            "total_files": 0,
            "total_size_bytes": 0,
            "total_size_human": "0 B",
            "by_purpose": by_purpose,
            "oldest_file_age_hours": None,
            "cleanup_threshold_hours": settings.temp_max_age_hours,
        }

    for file_path in base.rglob("*"):
        if not file_path.is_file():
            continue

        try:
            stat = file_path.stat()
        except FileNotFoundError:
            continue

        size = stat.st_size
        total_files += 1
        total_size += size

        if oldest_mtime is None or stat.st_mtime < oldest_mtime:
            oldest_mtime = stat.st_mtime

        # Determine which purpose directory this file belongs to
        try:
            relative = file_path.relative_to(base)
            purpose_part = relative.parts[0] if relative.parts else None
            if purpose_part in VALID_PURPOSES:
                by_purpose[purpose_part]["files"] += 1
                by_purpose[purpose_part]["size_bytes"] += size
        except ValueError:
            pass

    # Format human sizes
    for purpose in VALID_PURPOSES:
        by_purpose[purpose]["size_human"] = _human_size(by_purpose[purpose]["size_bytes"])

    oldest_age_hours = (now - oldest_mtime) / 3600 if oldest_mtime is not None else None

    return {
        "total_files": total_files,
        "total_size_bytes": total_size,
        "total_size_human": _human_size(total_size),
        "by_purpose": by_purpose,
        "oldest_file_age_hours": round(oldest_age_hours, 2) if oldest_age_hours is not None else None,
        "cleanup_threshold_hours": settings.temp_max_age_hours,
    }
