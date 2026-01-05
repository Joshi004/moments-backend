"""
GCS-based file uploader for audio files and video clips.
Uploads files to Google Cloud Storage and generates signed URLs for remote access.
"""
import asyncio
import logging
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple
from datetime import timedelta
import google.auth
from google.cloud import storage
from google.oauth2 import service_account
from app.core.config import get_settings
from app.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)


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
        
        self.bucket = self.client.bucket(self.bucket_name)
    
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
    
    async def _upload_file_with_retry(
        self, 
        local_path: Path, 
        gcs_path: str,
        operation_name: str = "upload"
    ) -> None:
        """
        Upload file to GCS with retry logic.
        
        Args:
            local_path: Local file path
            gcs_path: Destination path in GCS (without gs:// prefix)
            operation_name: Name for logging
        """
        async def _do_upload():
            """Internal upload function to be retried."""
            blob = self.bucket.blob(gcs_path)
            
            # Run blocking upload in thread pool
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                blob.upload_from_filename,
                str(local_path)
            )
        
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
            logger.debug(f"Generated signed URL using service account credentials")
        else:
            # Fallback to default signing (may fail with ADC)
            logger.warning("Generating signed URL without service account - this may fail")
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(hours=expiry_hours),
                method="GET"
            )
        
        return url
    
    async def upload_audio(self, local_path: Path, video_id: str) -> Tuple[str, str]:
        """
        Upload audio file to GCS and return signed URL.
        
        Args:
            local_path: Path to local audio file
            video_id: Video identifier
        
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
            
            # Compare MD5 hashes (note: GCS returns base64, we have hex)
            if blob.md5_hash:
                logger.info(
                    f"Audio file already exists in GCS: gs://{self.bucket_name}/{gcs_path}. "
                    f"Skipping upload and generating new signed URL."
                )
                signed_url = self.generate_signed_url(gcs_path)
                logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s))")
                return (gcs_path, signed_url)
        
        # Upload file with retry
        await self._upload_file_with_retry(
            local_path,
            gcs_path,
            operation_name=f"GCS audio upload ({video_id})"
        )
        
        duration = time.time() - start_time
        logger.info(
            f"Successfully uploaded audio to GCS: gs://{self.bucket_name}/{gcs_path} "
            f"({duration:.2f}s)"
        )
        
        # Generate signed URL
        signed_url = self.generate_signed_url(gcs_path)
        logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s))")
        
        return (gcs_path, signed_url)
    
    async def upload_clip(
        self, 
        local_path: Path, 
        video_id: str, 
        moment_id: str
    ) -> Tuple[str, str]:
        """
        Upload video clip to GCS and return signed URL.
        
        Args:
            local_path: Path to local clip file
            video_id: Video identifier
            moment_id: Moment identifier
        
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
            operation_name=f"GCS clip upload ({video_id}/{moment_id})"
        )
        
        duration = time.time() - start_time
        logger.info(
            f"Successfully uploaded clip to GCS: gs://{self.bucket_name}/{gcs_path} "
            f"({duration:.2f}s)"
        )
        
        # Generate signed URL
        signed_url = self.generate_signed_url(gcs_path)
        logger.info(f"Generated signed URL (expires in {self.expiry_hours} hour(s))")
        
        return (gcs_path, signed_url)
    
    async def upload_all_clips(self, video_id: str, moments: List[Dict]) -> List[Dict]:
        """
        Upload all clips for moments, return updated moments with GCS info.
        
        Args:
            video_id: Video identifier
            moments: List of moment dictionaries
        
        Returns:
            Updated moments list with gcs_clip_path and clip_signed_url fields
        """
        from app.services.video_clipping_service import get_clip_path
        
        for moment in moments:
            clip_path = get_clip_path(moment['id'], f"{video_id}.mp4")
            if clip_path.exists():
                try:
                    gcs_path, signed_url = await self.upload_clip(
                        clip_path, 
                        video_id, 
                        moment['id']
                    )
                    moment['gcs_clip_path'] = gcs_path
                    moment['clip_signed_url'] = signed_url
                    logger.info(f"Uploaded clip for moment {moment['id']} to GCS")
                except Exception as e:
                    logger.error(
                        f"Failed to upload clip for moment {moment['id']}: "
                        f"{type(e).__name__}: {e}"
                    )
                    # Continue with other clips even if one fails
            else:
                logger.warning(
                    f"Clip file not found for moment {moment['id']}: {clip_path}"
                )
        
        return moments



