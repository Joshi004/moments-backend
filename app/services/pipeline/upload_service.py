"""
GCS-based file uploader for audio files and video clips.
Uploads files to Google Cloud Storage and generates signed URLs for remote access.
"""
import asyncio
import logging
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Callable
from datetime import timedelta
import google.auth
from google.cloud import storage
from google.oauth2 import service_account
from app.core.config import get_settings
from app.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)


class ProgressFileWrapper:
    """File wrapper that reports read progress via callback."""
    
    def __init__(self, file_obj, total_size, progress_callback):
        self._file = file_obj
        self._total_size = total_size
        self._callback = progress_callback
        self._bytes_read = 0
    
    def read(self, size=-1):
        data = self._file.read(size)
        self._bytes_read += len(data)
        if self._callback:
            self._callback(self._bytes_read, self._total_size)
        return data
    
    def seek(self, *args, **kwargs):
        return self._file.seek(*args, **kwargs)
    
    def tell(self):
        return self._file.tell()


# ==================== DEPRECATED: SCP Uploader (kept for reference) ====================
# class SCPUploader:
#     """SCP-based file uploader for audio and clips."""
#     
#     def __init__(self):
#         """Initialize uploader with settings from config."""
#         settings = get_settings()
#         self.remote_host = settings.scp_remote_host
#         self.audio_remote_path = settings.scp_audio_remote_path
#         self.clips_remote_path = settings.scp_clips_remote_path
#         self.timeout = settings.scp_connect_timeout
#     
#     async def upload_audio(self, local_path: Path) -> str:
#         """
#         Upload audio file to remote server for Parakeet access.
#         
#         Args:
#             local_path: Path to local audio file (e.g., static/audios/motivation.wav)
#         
#         Returns:
#             Remote path where file was uploaded
#         
#         Raises:
#             Exception: If SCP upload fails
#         """
#         if not local_path.exists():
#             raise FileNotFoundError(f"Audio file not found: {local_path}")
#         
#         remote_name = local_path.name
#         remote_dest = f"{self.remote_host}:{self.audio_remote_path}{remote_name}"
#         
#         cmd = [
#             "scp",
#             "-o", "StrictHostKeyChecking=no",
#             "-o", f"ConnectTimeout={self.timeout}",
#             str(local_path),
#             remote_dest
#         ]
#         
#         logger.info(f"Uploading audio to remote: {remote_dest}")
#         
#         process = await asyncio.create_subprocess_exec(
#             *cmd,
#             stdout=asyncio.subprocess.PIPE,
#             stderr=asyncio.subprocess.PIPE
#         )
#         stdout, stderr = await process.communicate()
#         
#         if process.returncode != 0:
#             error_msg = stderr.decode() if stderr else "Unknown error"
#             logger.error(f"SCP audio upload failed: {error_msg}")
#             raise Exception(f"SCP audio upload failed: {error_msg}")
#         
#         remote_path = f"{self.audio_remote_path}{remote_name}"
#         logger.info(f"Successfully uploaded audio to: {remote_path}")
#         return remote_path
#     
#     async def upload_clip(self, local_path: Path) -> str:
#         """
#         Upload video clip to remote server.
#         
#         Args:
#             local_path: Path to local clip file
#         
#         Returns:
#             Remote path where file was uploaded
#         
#         Raises:
#             Exception: If SCP upload fails
#         """
#         if not local_path.exists():
#             raise FileNotFoundError(f"Clip file not found: {local_path}")
#         
#         remote_name = local_path.name
#         remote_dest = f"{self.remote_host}:{self.clips_remote_path}{remote_name}"
#         
#         cmd = [
#             "scp",
#             "-o", "StrictHostKeyChecking=no",
#             "-o", f"ConnectTimeout={self.timeout}",
#             str(local_path),
#             remote_dest
#         ]
#         
#         logger.info(f"Uploading clip to remote: {remote_dest}")
#         
#         process = await asyncio.create_subprocess_exec(
#             *cmd,
#             stdout=asyncio.subprocess.PIPE,
#             stderr=asyncio.subprocess.PIPE
#         )
#         stdout, stderr = await process.communicate()
#         
#         if process.returncode != 0:
#             error_msg = stderr.decode() if stderr else "Unknown error"
#             logger.error(f"SCP clip upload failed: {error_msg}")
#             raise Exception(f"SCP clip upload failed: {error_msg}")
#         
#         remote_path = f"{self.clips_remote_path}{remote_name}"
#         logger.info(f"Successfully uploaded clip to: {remote_path}")
#         return remote_path
#     
#     async def upload_all_clips(self, video_id: str, moments: List[Dict]) -> List[Dict]:
#         """
#         Upload all clips for moments, return updated moments with remote paths.
#         
#         Args:
#             video_id: Video identifier
#             moments: List of moment dictionaries
#         
#         Returns:
#             Updated moments list with remote_clip_path field
#         """
#         from app.services.video_clipping_service import get_clip_path
#         
#         for moment in moments:
#             clip_path = get_clip_path(moment['id'], f"{video_id}.mp4")
#             if clip_path.exists():
#                 try:
#                     remote_path = await self.upload_clip(clip_path)
#                     moment['remote_clip_path'] = remote_path
#                     logger.info(f"Uploaded clip for moment {moment['id']}")
#                 except Exception as e:
#                     logger.error(f"Failed to upload clip for moment {moment['id']}: {e}")
#                     # Continue with other clips even if one fails
#             else:
#                 logger.warning(f"Clip file not found for moment {moment['id']}: {clip_path}")
#         
#         return moments
# ==================== END DEPRECATED SCP Uploader ====================


class GCSUploader:
    """GCS-based file uploader for audio files and video clips."""
    
    def __init__(self):
        """Initialize GCS uploader with settings from config."""
        settings = get_settings()
        self.bucket_name = settings.gcs_bucket_name
        self.audio_prefix = settings.gcs_audio_prefix
        self.clips_prefix = settings.gcs_clips_prefix
        self.videos_prefix = settings.gcs_videos_prefix
        self.thumbnails_prefix = settings.gcs_thumbnails_prefix
        self.expiry_hours = settings.gcs_signed_url_expiry_hours
        self.timeout = settings.gcs_upload_timeout_seconds
        self.max_retries = settings.gcs_max_retries
        self.retry_base_delay = settings.gcs_retry_base_delay
        
        # Initialize GCS client with service account or ADC
        self.credentials = None
        credentials_path = settings.gcs_credentials_path
        
        if credentials_path and credentials_path.exists():
            logger.info(f"Using GCS service account from: {credentials_path}")
            try:
                self.credentials = service_account.Credentials.from_service_account_file(
                    str(credentials_path),
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                # Create client with custom timeout configuration
                from google.cloud.storage import Client
                self.client = Client(
                    credentials=self.credentials,
                    project=self.credentials.project_id,
                    client_options={"api_endpoint": None}  # Use default endpoint
                )
                # Configure timeout on the client's session
                self.client._http.timeout = self.timeout
                logger.info(f"GCS client initialized with service account (project: {self.credentials.project_id}, timeout: {self.timeout}s)")
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
        
        self.bucket = self.client.bucket(self.bucket_name)
    
    def _init_with_adc(self) -> storage.Client:
        """Initialize client with Application Default Credentials (fallback)."""
        try:
            client = storage.Client()
            # Configure timeout on the client's session
            client._http.timeout = self.timeout
            logger.info(f"GCS client initialized with Application Default Credentials (timeout: {self.timeout}s)")
            return client
        except Exception as e:
            if "Project" in str(e) or "project" in str(e):
                logger.warning("Could not auto-detect project, using bucket-specific access")
                credentials, _ = google.auth.default()
                client = storage.Client(credentials=credentials, project=None)
                # Configure timeout
                client._http.timeout = self.timeout
                return client
            else:
                raise
    
    def _get_file_md5(self, file_path: Path) -> str:
        """Get MD5 hash of local file."""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def _get_file_size_mb(self, file_path: Path) -> float:
        """Get file size in megabytes."""
        return file_path.stat().st_size / (1024 * 1024)
    
    def _delete_by_prefix(self, prefix: str) -> int:
        """
        Delete all blobs with given prefix.
        
        Args:
            prefix: GCS prefix (e.g., "clips/video123/")
        
        Returns:
            Number of files deleted
        """
        try:
            # List all blobs with this prefix
            blobs = list(self.bucket.list_blobs(prefix=prefix))
            
            if not blobs:
                logger.debug(f"No files found with prefix: {prefix}")
                return 0
            
            deleted_count = 0
            for blob in blobs:
                try:
                    blob.delete()
                    deleted_count += 1
                    logger.debug(f"Deleted GCS blob: {blob.name}")
                except Exception as e:
                    logger.error(f"Failed to delete blob {blob.name}: {e}")
            
            logger.info(f"Deleted {deleted_count} files with prefix: {prefix}")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to list/delete blobs with prefix {prefix}: {e}")
            return 0
    
    def _upload_with_progress(
        self,
        blob,
        local_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        chunk_size: int = 5 * 1024 * 1024  # 5MB chunks
    ) -> None:
        """
        Upload file with progress tracking.
        
        Args:
            blob: GCS blob object
            local_path: Path to local file
            progress_callback: Optional callback(bytes_uploaded, total_bytes)
            chunk_size: Size of each chunk in bytes (used for resumable threshold)
        """
        file_size = local_path.stat().st_size
        
        with open(local_path, 'rb') as f:
            if progress_callback:
                # Wrap file with progress tracker
                wrapped_file = ProgressFileWrapper(f, file_size, progress_callback)
                blob.upload_from_file(
                    wrapped_file,
                    size=file_size,
                    timeout=self.timeout,
                    num_retries=0  # Retries handled by our retry wrapper
                )
            else:
                # Simple upload without progress tracking
                blob.upload_from_file(
                    f,
                    size=file_size,
                    timeout=self.timeout,
                    num_retries=0
                )
    
    async def _upload_file_with_retry(
        self, 
        local_path: Path, 
        gcs_path: str,
        operation_name: str = "upload",
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> None:
        """
        Upload file to GCS with retry logic and optional progress tracking.
        
        Args:
            local_path: Local file path
            gcs_path: Destination path in GCS (without gs:// prefix)
            operation_name: Name for logging
            progress_callback: Optional callback(bytes_uploaded, total_bytes)
        """
        async def _do_upload():
            """Internal upload function to be retried."""
            blob = self.bucket.blob(gcs_path)
            
            # Run blocking upload in thread pool
            loop = asyncio.get_event_loop()
            
            if progress_callback:
                # Use chunked upload with progress tracking
                await loop.run_in_executor(
                    None,
                    self._upload_with_progress,
                    blob,
                    local_path,
                    progress_callback
                )
            else:
                # Use simple upload without progress (existing behavior)
                from functools import partial
                upload_func = partial(
                    blob.upload_from_filename,
                    str(local_path),
                    timeout=self.timeout
                )
                await loop.run_in_executor(None, upload_func)
        
        # Use retry utility
        await retry_with_backoff(
            _do_upload,
            max_retries=self.max_retries,
            base_delay=self.retry_base_delay,
            operation_name=operation_name
        )
    
    def generate_signed_url(self, gcs_path: str, expiry_hours: float = None) -> str:
        """
        Generate signed URL for a GCS object.
        
        Args:
            gcs_path: Path in bucket (e.g., "audio/video123/video123.wav")
            expiry_hours: Hours until expiration (defaults to config value)
        
        Returns:
            Signed URL string
        """
        if expiry_hours is None:
            expiry_hours = self.expiry_hours
        
        blob = self.bucket.blob(gcs_path)
        
        # If using service account, use credentials for signing
        if self.credentials:
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(hours=expiry_hours),
                method="GET",
                credentials=self.credentials  # Explicitly use service account
            )
            logger.debug(f"Generated signed URL using service account credentials",url)
        else:
            # Fallback to default signing (may fail with ADC)
            logger.warning("Generating signed URL without service account - this may fail")
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(hours=expiry_hours),
                method="GET"
            )
        
        return url
    
    async def upload_audio(
        self, 
        local_path: Path, 
        video_id: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[str, str]:
        """
        Upload audio file to GCS and return signed URL.
        
        Args:
            local_path: Path to local audio file
            video_id: Video identifier
            progress_callback: Optional callback(bytes_uploaded, total_bytes)
        
        Returns:
            Tuple of (gcs_path, signed_url)
        
        Raises:
            FileNotFoundError: If local file doesn't exist
            Exception: If upload fails after retries
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Audio file not found: {local_path}")
        
        # Construct GCS path: audio/{video_id}/{video_id}.wav
        filename = f"{video_id}.wav"
        gcs_path = f"{self.audio_prefix}{video_id}/{filename}"
        
        file_size_mb = self._get_file_size_mb(local_path)
        start_time = time.time()
        
        logger.info(
            f"Starting GCS audio upload: {local_path} -> gs://{self.bucket_name}/{gcs_path} "
            f"({file_size_mb:.2f} MB)"
        )
        
        # Check if file already exists in GCS
        blob = self.bucket.blob(gcs_path)
        if blob.exists():
            blob.reload()  # Get metadata
            local_md5 = self._get_file_md5(local_path)
            
            # Compare MD5 hashes (GCS returns base64, we have hex)
            if blob.md5_hash:
                import base64
                remote_md5_hex = base64.b64decode(blob.md5_hash).hex()
                
                if remote_md5_hex == local_md5:
                    logger.info(
                        f"Audio file already exists in GCS with matching MD5: gs://{self.bucket_name}/{gcs_path}. "
                        f"Skipping upload and generating new signed URL."
                    )
                    signed_url = self.generate_signed_url(gcs_path)
                    logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s)): {signed_url}")
                    return (gcs_path, signed_url)
                else:
                    logger.warning(
                        f"MD5 mismatch for gs://{self.bucket_name}/{gcs_path}! "
                        f"Local: {local_md5}, Remote: {remote_md5_hex}. Re-uploading..."
                    )
        
        # Upload file with retry and progress tracking
        await self._upload_file_with_retry(
            local_path,
            gcs_path,
            operation_name=f"GCS audio upload ({video_id})",
            progress_callback=progress_callback
        )
        
        duration = time.time() - start_time
        logger.info(
            f"Successfully uploaded audio to GCS: gs://{self.bucket_name}/{gcs_path} "
            f"({duration:.2f}s)"
        )
        
        # Generate signed URL
        signed_url = self.generate_signed_url(gcs_path)
        logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s)): {signed_url}")
        
        return (gcs_path, signed_url)
    
    async def upload_clip(
        self, 
        local_path: Path, 
        video_id: str, 
        moment_id: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[str, str]:
        """
        Upload video clip to GCS and return signed URL.
        
        Args:
            local_path: Path to local clip file
            video_id: Video identifier
            moment_id: Moment identifier
            progress_callback: Optional callback(bytes_uploaded, total_bytes)
        
        Returns:
            Tuple of (gcs_path, signed_url)
        
        Raises:
            FileNotFoundError: If local file doesn't exist
            Exception: If upload fails after retries
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Clip file not found: {local_path}")
        
        # Construct GCS path: clips/{video_id}/{video_id}_{moment_id}_clip.mp4
        filename = f"{video_id}_{moment_id}_clip.mp4"
        gcs_path = f"{self.clips_prefix}{video_id}/{filename}"
        
        file_size_mb = self._get_file_size_mb(local_path)
        start_time = time.time()
        
        logger.info(
            f"Starting GCS clip upload: {local_path} -> gs://{self.bucket_name}/{gcs_path} "
            f"({file_size_mb:.2f} MB)"
        )
        
        # Always upload clips (they may be regenerated with different content)
        await self._upload_file_with_retry(
            local_path,
            gcs_path,
            operation_name=f"GCS clip upload ({video_id}/{moment_id})",
            progress_callback=progress_callback
        )
        
        duration = time.time() - start_time
        logger.info(
            f"Successfully uploaded clip to GCS: gs://{self.bucket_name}/{gcs_path} "
            f"({duration:.2f}s)"
        )
        
        # Generate signed URL
        signed_url = self.generate_signed_url(gcs_path)
        logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s)): {signed_url}")
        
        return (gcs_path, signed_url)
    
    async def upload_video(
        self, 
        local_path: Path, 
        identifier: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[str, str]:
        """
        Upload video file to GCS and return signed URL.
        
        Args:
            local_path: Path to the local video file
            identifier: Video identifier (e.g., "motivation")
            progress_callback: Optional callback(bytes_uploaded, total_bytes)
        
        Returns:
            Tuple of (gcs_path, signed_url)
        
        Raises:
            FileNotFoundError: If local file doesn't exist
            Exception: If upload fails after retries
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Video file not found: {local_path}")
        
        # Construct GCS path: videos/{identifier}/{filename}
        filename = local_path.name
        gcs_path = f"{self.videos_prefix}{identifier}/{filename}"
        
        file_size_mb = self._get_file_size_mb(local_path)
        start_time = time.time()
        
        logger.info(
            f"Starting GCS video upload: {local_path} -> gs://{self.bucket_name}/{gcs_path} "
            f"({file_size_mb:.2f} MB)"
        )
        
        # Check if file already exists in GCS
        blob = self.bucket.blob(gcs_path)
        if blob.exists():
            blob.reload()  # Get metadata
            local_md5 = self._get_file_md5(local_path)
            
            # Compare MD5 hashes (GCS returns base64, we have hex)
            if blob.md5_hash:
                import base64
                remote_md5_hex = base64.b64decode(blob.md5_hash).hex()
                
                if remote_md5_hex == local_md5:
                    logger.info(
                        f"Video file already exists in GCS with matching MD5: gs://{self.bucket_name}/{gcs_path}. "
                        f"Skipping upload and generating new signed URL."
                    )
                    signed_url = self.generate_signed_url(gcs_path)
                    logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s)): {signed_url}")
                    return (gcs_path, signed_url)
                else:
                    logger.warning(
                        f"MD5 mismatch for gs://{self.bucket_name}/{gcs_path}! "
                        f"Local: {local_md5}, Remote: {remote_md5_hex}. Re-uploading..."
                    )
        
        # Upload file with retry and progress tracking
        await self._upload_file_with_retry(
            local_path,
            gcs_path,
            operation_name=f"GCS video upload ({identifier})",
            progress_callback=progress_callback
        )
        
        duration = time.time() - start_time
        logger.info(
            f"Successfully uploaded video to GCS: gs://{self.bucket_name}/{gcs_path} "
            f"({duration:.2f}s)"
        )
        
        # Generate signed URL
        signed_url = self.generate_signed_url(gcs_path)
        logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s)): {signed_url}")
        
        return (gcs_path, signed_url)
    
    def get_video_signed_url(self, identifier: str, filename: str) -> Optional[str]:
        """
        Get a signed URL for an existing video in GCS.
        
        Args:
            identifier: Video identifier (e.g., "motivation")
            filename: Video filename (e.g., "motivation.mp4")
        
        Returns:
            Signed URL or None if blob doesn't exist
        """
        # Construct GCS path: videos/{identifier}/{filename}
        gcs_path = f"{self.videos_prefix}{identifier}/{filename}"
        
        blob = self.bucket.blob(gcs_path)
        if not blob.exists():
            logger.warning(f"Video blob does not exist: gs://{self.bucket_name}/{gcs_path}")
            return None
        
        signed_url = self.generate_signed_url(gcs_path)
        logger.info(f"Generated signed URL for video: {gcs_path}")
        return signed_url
    
    async def delete_clips_for_video(self, video_id: str) -> int:
        """
        Delete all GCS clip files for a video.
        
        Args:
            video_id: Video identifier
        
        Returns:
            Number of clip files deleted
        """
        prefix = f"{self.clips_prefix}{video_id}/"
        
        # Run blocking GCS list+delete in thread pool
        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(None, self._delete_by_prefix, prefix)
        
        return count
    
    async def upload_all_clips(
        self, 
        video_id: str, 
        moments: List[Dict],
        progress_callback: Optional[Callable[[int, int, int, int], None]] = None
    ) -> List[Dict]:
        """
        Upload all clips for moments, return updated moments with GCS info.
        
        Args:
            video_id: Video identifier
            moments: List of moment dictionaries
            progress_callback: Optional callback(clip_index, total_clips, bytes_uploaded, total_bytes)
                              Note: bytes_uploaded and total_bytes are cumulative across all clips
        
        Returns:
            Updated moments list with gcs_clip_path and clip_signed_url fields
        """
        from app.services.video_clipping_service import get_clip_path
        
        # Pre-calculate total size of all clips and build list of clips to upload
        clip_paths = []
        for moment in moments:
            clip_path = get_clip_path(moment['id'], f"{video_id}.mp4")
            if clip_path.exists():
                clip_paths.append((moment, clip_path))
        
        total_clips = len(clip_paths)
        total_bytes_all_clips = sum(p.stat().st_size for _, p in clip_paths)
        cumulative_bytes_completed = 0
        
        logger.info(f"Starting upload of {total_clips} clips with total size {total_bytes_all_clips} bytes")
        
        for idx, (moment, clip_path) in enumerate(clip_paths, start=1):
            clip_file_size = clip_path.stat().st_size
            
            # Create cumulative progress callback wrapper
            clip_progress_callback = None
            if progress_callback:
                def make_clip_callback(clip_idx, total, offset, grand_total):
                    def callback(bytes_uploaded: int, total_bytes: int):
                        # Pass cumulative bytes: offset + current clip's bytes
                        progress_callback(clip_idx, total, offset + bytes_uploaded, grand_total)
                    return callback
                clip_progress_callback = make_clip_callback(
                    idx, total_clips, cumulative_bytes_completed, total_bytes_all_clips
                )
            
            try:
                gcs_path, signed_url = await self.upload_clip(
                    clip_path, 
                    video_id, 
                    moment['id'],
                    progress_callback=clip_progress_callback
                )
                moment['gcs_clip_path'] = gcs_path
                moment['clip_signed_url'] = signed_url
                logger.info(f"Uploaded clip for moment {moment['id']} to GCS ({idx}/{total_clips})")
                
                # Update cumulative bytes after successful upload
                cumulative_bytes_completed += clip_file_size
            except Exception as e:
                logger.error(
                    f"Failed to upload clip for moment {moment['id']}: "
                    f"{type(e).__name__}: {e}"
                )
                # Continue with other clips even if one fails
                # Still increment cumulative bytes to keep progress accurate
                cumulative_bytes_completed += clip_file_size
        
        # Handle moments without clip files (preserve original behavior)
        for moment in moments:
            clip_path = get_clip_path(moment['id'], f"{video_id}.mp4")
            if not clip_path.exists():
                logger.warning(
                    f"Clip file not found for moment {moment['id']}: {clip_path}"
                )
        
        return moments

    async def upload_thumbnail(
        self,
        local_path: Path,
        entity_type: str,
        entity_id: str,
    ) -> Tuple[str, str]:
        """
        Upload a thumbnail JPEG to GCS and return (gcs_path, signed_url).

        Args:
            local_path: Path to the local JPEG file
            entity_type: Either "video" or "clip"
            entity_id: The video identifier or clip DB id (as string)

        Returns:
            Tuple of (gcs_path, signed_url)

        Raises:
            FileNotFoundError: If local_path does not exist
            Exception: If upload fails after retries
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Thumbnail file not found: {local_path}")

        gcs_path = f"{self.thumbnails_prefix}{entity_type}/{entity_id}.jpg"

        file_size_mb = self._get_file_size_mb(local_path)
        logger.info(
            f"Starting GCS thumbnail upload: {local_path} -> gs://{self.bucket_name}/{gcs_path} "
            f"({file_size_mb:.3f} MB)"
        )

        # Use retry-aware upload; set content_type so browsers render inline
        async def _do_upload():
            blob = self.bucket.blob(gcs_path)
            blob.content_type = "image/jpeg"
            loop = asyncio.get_event_loop()
            from functools import partial
            upload_func = partial(
                blob.upload_from_filename,
                str(local_path),
                content_type="image/jpeg",
                timeout=self.timeout,
            )
            await loop.run_in_executor(None, upload_func)

        await retry_with_backoff(
            _do_upload,
            max_retries=self.max_retries,
            base_delay=self.retry_base_delay,
            operation_name=f"GCS thumbnail upload ({entity_type}/{entity_id})",
        )

        logger.info(f"Successfully uploaded thumbnail to GCS: gs://{self.bucket_name}/{gcs_path}")

        signed_url = self.generate_signed_url(gcs_path)
        return (gcs_path, signed_url)

    def get_thumbnail_signed_url(self, gcs_path: str) -> Optional[str]:
        """
        Generate a fresh signed URL for an existing thumbnail blob.

        Args:
            gcs_path: GCS blob path (e.g., "thumbnails/video/motivation.jpg")

        Returns:
            Signed URL string, or None if the blob does not exist
        """
        blob = self.bucket.blob(gcs_path)
        if not blob.exists():
            logger.warning(f"Thumbnail blob does not exist: gs://{self.bucket_name}/{gcs_path}")
            return None
        signed_url = self.generate_signed_url(gcs_path)
        logger.debug(f"Generated signed URL for thumbnail: {gcs_path}")
        return signed_url

    def delete_thumbnail(self, entity_type: str, entity_id: str) -> bool:
        """
        Delete a single thumbnail blob from GCS.

        Args:
            entity_type: Either "video" or "clip"
            entity_id: The video identifier or clip DB id (as string)

        Returns:
            True if the blob was deleted, False if it did not exist
        """
        gcs_path = f"{self.thumbnails_prefix}{entity_type}/{entity_id}.jpg"
        try:
            blob = self.bucket.blob(gcs_path)
            if blob.exists():
                blob.delete()
                logger.info(f"Deleted GCS thumbnail: gs://{self.bucket_name}/{gcs_path}")
                return True
            logger.debug(f"Thumbnail blob not found (nothing to delete): {gcs_path}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete GCS thumbnail {gcs_path}: {e}")
            return False

    def delete_thumbnails_for_video(self, video_identifier: str) -> int:
        """
        Delete all thumbnail blobs for a video (prefix-based deletion).

        Args:
            video_identifier: Video identifier (e.g., "motivation")

        Returns:
            Number of blobs deleted
        """
        prefix = f"{self.thumbnails_prefix}video/{video_identifier}"
        count = self._delete_by_prefix(prefix)
        logger.info(f"Deleted {count} GCS thumbnail(s) for video: {video_identifier}")
        return count
