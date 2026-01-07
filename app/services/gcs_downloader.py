"""
GCS Downloader Service for downloading videos from URLs.
Supports HTTP/HTTPS public URLs and GCS URIs (gs://).
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional, Callable
from urllib.parse import urlparse
import aiofiles
import requests
from google.cloud import storage
from google.oauth2 import service_account
import google.auth

from app.core.config import get_settings
from app.core.redis import get_redis_client
from app.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class GCSDownloader:
    """Downloads videos from various sources with progress tracking."""
    
    SUPPORTED_EXTENSIONS = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.ogg'}
    
    def __init__(self):
        """Initialize downloader with settings from config."""
        settings = get_settings()
        self.timeout = settings.video_download_timeout_seconds
        self.max_size = settings.video_download_max_size_bytes
        self.chunk_size = settings.video_download_chunk_size
        self.max_retries = settings.video_download_retry_count
        self.retry_base_delay = settings.video_download_retry_base_delay
        self.max_concurrent = settings.video_download_max_concurrent
        
        # Initialize GCS client (same pattern as GCSUploader)
        self.credentials = None
        credentials_path = settings.gcs_credentials_path
        
        if credentials_path and credentials_path.exists():
            logger.info(f"Using GCS service account from: {credentials_path}")
            try:
                self.credentials = service_account.Credentials.from_service_account_file(
                    str(credentials_path),
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                self.client = storage.Client(
                    credentials=self.credentials,
                    project=self.credentials.project_id
                )
                logger.info(f"GCS client initialized with service account (project: {self.credentials.project_id})")
            except Exception as e:
                logger.error(f"Failed to load service account credentials: {e}")
                logger.info("Falling back to Application Default Credentials")
                self.credentials = None
                self.client = self._init_with_adc()
        else:
            if credentials_path:
                logger.warning(f"Service account file not found: {credentials_path}")
            logger.warning("No service account configured, using Application Default Credentials")
            self.client = self._init_with_adc()
    
    def _init_with_adc(self) -> storage.Client:
        """Initialize client with Application Default Credentials (fallback)."""
        try:
            client = storage.Client()
            logger.info("GCS client initialized with Application Default Credentials")
            return client
        except Exception as e:
            if "Project" in str(e) or "project" in str(e):
                logger.warning("Could not auto-detect project, using bucket-specific access")
                credentials, _ = google.auth.default()
                return storage.Client(credentials=credentials, project=None)
            else:
                raise
    
    def detect_url_type(self, url: str) -> str:
        """
        Detect URL type.
        
        Args:
            url: URL to analyze
        
        Returns:
            "public" for HTTP/HTTPS, "gcs_uri" for gs://, "gcs_signed" for signed GCS URLs
        """
        parsed = urlparse(url)
        
        if parsed.scheme == 'gs':
            return "gcs_uri"
        elif parsed.scheme in ('http', 'https'):
            # Check if it's a GCS signed URL
            if 'storage.googleapis.com' in parsed.netloc or 'storage.cloud.google.com' in parsed.netloc:
                return "gcs_signed"
            return "public"
        else:
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    
    def validate_video_format(self, url: str) -> bool:
        """
        Validate that URL points to a supported video format.
        
        Args:
            url: URL to validate
        
        Returns:
            True if valid, False otherwise
        """
        parsed = urlparse(url)
        path = Path(parsed.path)
        extension = path.suffix.lower()
        
        return extension in self.SUPPORTED_EXTENSIONS
    
    async def get_content_length(self, url: str) -> Optional[int]:
        """
        Get content length via HEAD request.
        
        Args:
            url: URL to check
        
        Returns:
            Content length in bytes, or None if unavailable
        """
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.head(url, timeout=30, allow_redirects=True)
            )
            
            content_length = response.headers.get('Content-Length')
            if content_length:
                return int(content_length)
        except Exception as e:
            logger.warning(f"Failed to get content length: {e}")
        
        return None
    
    async def _acquire_download_slot(self, video_id: str) -> None:
        """
        Acquire a download slot using Redis semaphore.
        
        Args:
            video_id: Video identifier for logging
        
        Raises:
            TimeoutError: If unable to acquire slot within timeout
        """
        redis = get_redis_client()
        semaphore_key = "download:active_count"
        
        max_wait_seconds = 300  # 5 minutes
        check_interval = 5  # 5 seconds
        elapsed = 0
        
        while elapsed < max_wait_seconds:
            # Try to increment counter
            current_count = redis.incr(semaphore_key)
            
            if current_count <= self.max_concurrent:
                # Got a slot
                logger.info(f"Acquired download slot for {video_id} ({current_count}/{self.max_concurrent})")
                return
            else:
                # Too many downloads, decrement and wait
                redis.decr(semaphore_key)
                logger.info(
                    f"Download queue full ({self.max_concurrent} active), waiting for slot... "
                    f"({elapsed}/{max_wait_seconds}s)"
                )
                await asyncio.sleep(check_interval)
                elapsed += check_interval
        
        raise TimeoutError(
            f"Download queue full, could not acquire slot within {max_wait_seconds}s"
        )
    
    def _release_download_slot(self, video_id: str) -> None:
        """
        Release download slot.
        
        Args:
            video_id: Video identifier for logging
        """
        try:
            redis = get_redis_client()
            semaphore_key = "download:active_count"
            current_count = redis.decr(semaphore_key)
            
            # Ensure it doesn't go negative
            if current_count < 0:
                redis.set(semaphore_key, 0)
                current_count = 0
            
            logger.info(f"Released download slot for {video_id} ({current_count} active)")
        except Exception as e:
            logger.error(f"Failed to release download slot: {e}")
    
    async def download_from_public_url(
        self,
        url: str,
        dest_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        Download video from HTTP/HTTPS URL.
        
        Args:
            url: Public URL to download from
            dest_path: Destination file path
            progress_callback: Optional callback(bytes_downloaded, total_bytes)
        
        Returns:
            True if successful, False otherwise
        
        Raises:
            Exception: If download fails after retries
        """
        logger.info(f"Starting download from public URL: {url}")
        
        # Get content length
        content_length = await self.get_content_length(url)
        
        if content_length:
            # Check size limit
            if content_length > self.max_size:
                raise ValueError(
                    f"File size ({content_length} bytes) exceeds limit "
                    f"({self.max_size} bytes = {self.max_size / (1024**3):.2f} GB)"
                )
            logger.info(f"Content length: {content_length / (1024**2):.2f} MB")
        else:
            logger.warning("Content length not available, proceeding without size check")
        
        # Download with streaming
        bytes_downloaded = 0
        start_time = time.time()
        
        async def _do_download():
            nonlocal bytes_downloaded
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, stream=True, timeout=self.timeout)
            )
            response.raise_for_status()
            
            # Open file for writing
            async with aiofiles.open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if chunk:
                        await f.write(chunk)
                        bytes_downloaded += len(chunk)
                        
                        # Check size limit during download
                        if bytes_downloaded > self.max_size:
                            raise ValueError(
                                f"Download exceeded size limit ({self.max_size} bytes)"
                            )
                        
                        # Progress callback
                        if progress_callback and content_length:
                            progress_callback(bytes_downloaded, content_length)
        
        # Download with retry
        await retry_with_backoff(
            _do_download,
            max_retries=self.max_retries,
            base_delay=self.retry_base_delay,
            operation_name=f"Download from {url}"
        )
        
        duration = time.time() - start_time
        logger.info(
            f"Download completed: {bytes_downloaded / (1024**2):.2f} MB "
            f"in {duration:.2f}s ({bytes_downloaded / duration / (1024**2):.2f} MB/s)"
        )
        
        return True
    
    async def download_from_gcs_uri(
        self,
        gcs_uri: str,
        dest_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        Download video from GCS URI (gs://bucket/path).
        
        Args:
            gcs_uri: GCS URI (e.g., gs://bucket/path/video.mp4)
            dest_path: Destination file path
            progress_callback: Optional callback(bytes_downloaded, total_bytes)
        
        Returns:
            True if successful, False otherwise
        
        Raises:
            Exception: If download fails
        """
        logger.info(f"Starting download from GCS URI: {gcs_uri}")
        
        # Parse GCS URI
        parsed = urlparse(gcs_uri)
        if parsed.scheme != 'gs':
            raise ValueError(f"Invalid GCS URI scheme: {parsed.scheme}")
        
        bucket_name = parsed.netloc
        blob_path = parsed.path.lstrip('/')
        
        logger.info(f"GCS bucket: {bucket_name}, blob: {blob_path}")
        
        # Get blob
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        
        # Check if blob exists and get size
        if not blob.exists():
            raise FileNotFoundError(f"GCS blob not found: {gcs_uri}")
        
        blob.reload()  # Get metadata
        blob_size = blob.size
        
        # Check size limit
        if blob_size > self.max_size:
            raise ValueError(
                f"File size ({blob_size} bytes) exceeds limit "
                f"({self.max_size} bytes = {self.max_size / (1024**3):.2f} GB)"
            )
        
        logger.info(f"Blob size: {blob_size / (1024**2):.2f} MB")
        
        # Download blob
        bytes_downloaded = 0
        start_time = time.time()
        
        async def _do_download():
            nonlocal bytes_downloaded
            
            # Download to file
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                blob.download_to_filename,
                str(dest_path)
            )
            
            bytes_downloaded = blob_size
            
            # Final progress callback
            if progress_callback:
                progress_callback(bytes_downloaded, blob_size)
        
        # Download with retry
        await retry_with_backoff(
            _do_download,
            max_retries=self.max_retries,
            base_delay=self.retry_base_delay,
            operation_name=f"Download from GCS {gcs_uri}"
        )
        
        duration = time.time() - start_time
        logger.info(
            f"GCS download completed: {bytes_downloaded / (1024**2):.2f} MB "
            f"in {duration:.2f}s"
        )
        
        return True
    
    async def download(
        self,
        url: str,
        dest_path: Path,
        video_id: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        Download video from URL (auto-detects type).
        
        Args:
            url: URL to download from
            dest_path: Destination file path
            video_id: Video identifier for slot management
            progress_callback: Optional callback(bytes_downloaded, total_bytes)
        
        Returns:
            True if successful
        
        Raises:
            Exception: If download fails
        """
        # Validate URL format
        if not self.validate_video_format(url):
            raise ValueError(f"Unsupported video format in URL: {url}")
        
        # Detect URL type
        url_type = self.detect_url_type(url)
        logger.info(f"Detected URL type: {url_type}")
        
        # Acquire download slot
        await self._acquire_download_slot(video_id)
        
        try:
            # Download based on type
            if url_type == "gcs_uri":
                result = await self.download_from_gcs_uri(url, dest_path, progress_callback)
            else:
                # Both "public" and "gcs_signed" use HTTP download
                result = await self.download_from_public_url(url, dest_path, progress_callback)
            
            return result
        
        finally:
            # Always release slot
            self._release_download_slot(video_id)


