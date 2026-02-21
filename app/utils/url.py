"""
URL utility functions for generating video identifiers from URLs.

These functions were extracted from the URLRegistry class (removed in Phase 10).
They are pure utilities with no dependency on any persistence layer.
"""
import hashlib
import re
from pathlib import Path
from typing import List
from urllib.parse import urlparse, unquote


def is_generic_filename(filename: str, generic_names: List[str]) -> bool:
    """
    Check if a filename is too generic to use as a video identifier.

    Generic filenames (e.g. "video", "output") would cause collisions when
    multiple videos share the same filename but are different content. When a
    filename is generic, the caller should fall back to a hash-based identifier.

    Args:
        filename: Filename stem to check (without extension, e.g. "video")
        generic_names: List of names considered generic (from config)

    Returns:
        True if the filename is generic or a single character, False otherwise
    """
    filename_lower = filename.lower()

    # Single character names are always generic
    if len(filename_lower) <= 1:
        return True

    for generic in generic_names:
        if filename_lower == generic.lower():
            return True

    return False


def generate_video_id_from_url(url: str, generic_names: List[str]) -> str:
    """
    Derive a clean, filesystem-safe video identifier from a URL.

    For non-generic filenames, sanitizes the filename stem (lowercase,
    non-alphanumeric chars replaced with hyphens, max 50 chars).

    For generic filenames (e.g. "video.mp4", "output.mp4"), falls back to a
    hash-based identifier in the form ``"video-<8 hex chars>"``.

    Args:
        url: Full video URL (e.g. "https://cdn.example.com/uploads/speech.mp4")
        generic_names: List of names considered generic (from config)

    Returns:
        A stable, URL-derived video identifier string

    Examples:
        >>> generate_video_id_from_url("https://cdn.example.com/speech.mp4", [])
        'speech'
        >>> generate_video_id_from_url("https://cdn.example.com/video.mp4", ["video"])
        'video-a1b2c3d4'
    """
    parsed = urlparse(url)

    # Extract filename stem from URL path, decoding percent-encoding first
    # so that "My%20Video.mp4" becomes "My Video" before sanitization
    filename = unquote(Path(parsed.path).stem)

    if not filename or is_generic_filename(filename, generic_names):
        # Hash the normalized URL (lowercase, no trailing slash) for a stable ID
        normalized = f"{parsed.scheme}://{parsed.netloc}{unquote(parsed.path)}".lower()
        url_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"video-{url_hash[:8]}"

    # Sanitize: lowercase, collapse non-alphanumeric runs to a single hyphen
    sanitized = filename.lower()
    sanitized = re.sub(r"[^a-z0-9]+", "-", sanitized)
    sanitized = sanitized.strip("-")

    # Guard against an empty result after stripping (e.g. filename was "---")
    if not sanitized:
        normalized = f"{parsed.scheme}://{parsed.netloc}{unquote(parsed.path)}".lower()
        url_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"video-{url_hash[:8]}"

    # Limit to 50 characters, avoid trailing hyphen after truncation
    if len(sanitized) > 50:
        sanitized = sanitized[:50].rstrip("-")

    return sanitized
