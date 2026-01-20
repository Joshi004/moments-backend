"""
Video deletion service for comprehensive cleanup of video resources.
Handles deletion of local files, GCS files, and Redis state.
"""
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from google.cloud import storage

from app.core.config import get_settings
from app.core.redis import get_redis_client
from app.services.url_registry import URLRegistry
from app.services.pipeline.status import get_status as get_pipeline_status
from app.utils.video import get_videos_directory

logger = logging.getLogger(__name__)


@dataclass
class DeleteResult:
    """Result of video deletion operation."""
    status: str  # "completed", "partial", "failed"
    video_id: str
    deleted: Dict[str, any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    duration_ms: int = 0


class LocalDeleter:
    """Handles deletion of local video files."""
    
    def __init__(self, video_id: str):
        self.video_id = video_id
        self.settings = get_settings()
        self.backend_root = Path(__file__).parent.parent.parent
        
    def delete_all(
        self,
        skip_video: bool = False,
        skip_audio: bool = False,
        skip_thumbnail: bool = False,
        skip_transcript: bool = False,
        skip_moments: bool = False,
        skip_clips: bool = False
    ) -> Dict[str, any]:
        """
        Delete local files for video_id based on skip flags.
        
        Args:
            skip_video: If True, keep video file
            skip_audio: If True, keep audio file
            skip_thumbnail: If True, keep thumbnail
            skip_transcript: If True, keep transcript
            skip_moments: If True, keep moments metadata
            skip_clips: If True, keep video clips
        
        Returns:
            Dictionary with deletion results
        """
        result = {
            "video": False,
            "audio": False,
            "thumbnail": False,
            "transcript": False,
            "moments": False,
            "clips": 0
        }
        
        # Delete video file
        if not skip_video:
            video_deleted = self._delete_video_file()
            result["video"] = video_deleted
        else:
            logger.info(f"Skipping video file deletion (skip_video=True)")
        
        # Delete audio file
        if not skip_audio:
            audio_deleted = self._delete_audio_file()
            result["audio"] = audio_deleted
        else:
            logger.info(f"Skipping audio file deletion (skip_audio=True)")
        
        # Delete thumbnail
        if not skip_thumbnail:
            thumbnail_deleted = self._delete_thumbnail()
            result["thumbnail"] = thumbnail_deleted
        else:
            logger.info(f"Skipping thumbnail deletion (skip_thumbnail=True)")
        
        # Delete transcript
        if not skip_transcript:
            transcript_deleted = self._delete_transcript()
            result["transcript"] = transcript_deleted
        else:
            logger.info(f"Skipping transcript deletion (skip_transcript=True)")
        
        # Delete moments.json
        if not skip_moments:
            moments_deleted = self._delete_moments_file()
            result["moments"] = moments_deleted
        else:
            logger.info(f"Skipping moments file deletion (skip_moments=True)")
        
        # Delete video clips
        if not skip_clips:
            clips_deleted = self._delete_clips()
            result["clips"] = clips_deleted
        else:
            logger.info(f"Skipping clips deletion (skip_clips=True)")
        
        return result
    
    def _delete_video_file(self) -> bool:
        """Delete the main video file."""
        try:
            videos_dir = self.backend_root / self.settings.videos_dir
            # Look for video file with video_id as stem
            for ext in ['.mp4', '.mov', '.avi', '.mkv']:
                video_path = videos_dir / f"{self.video_id}{ext}"
                if video_path.exists():
                    video_path.unlink()
                    logger.info(f"Deleted video file: {video_path}")
                    return True
            logger.debug(f"No video file found for {self.video_id}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete video file for {self.video_id}: {e}")
            return False
    
    def _delete_audio_file(self) -> bool:
        """Delete audio file."""
        try:
            audio_path = self.backend_root / self.settings.audios_dir / f"{self.video_id}.wav"
            if audio_path.exists():
                audio_path.unlink()
                logger.info(f"Deleted audio file: {audio_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete audio file for {self.video_id}: {e}")
            return False
    
    def _delete_thumbnail(self) -> bool:
        """Delete thumbnail file."""
        try:
            thumbnail_path = self.backend_root / self.settings.thumbnails_dir / f"{self.video_id}.jpg"
            if thumbnail_path.exists():
                thumbnail_path.unlink()
                logger.info(f"Deleted thumbnail: {thumbnail_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete thumbnail for {self.video_id}: {e}")
            return False
    
    def _delete_transcript(self) -> bool:
        """Delete transcript file."""
        try:
            transcript_path = self.backend_root / self.settings.transcripts_dir / f"{self.video_id}.json"
            if transcript_path.exists():
                transcript_path.unlink()
                logger.info(f"Deleted transcript: {transcript_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete transcript for {self.video_id}: {e}")
            return False
    
    def _delete_moments_file(self) -> bool:
        """Delete moments.json file."""
        try:
            moments_path = self.backend_root / self.settings.moments_dir / f"{self.video_id}.json"
            if moments_path.exists():
                moments_path.unlink()
                logger.info(f"Deleted moments file: {moments_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete moments file for {self.video_id}: {e}")
            return False
    
    def _delete_clips(self) -> int:
        """Delete all video clips for this video."""
        try:
            clips_dir = self.backend_root / self.settings.moment_clips_dir
            if not clips_dir.exists():
                return 0
            
            # Find all clips matching pattern: {video_id}_*_clip.mp4
            pattern = f"{self.video_id}_*_clip.mp4"
            clips = list(clips_dir.glob(pattern))
            
            deleted_count = 0
            for clip_path in clips:
                try:
                    clip_path.unlink()
                    deleted_count += 1
                    logger.debug(f"Deleted clip: {clip_path.name}")
                except Exception as e:
                    logger.error(f"Failed to delete clip {clip_path.name}: {e}")
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} clips for {self.video_id}")
            
            return deleted_count
        except Exception as e:
            logger.error(f"Failed to delete clips for {self.video_id}: {e}")
            return 0


class GCSDeleter:
    """Handles deletion of GCS files using bucket listing."""
    
    def __init__(self, video_id: str):
        self.video_id = video_id
        self.settings = get_settings()
        self._init_gcs_client()
    
    def _init_gcs_client(self):
        """Initialize GCS client."""
        try:
            credentials_path = self.settings.gcs_credentials_path
            if credentials_path and credentials_path.exists():
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(
                    str(credentials_path),
                    scopes=['https://www.googleapis.com/auth/cloud-platform']
                )
                self.client = storage.Client(
                    credentials=credentials,
                    project=credentials.project_id
                )
                logger.info(f"GCS client initialized with service account")
            else:
                import google.auth
                self.client = storage.Client()
                logger.info("GCS client initialized with Application Default Credentials")
            
            self.bucket = self.client.bucket(self.settings.gcs_bucket_name)
        except Exception as e:
            logger.error(f"Failed to initialize GCS client: {e}")
            self.client = None
            self.bucket = None
    
    def delete_all(
        self,
        skip_audio: bool = False,
        skip_clips: bool = False
    ) -> Dict[str, int]:
        """
        Delete GCS files for video_id based on skip flags.
        
        Args:
            skip_audio: If True, keep GCS audio files
            skip_clips: If True, keep GCS clip files
        
        Returns:
            Dictionary with counts of deleted files
        """
        result = {
            "audio_files": 0,
            "clip_files": 0
        }
        
        if not self.client or not self.bucket:
            logger.warning("GCS client not initialized, skipping GCS deletion")
            return result
        
        # Delete audio files
        if not skip_audio:
            audio_count = self._delete_by_prefix(f"{self.settings.gcs_audio_prefix}{self.video_id}/")
            result["audio_files"] = audio_count
        else:
            logger.info(f"Skipping GCS audio deletion (skip_audio=True)")
        
        # Delete clip files
        if not skip_clips:
            clips_count = self._delete_by_prefix(f"{self.settings.gcs_clips_prefix}{self.video_id}/")
            result["clip_files"] = clips_count
        else:
            logger.info(f"Skipping GCS clips deletion (skip_clips=True)")
        
        total = result["audio_files"] + result["clip_files"]
        if total > 0:
            logger.info(f"Deleted {total} files from GCS for {self.video_id}")
        
        return result
    
    def _delete_by_prefix(self, prefix: str) -> int:
        """
        Delete all blobs with given prefix.
        
        Args:
            prefix: GCS prefix (e.g., "audio/video123/")
        
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


class StateDeleter:
    """Handles deletion of Redis state and URL registry entries."""
    
    def __init__(self, video_id: str):
        self.video_id = video_id
    
    def delete_all(self) -> Dict[str, any]:
        """
        Delete all state for video_id.
        
        Returns:
            Dictionary with deletion results
        """
        result = {
            "redis_keys": 0,
            "url_registry": False
        }
        
        # Delete Redis keys
        redis_count = self._delete_redis_keys()
        result["redis_keys"] = redis_count
        
        # Delete URL registry entry
        registry_deleted = self._delete_url_registry()
        result["url_registry"] = registry_deleted
        
        return result
    
    def _delete_redis_keys(self) -> int:
        """Delete all Redis keys associated with video_id."""
        try:
            redis = get_redis_client()
            deleted_count = 0
            
            # Keys to delete:
            # - pipeline:{video_id}:active
            # - pipeline:{video_id}:history
            # - pipeline:{video_id}:lock
            # - pipeline:{video_id}:cancel
            # - pipeline:run:{request_id} (need to scan for these)
            
            keys_to_delete = [
                f"pipeline:{self.video_id}:active",
                f"pipeline:{self.video_id}:history",
                f"pipeline:{self.video_id}:lock",
                f"pipeline:{self.video_id}:cancel",
            ]
            
            # Delete known keys
            for key in keys_to_delete:
                if redis.exists(key):
                    redis.delete(key)
                    deleted_count += 1
                    logger.debug(f"Deleted Redis key: {key}")
            
            # Scan for run records (pipeline:run:*)
            # These might contain video_id references but we'll skip them for now
            # as they expire automatically
            
            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} Redis keys for {self.video_id}")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"Failed to delete Redis keys for {self.video_id}: {e}")
            return 0
    
    def _delete_url_registry(self) -> bool:
        """Delete URL registry entry."""
        try:
            registry = URLRegistry()
            success = registry.unregister(self.video_id)
            if success:
                logger.info(f"Deleted URL registry entry for {self.video_id}")
            return success
        except Exception as e:
            logger.error(f"Failed to delete URL registry entry for {self.video_id}: {e}")
            return False


class VideoDeleteService:
    """Main service for video deletion orchestration."""
    
    def __init__(self):
        self.settings = get_settings()
    
    async def delete_video(
        self,
        video_id: str,
        # Local file options
        skip_local_video: bool = False,
        skip_local_audio: bool = False,
        skip_local_thumbnail: bool = False,
        skip_local_transcript: bool = False,
        skip_local_moments: bool = False,
        skip_local_clips: bool = False,
        # GCS options
        skip_gcs_audio: bool = False,
        skip_gcs_clips: bool = False,
        # State options
        skip_redis: bool = False,
        skip_registry: bool = False,
        force: bool = False
    ) -> DeleteResult:
        """
        Delete video and all associated resources.
        
        Args:
            video_id: Video identifier
            skip_local_video: If True, keep local video file
            skip_local_audio: If True, keep local audio file
            skip_local_thumbnail: If True, keep local thumbnail
            skip_local_transcript: If True, keep local transcript
            skip_local_moments: If True, keep local moments metadata
            skip_local_clips: If True, keep local video clips
            skip_gcs_audio: If True, keep GCS audio files
            skip_gcs_clips: If True, keep GCS clip files
            skip_redis: If True, keep Redis state
            skip_registry: If True, keep URL registry entry
            force: If True, skip active pipeline check
        
        Returns:
            DeleteResult with status and details
        """
        start_time = time.time()
        logger.info(f"Starting deletion for video: {video_id}")
        
        result = DeleteResult(
            status="completed",
            video_id=video_id,
            deleted={
                "local": {},
                "gcs": {},
                "redis_keys": 0,
                "url_registry": False
            },
            errors=[]
        )
        
        # Pre-deletion checks
        if not force:
            pipeline_status = get_pipeline_status(video_id)
            if pipeline_status and pipeline_status.get("status") in ["processing", "pending", "queued"]:
                result.status = "failed"
                result.errors.append(
                    f"Cannot delete video while pipeline is active (status: {pipeline_status.get('status')}). "
                    f"Use force=true to delete anyway."
                )
                result.duration_ms = int((time.time() - start_time) * 1000)
                return result
        
        # Check if video exists (at least one resource)
        video_exists = self._check_video_exists(video_id)
        if not video_exists:
            logger.warning(f"No resources found for video: {video_id}")
            # Still proceed with deletion to clean up any orphaned state
        
        # Delete in order: Redis -> Registry -> GCS -> Local
        
        # 1. Delete Redis state (unless skipped)
        if not skip_redis:
            try:
                state_deleter = StateDeleter(video_id)
                state_result = state_deleter.delete_all()
                result.deleted["redis_keys"] = state_result["redis_keys"]
                
                # URL registry is part of state deletion
                if not skip_registry:
                    result.deleted["url_registry"] = state_result["url_registry"]
                else:
                    logger.info("Skipping URL registry deletion (skip_registry=True)")
            except Exception as e:
                error_msg = f"Redis/Registry deletion failed: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                result.status = "partial"
        else:
            logger.info("Skipping Redis deletion (skip_redis=True)")
            if not skip_registry:
                # Delete registry separately if Redis is skipped
                try:
                    state_deleter = StateDeleter(video_id)
                    registry_deleted = state_deleter._delete_url_registry()
                    result.deleted["url_registry"] = registry_deleted
                except Exception as e:
                    error_msg = f"Registry deletion failed: {e}"
                    logger.error(error_msg)
                    result.errors.append(error_msg)
        
        # 2. Delete GCS files (with granular control)
        try:
            gcs_deleter = GCSDeleter(video_id)
            gcs_result = gcs_deleter.delete_all(
                skip_audio=skip_gcs_audio,
                skip_clips=skip_gcs_clips
            )
            result.deleted["gcs"] = gcs_result
        except Exception as e:
            error_msg = f"GCS deletion failed: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            result.status = "partial"
        
        # 3. Delete local files (with granular control)
        try:
            local_deleter = LocalDeleter(video_id)
            local_result = local_deleter.delete_all(
                skip_video=skip_local_video,
                skip_audio=skip_local_audio,
                skip_thumbnail=skip_local_thumbnail,
                skip_transcript=skip_local_transcript,
                skip_moments=skip_local_moments,
                skip_clips=skip_local_clips
            )
            result.deleted["local"] = local_result
        except Exception as e:
            error_msg = f"Local file deletion failed: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            result.status = "partial"
        
        # Calculate duration
        result.duration_ms = int((time.time() - start_time) * 1000)
        
        # Log summary
        total_deleted = (
            sum(1 for v in result.deleted["local"].values() if v) +
            sum(result.deleted["gcs"].values()) +
            result.deleted["redis_keys"] +
            (1 if result.deleted["url_registry"] else 0)
        )
        
        logger.info(
            f"Deletion completed for {video_id}: status={result.status}, "
            f"deleted={total_deleted} resources, duration={result.duration_ms}ms"
        )
        
        return result
    
    def _check_video_exists(self, video_id: str) -> bool:
        """Check if video has any resources."""
        # Check if video file exists
        backend_root = Path(__file__).parent.parent.parent
        videos_dir = backend_root / self.settings.videos_dir
        
        for ext in ['.mp4', '.mov', '.avi', '.mkv']:
            if (videos_dir / f"{video_id}{ext}").exists():
                return True
        
        # Check other files
        if (backend_root / self.settings.audios_dir / f"{video_id}.wav").exists():
            return True
        if (backend_root / self.settings.thumbnails_dir / f"{video_id}.jpg").exists():
            return True
        if (backend_root / self.settings.transcripts_dir / f"{video_id}.json").exists():
            return True
        if (backend_root / self.settings.moments_dir / f"{video_id}.json").exists():
            return True
        
        return False
