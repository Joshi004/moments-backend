"""
Video deletion service.
Handles deletion of database records, GCS files, temp files, and Redis state.
All data operations use database CASCADE deletes and GCS prefix deletion.
"""
import logging
import time
from typing import Dict, List
from dataclasses import dataclass, field

from google.cloud import storage

from app.core.config import get_settings
from app.core.redis import get_async_redis_client
from app.services.pipeline.status import get_status as get_pipeline_status

logger = logging.getLogger(__name__)


@dataclass
class DeleteResult:
    """Result of video deletion operation."""
    status: str  # "completed", "partial", "failed"
    video_id: str
    deleted: Dict[str, any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    duration_ms: int = 0


class GCSDeleter:
    """Handles deletion of GCS files by prefix."""

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
                logger.info("GCS client initialized with service account")
            else:
                self.client = storage.Client()
                logger.info("GCS client initialized with Application Default Credentials")

            self.bucket = self.client.bucket(self.settings.gcs_bucket_name)
        except Exception as e:
            logger.error(f"Failed to initialize GCS client: {e}")
            self.client = None
            self.bucket = None

    def delete_all(
        self,
        skip_video: bool = False,
        skip_audio: bool = False,
        skip_clips: bool = False,
        skip_thumbnails: bool = False,
    ) -> Dict[str, int]:
        """
        Delete all GCS files for video_id.

        Returns:
            Dictionary with counts of deleted files per category
        """
        result = {
            "video_files": 0,
            "audio_files": 0,
            "clip_files": 0,
            "thumbnail_files": 0,
        }

        if not self.client or not self.bucket:
            logger.warning("GCS client not initialized, skipping GCS deletion")
            return result

        if not skip_video:
            video_count = self._delete_by_prefix(f"{self.settings.gcs_videos_prefix}{self.video_id}/")
            result["video_files"] = video_count
        else:
            logger.info("Skipping GCS video deletion (skip_video=True)")

        if not skip_audio:
            audio_count = self._delete_by_prefix(f"{self.settings.gcs_audio_prefix}{self.video_id}/")
            result["audio_files"] = audio_count
        else:
            logger.info("Skipping GCS audio deletion (skip_audio=True)")

        if not skip_clips:
            clips_count = self._delete_by_prefix(f"{self.settings.gcs_clips_prefix}{self.video_id}/")
            result["clip_files"] = clips_count
        else:
            logger.info("Skipping GCS clips deletion (skip_clips=True)")

        if not skip_thumbnails:
            thumbnail_prefix = f"{self.settings.gcs_thumbnails_prefix}video/{self.video_id}"
            thumbnail_count = self._delete_by_prefix(thumbnail_prefix)
            result["thumbnail_files"] = thumbnail_count
        else:
            logger.info("Skipping GCS thumbnail deletion (skip_thumbnails=True)")

        total = sum(result.values())
        if total > 0:
            logger.info(f"Deleted {total} files from GCS for {self.video_id}")

        return result

    def _delete_by_prefix(self, prefix: str) -> int:
        """Delete all blobs with given prefix. Returns count deleted."""
        try:
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
    """Handles deletion of Redis pipeline state."""

    def __init__(self, video_id: str):
        self.video_id = video_id

    async def delete_all(self) -> Dict[str, any]:
        """Delete all Redis state for video_id. Returns deletion results."""
        result = {"redis_keys": 0}
        redis_count = await self._delete_redis_keys()
        result["redis_keys"] = redis_count
        return result

    async def _delete_redis_keys(self) -> int:
        """Delete all Redis keys associated with video_id."""
        try:
            redis = await get_async_redis_client()
            deleted_count = 0

            keys_to_delete = [
                f"pipeline:{self.video_id}:active",
                f"pipeline:{self.video_id}:history",
                f"pipeline:{self.video_id}:lock",
                f"pipeline:{self.video_id}:cancel",
            ]

            for key in keys_to_delete:
                if await redis.exists(key):
                    await redis.delete(key)
                    deleted_count += 1
                    logger.debug(f"Deleted Redis key: {key}")

            if deleted_count > 0:
                logger.info(f"Deleted {deleted_count} Redis keys for {self.video_id}")

            return deleted_count

        except Exception as e:
            logger.error(f"Failed to delete Redis keys for {self.video_id}: {e}")
            return 0


class VideoDeleteService:
    """Main service for video deletion orchestration."""

    def __init__(self):
        self.settings = get_settings()

    async def delete_video(
        self,
        video_id: str,
        # GCS options
        skip_gcs_video: bool = False,
        skip_gcs_audio: bool = False,
        skip_gcs_clips: bool = False,
        skip_gcs_thumbnails: bool = False,
        # State options
        skip_redis: bool = False,
        # Database option
        skip_database: bool = False,
        force: bool = False
    ) -> DeleteResult:
        """
        Delete video and all associated resources.

        Deletion order:
          1. GCS files (video, audio, clips, thumbnails)
          2. Temp files (managed by temp_file_manager)
          3. Redis state (pipeline keys)
          4. Database record (CASCADE deletes transcripts, moments, clips, thumbnails, history)

        Args:
            video_id: Video identifier
            skip_gcs_video: Keep GCS video file
            skip_gcs_audio: Keep GCS audio files
            skip_gcs_clips: Keep GCS clip files
            skip_gcs_thumbnails: Keep GCS thumbnail files
            skip_redis: Keep Redis state
            skip_database: Keep database record (and cascaded data)
            force: Skip active pipeline check

        Returns:
            DeleteResult with status and details
        """
        start_time = time.time()
        logger.info(f"Starting deletion for video: {video_id}")

        result = DeleteResult(
            status="completed",
            video_id=video_id,
            deleted={
                "gcs": {},
                "temp": {},
                "redis_keys": 0,
                "database": False,
            },
            errors=[],
        )

        # Pre-deletion checks
        if not force:
            pipeline_status = await get_pipeline_status(video_id)
            if pipeline_status and pipeline_status.get("status") in ["processing", "pending", "queued"]:
                result.status = "failed"
                result.errors.append(
                    f"Cannot delete video while pipeline is active (status: {pipeline_status.get('status')}). "
                    f"Use force=true to delete anyway."
                )
                result.duration_ms = int((time.time() - start_time) * 1000)
                return result

        # Check if video exists in database
        video_exists = await self._check_video_exists(video_id)
        if not video_exists:
            logger.warning(f"No database record found for video: {video_id}")
            # Still proceed to clean up any orphaned state

        # 1. Delete GCS files
        try:
            gcs_deleter = GCSDeleter(video_id)
            gcs_result = gcs_deleter.delete_all(
                skip_video=skip_gcs_video,
                skip_audio=skip_gcs_audio,
                skip_clips=skip_gcs_clips,
                skip_thumbnails=skip_gcs_thumbnails,
            )
            result.deleted["gcs"] = gcs_result
        except Exception as e:
            error_msg = f"GCS deletion failed: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            result.status = "partial"

        # 2. Delete managed temp files
        try:
            from app.services.temp_file_manager import cleanup_video as cleanup_temp_video
            temp_result = await cleanup_temp_video(video_id)
            result.deleted["temp"] = temp_result
        except Exception as e:
            error_msg = f"Temp file cleanup failed: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            result.status = "partial"

        # 3. Delete Redis state
        if not skip_redis:
            try:
                state_deleter = StateDeleter(video_id)
                state_result = await state_deleter.delete_all()
                result.deleted["redis_keys"] = state_result["redis_keys"]
            except Exception as e:
                error_msg = f"Redis deletion failed: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                result.status = "partial"
        else:
            logger.info("Skipping Redis deletion (skip_redis=True)")

        # 4. Delete from database (CASCADE handles all related records)
        if not skip_database:
            try:
                from app.database.session import get_session_factory
                from app.repositories import video_db_repository

                session_factory = get_session_factory()
                async with session_factory() as session:
                    video = await video_db_repository.get_by_identifier(session, video_id)
                    if video:
                        await video_db_repository.delete(session, video.id)
                        await session.commit()
                        result.deleted["database"] = True
                        logger.info(
                            f"Deleted video {video_id} from database "
                            f"(CASCADE removed transcripts, moments, clips, thumbnails, history)"
                        )
                    else:
                        logger.warning(f"Video {video_id} not found in database during deletion")
            except Exception as e:
                error_msg = f"Database deletion failed: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                result.status = "partial"
        else:
            logger.info("Skipping database deletion (skip_database=True)")

        result.duration_ms = int((time.time() - start_time) * 1000)

        gcs_total = sum(result.deleted["gcs"].values()) if isinstance(result.deleted["gcs"], dict) else 0
        logger.info(
            f"Deletion completed for {video_id}: status={result.status}, "
            f"gcs={gcs_total} files, database={result.deleted['database']}, "
            f"redis={result.deleted['redis_keys']} keys, duration={result.duration_ms}ms"
        )

        return result

    async def _check_video_exists(self, video_id: str) -> bool:
        """Check if video exists in the database."""
        try:
            from app.database.session import get_session_factory
            from app.repositories import video_db_repository

            session_factory = get_session_factory()
            async with session_factory() as session:
                video = await video_db_repository.get_by_identifier(session, video_id)
                return video is not None
        except Exception as e:
            logger.error(f"Failed to check video existence in database: {e}")
            return False
