"""
URL Registry Service for managing URL-to-video_id mappings.
Handles duplicate detection and video ID generation from URLs.
"""
import json
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, List
from urllib.parse import urlparse, unquote
from filelock import FileLock

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class RegistryEntry:
    """Represents a URL registry entry."""
    
    def __init__(
        self,
        url: str,
        url_hash: str,
        video_id: str,
        file_size: int,
        downloaded_at: str,
        force_downloaded: bool = False
    ):
        self.url = url
        self.url_hash = url_hash
        self.video_id = video_id
        self.file_size = file_size
        self.downloaded_at = downloaded_at
        self.force_downloaded = force_downloaded
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "url": self.url,
            "url_hash": self.url_hash,
            "video_id": self.video_id,
            "file_size": self.file_size,
            "downloaded_at": self.downloaded_at,
            "force_downloaded": self.force_downloaded
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'RegistryEntry':
        """Create entry from dictionary."""
        return cls(
            url=data["url"],
            url_hash=data["url_hash"],
            video_id=data["video_id"],
            file_size=data["file_size"],
            downloaded_at=data["downloaded_at"],
            force_downloaded=data.get("force_downloaded", False)
        )


class URLRegistry:
    """Manages URL-to-video_id mappings with persistent storage."""
    
    def __init__(self):
        """Initialize URL registry."""
        settings = get_settings()
        
        # Get backend root directory
        backend_dir = Path(__file__).parent.parent.parent
        self.registry_file = backend_dir / settings.url_registry_file
        self.lock_file = self.registry_file.with_suffix('.lock')
        self.generic_names = settings.video_download_generic_names
        
        # Ensure parent directory exists
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize registry structure
        self.entries: List[RegistryEntry] = []
        self.url_to_latest_video_id: Dict[str, str] = {}
        
        # Load existing registry
        self._load()
    
    def _load(self) -> None:
        """Load registry from file (thread-safe)."""
        if not self.registry_file.exists():
            logger.info(f"Registry file not found, creating new: {self.registry_file}")
            self._save()
            return
        
        try:
            with FileLock(str(self.lock_file), timeout=10):
                with open(self.registry_file, 'r') as f:
                    data = json.load(f)
                    
                    # Load entries
                    self.entries = [
                        RegistryEntry.from_dict(entry_data)
                        for entry_data in data.get("entries", [])
                    ]
                    
                    # Load URL hash to latest video_id mapping
                    self.url_to_latest_video_id = data.get("url_to_latest_video_id", {})
                    
                    logger.info(f"Loaded {len(self.entries)} entries from registry")
        except Exception as e:
            logger.error(f"Failed to load registry: {e}")
            # Start with empty registry
            self.entries = []
            self.url_to_latest_video_id = {}
    
    def _save(self) -> None:
        """Save registry to file (thread-safe)."""
        try:
            with FileLock(str(self.lock_file), timeout=10):
                data = {
                    "entries": [entry.to_dict() for entry in self.entries],
                    "url_to_latest_video_id": self.url_to_latest_video_id
                }
                
                # Write atomically
                temp_file = self.registry_file.with_suffix('.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(data, f, indent=2)
                
                # Atomic rename
                temp_file.replace(self.registry_file)
                
                logger.debug(f"Saved registry with {len(self.entries)} entries")
        except Exception as e:
            logger.error(f"Failed to save registry: {e}")
    
    def normalize_url(self, url: str) -> str:
        """
        Normalize URL for consistent hashing.
        
        Args:
            url: URL to normalize
        
        Returns:
            Normalized URL string
        """
        # Parse URL
        parsed = urlparse(url)
        
        # Decode percent-encoded characters
        path = unquote(parsed.path)
        
        # Remove common variable query parameters
        # Keep content-identifying parameters like v, id, video_id, file
        query_params_to_remove = [
            'token', 'auth', 'key', 'signature', 'expires',
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
            'ref', 'source', 'fbclid', 'gclid'
        ]
        
        # For simplicity, we'll remove all query params except GCS signed URL params
        # which are needed for identification
        query = ""
        if parsed.query and "X-Goog-" in parsed.query:
            # Keep GCS signed URL params for identification
            query = f"?{parsed.query}"
        
        # Reconstruct normalized URL
        normalized = f"{parsed.scheme}://{parsed.netloc}{path}{query}"
        
        # Convert to lowercase
        normalized = normalized.lower()
        
        return normalized
    
    def compute_url_hash(self, url: str) -> str:
        """
        Compute SHA256 hash of normalized URL.
        
        Args:
            url: URL to hash
        
        Returns:
            SHA256 hash as hex string
        """
        normalized = self.normalize_url(url)
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    
    def lookup_by_url(self, url: str) -> Optional[RegistryEntry]:
        """
        Look up the latest registry entry for a URL.
        
        Args:
            url: URL to look up
        
        Returns:
            Latest RegistryEntry if found, None otherwise
        """
        url_hash = self.compute_url_hash(url)
        
        # Get latest video_id for this URL hash
        latest_video_id = self.url_to_latest_video_id.get(url_hash)
        
        if not latest_video_id:
            return None
        
        # Find the entry with this video_id
        for entry in reversed(self.entries):  # Search from newest to oldest
            if entry.url_hash == url_hash and entry.video_id == latest_video_id:
                return entry
        
        return None
    
    def register(
        self,
        url: str,
        video_id: str,
        file_size: int,
        force_downloaded: bool = False
    ) -> None:
        """
        Register a URL-to-video_id mapping.
        
        Args:
            url: Original URL
            video_id: Generated/assigned video_id
            file_size: Size of downloaded file in bytes
            force_downloaded: Whether this was a forced re-download
        """
        url_hash = self.compute_url_hash(url)
        downloaded_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        
        # Create new entry
        entry = RegistryEntry(
            url=url,
            url_hash=url_hash,
            video_id=video_id,
            file_size=file_size,
            downloaded_at=downloaded_at,
            force_downloaded=force_downloaded
        )
        
        # Add to entries list
        self.entries.append(entry)
        
        # Update latest mapping
        self.url_to_latest_video_id[url_hash] = video_id
        
        # Save to disk
        self._save()
        
        logger.info(f"Registered URL mapping: {url_hash[:8]}... -> {video_id}")
    
    def is_generic_filename(self, filename: str) -> bool:
        """
        Check if filename is generic (triggers hash-based ID).
        
        Args:
            filename: Filename to check (without extension)
        
        Returns:
            True if generic, False otherwise
        """
        filename_lower = filename.lower()
        
        # Single character names
        if len(filename_lower) <= 1:
            return True
        
        # Check against generic names list
        for generic in self.generic_names:
            if filename_lower == generic.lower():
                return True
        
        return False
    
    def generate_video_id_from_url(self, url: str) -> str:
        """
        Generate a clean video_id from URL.
        
        Args:
            url: URL to extract ID from
        
        Returns:
            Generated video_id
        """
        parsed = urlparse(url)
        
        # Extract filename from path
        path = parsed.path
        filename = Path(path).stem  # Gets filename without extension
        
        # If no meaningful filename or it's generic, use hash-based ID
        if not filename or self.is_generic_filename(filename):
            url_hash = self.compute_url_hash(url)
            return f"video-{url_hash[:8]}"
        
        # Sanitize filename: lowercase, replace spaces/special chars with hyphens
        sanitized = filename.lower()
        sanitized = re.sub(r'[^a-z0-9]+', '-', sanitized)
        sanitized = sanitized.strip('-')
        
        # Limit length
        if len(sanitized) > 50:
            sanitized = sanitized[:50].rstrip('-')
        
        return sanitized
    
    def get_video_id_for_url(
        self,
        url: str,
        force_download: bool = False,
        local_file_size: Optional[int] = None
    ) -> Tuple[str, bool]:
        """
        Get video_id for a URL, handling caching and collisions.
        
        Args:
            url: Video URL
            force_download: Force re-download even if cached
            local_file_size: Size of existing local file (for collision detection)
        
        Returns:
            Tuple of (video_id, needs_download)
        """
        # Check registry for existing mapping
        existing_entry = self.lookup_by_url(url)
        
        if existing_entry and not force_download:
            # URL already downloaded, use cached video_id
            logger.info(f"URL found in registry: {url} -> {existing_entry.video_id}")
            return (existing_entry.video_id, False)
        
        # Generate new video_id
        base_video_id = self.generate_video_id_from_url(url)
        
        if force_download and existing_entry:
            # Force download requested - create new ID with timestamp
            timestamp = int(time.time())
            video_id = f"{base_video_id}-{timestamp}"
            logger.info(f"Force download: generated new video_id {video_id}")
            return (video_id, True)
        
        # Check for local file collision
        if local_file_size is not None:
            # File exists locally with this name
            # This shouldn't happen normally, but handle it
            timestamp = int(time.time())
            video_id = f"{base_video_id}-{timestamp}"
            logger.warning(f"Local file collision detected, using timestamped ID: {video_id}")
            return (video_id, True)
        
        # New URL, use base ID
        return (base_video_id, True)
    
    def unregister(self, video_id: str) -> bool:
        """
        Remove registry entry by video_id.
        
        Args:
            video_id: Video identifier to remove
        
        Returns:
            True if entry was found and removed, False otherwise
        """
        found = False
        
        # Find and remove entries with this video_id
        entries_to_remove = []
        url_hashes_to_update = []
        
        for entry in self.entries:
            if entry.video_id == video_id:
                entries_to_remove.append(entry)
                url_hashes_to_update.append(entry.url_hash)
                found = True
        
        # Remove entries
        for entry in entries_to_remove:
            self.entries.remove(entry)
        
        # Update url_to_latest_video_id mapping
        for url_hash in url_hashes_to_update:
            if url_hash in self.url_to_latest_video_id:
                if self.url_to_latest_video_id[url_hash] == video_id:
                    # Find if there's an older entry for this URL hash
                    older_entry = None
                    for entry in reversed(self.entries):
                        if entry.url_hash == url_hash:
                            older_entry = entry
                            break
                    
                    if older_entry:
                        # Revert to older entry
                        self.url_to_latest_video_id[url_hash] = older_entry.video_id
                    else:
                        # No other entry, remove from mapping
                        del self.url_to_latest_video_id[url_hash]
        
        # Save changes
        if found:
            self._save()
            logger.info(f"Unregistered video_id: {video_id}")
        else:
            logger.debug(f"Video_id not found in registry: {video_id}")
        
        return found


