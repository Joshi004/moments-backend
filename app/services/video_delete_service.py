"""
Video deletion service.
Handles scoped deletion of DB records, GCS files, temp files, and Redis state.
"""
import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from google.cloud import storage
from google.api_core.exceptions import NotFound

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.redis import get_async_redis_client
from app.services.pipeline.status import get_status as get_pipeline_status
from app.database.session import get_session_factory
from app.database.models.video import Video
from app.database.models.moment import Moment
from app.database.models.clip import Clip
from app.database.models.thumbnail import Thumbnail
from app.database.models.audio import Audio
from app.repositories import video_db_repository

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
    """Handles deletion of GCS files for a video."""

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

    # ------------------------------------------------------------------
    # Prefix-based bulk deletion (used by scope=all)
    # ------------------------------------------------------------------

    def delete_all(
        self,
        skip_video: bool = False,
        skip_audio: bool = False,
        skip_clips: bool = False,
        skip_thumbnails: bool = False,
    ) -> Dict[str, int]:
        """
        Delete all GCS files for video_id by prefix.

        Kept for backward compatibility with scope=all.
        Returns a dict with counts per category.
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
            result["video_files"] = self._delete_by_prefix(
                f"{self.settings.gcs_videos_prefix}{self.video_id}/"
            )

        if not skip_audio:
            result["audio_files"] = self._delete_by_prefix(
                f"{self.settings.gcs_audio_prefix}{self.video_id}/"
            )

        if not skip_clips:
            result["clip_files"] = self._delete_by_prefix(
                f"{self.settings.gcs_clips_prefix}{self.video_id}/"
            )

        if not skip_thumbnails:
            result["thumbnail_files"] = self._delete_by_prefix(
                f"{self.settings.gcs_thumbnails_prefix}video/{self.video_id}"
            )

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

    # ------------------------------------------------------------------
    # Single-file deletion (used by scope=video_file, moments, refined_moments)
    # ------------------------------------------------------------------

    def delete_by_url(self, url: str) -> bool:
        """
        Delete a single GCS blob by its GCS path or gs:// URI.

        Strips gs://{bucket}/ prefix if present to extract the blob name.
        Returns True if deleted, False if not found or on error.
        """
        if not self.client or not self.bucket:
            logger.warning("GCS client not initialized, skipping single-file deletion")
            return False

        if not url:
            return False

        # Strip gs://bucket-name/ prefix if present
        gs_prefix = f"gs://{self.settings.gcs_bucket_name}/"
        if url.startswith(gs_prefix):
            blob_name = url[len(gs_prefix):]
        else:
            blob_name = url

        try:
            blob = self.bucket.blob(blob_name)
            blob.delete()
            logger.debug(f"Deleted GCS blob: {blob_name}")
            return True
        except NotFound:
            logger.warning(f"GCS blob not found (already deleted?): {blob_name}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete GCS blob {blob_name}: {e}")
            return False

    def delete_video_file(self, video_url: str) -> bool:
        """Delete the video GCS file by its cloud_url."""
        return self.delete_by_url(video_url)

    def delete_audio_file(self, audio_url: str) -> bool:
        """Delete the audio GCS file by its cloud_url."""
        return self.delete_by_url(audio_url)

    async def delete_clip_thumbnails_for_clips(
        self, clip_ids: list[int], session: AsyncSession
    ) -> int:
        """
        Delete all clip thumbnail GCS files for the given clip DB IDs.

        Queries thumbnail cloud_url values from DB using the provided session,
        then deletes each from GCS. Returns total count of successfully deleted files.
        """
        if not clip_ids:
            return 0

        stmt = select(Thumbnail).where(Thumbnail.clip_id.in_(clip_ids))
        result = await session.execute(stmt)
        thumbnails = list(result.scalars().all())

        deleted_count = 0
        for thumbnail in thumbnails:
            if thumbnail.cloud_url and self.delete_by_url(thumbnail.cloud_url):
                deleted_count += 1

        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} clip thumbnail(s) from GCS")

        return deleted_count


class StateDeleter:
    """Handles deletion of Redis pipeline state."""

    def __init__(self, video_id: str):
        self.video_id = video_id

    async def delete_all(self) -> Dict[str, any]:
        """Delete all Redis keys for video_id. Returns deletion results."""
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
        scope: str,
        moment_ids: Optional[list[str]] = None,
        force: bool = False,
    ) -> DeleteResult:
        """
        Delete a video or a subset of its resources based on scope.

        scope=all           — Full deletion (GCS + temp + Redis + DB record)
        scope=video_file    — Delete video/audio GCS files; nullify cloud_url in DB
        scope=moments       — Delete specific or all moments with clips/thumbnails
        scope=refined_moments — Delete only refined moments with their clips/thumbnails

        GCS errors are collected and result in status="partial".
        DB errors abort the operation and are raised to the caller.
        """
        start_time = time.time()
        logger.info(f"Starting deletion for video: {video_id}, scope: {scope}")

        result = DeleteResult(
            status="completed",
            video_id=video_id,
            deleted={},
            errors=[],
        )

        if scope == "all":
            await self._delete_all(video_id, force, result)
        elif scope == "video_file":
            await self._delete_video_file(video_id, result)
        elif scope == "moments":
            await self._delete_moments(video_id, moment_ids, force, result)
        elif scope == "refined_moments":
            await self._delete_refined_moments(video_id, result)

        result.duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            f"Deletion {result.status} for {video_id} (scope={scope}), "
            f"duration={result.duration_ms}ms, errors={len(result.errors)}"
        )
        return result

    # ------------------------------------------------------------------
    # scope=all
    # ------------------------------------------------------------------

    async def _delete_all(self, video_id: str, force: bool, result: DeleteResult) -> None:
        """Full deletion: GCS files, temp files, Redis state, DB record."""

        # 1. Pipeline check
        if not force:
            pipeline_status = await get_pipeline_status(video_id)
            if pipeline_status and pipeline_status.get("status") in ("processing", "pending", "queued"):
                result.status = "failed"
                result.errors.append(
                    f"Cannot delete video while pipeline is active "
                    f"(status: {pipeline_status.get('status')}). Use force=true to delete anyway."
                )
                return

        session_factory = get_session_factory()
        async with session_factory() as session:
            # 2. Fetch video with audio eagerly loaded
            video = await self._get_video(session, video_id)
            if video is None:
                result.status = "failed"
                result.errors.append(f"Video '{video_id}' not found in database.")
                return

            # 3. Collect clip IDs for thumbnail deletion
            clips_stmt = select(Clip).where(Clip.video_id == video.id)
            clips_result = await session.execute(clips_stmt)
            clips = list(clips_result.scalars().all())
            clip_ids = [c.id for c in clips]

            gcs_deleter = GCSDeleter(video_id)

            # 4a. Delete video GCS file by explicit cloud_url
            gcs_deleted: Dict[str, int] = {
                "video_files": 0,
                "audio_files": 0,
                "clip_files": 0,
                "thumbnail_files": 0,
                "clip_thumbnail_files": 0,
            }

            if video.cloud_url:
                if gcs_deleter.delete_video_file(video.cloud_url):
                    gcs_deleted["video_files"] += 1
                else:
                    msg = f"Failed to delete video GCS file: {video.cloud_url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            # 4b. Delete audio GCS file
            audio_url = self._resolve_audio_url(video, video_id)
            if audio_url:
                if gcs_deleter.delete_audio_file(audio_url):
                    gcs_deleted["audio_files"] += 1
                else:
                    msg = f"Failed to delete audio GCS file: {audio_url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            # 4c. Delete remaining clips and video thumbnail by prefix
            prefix_result = gcs_deleter.delete_all(
                skip_video=True,   # already deleted individually above
                skip_audio=True,   # already deleted individually above
                skip_clips=False,
                skip_thumbnails=False,
            )
            gcs_deleted["clip_files"] = prefix_result.get("clip_files", 0)
            gcs_deleted["thumbnail_files"] = prefix_result.get("thumbnail_files", 0)

            # 4d. Delete clip thumbnails by DB cloud_url (fixes Issue 3.3)
            clip_thumb_count = await gcs_deleter.delete_clip_thumbnails_for_clips(clip_ids, session)
            gcs_deleted["clip_thumbnail_files"] = clip_thumb_count

            result.deleted["gcs"] = gcs_deleted

            # 5. Delete temp files
            try:
                from app.services.temp_file_manager import cleanup_video as cleanup_temp_video
                temp_result = await cleanup_temp_video(video_id)
                result.deleted["temp"] = temp_result
            except Exception as e:
                msg = f"Temp file cleanup failed: {e}"
                logger.error(msg)
                result.errors.append(msg)
                result.status = "partial"

            # 6. Delete Redis state
            try:
                state_deleter = StateDeleter(video_id)
                state_result = await state_deleter.delete_all()
                result.deleted["redis_keys"] = state_result["redis_keys"]
            except Exception as e:
                msg = f"Redis deletion failed: {e}"
                logger.error(msg)
                result.errors.append(msg)
                result.status = "partial"

            # 7. Delete video DB record (CASCADE removes transcript, moments, clips, thumbnails, history)
            deleted = await video_db_repository.delete_by_id(session, video.id)
            await session.commit()
            result.deleted["database"] = deleted
            logger.info(
                f"Deleted video {video_id} from database "
                f"(CASCADE removed all related records)"
            )

    # ------------------------------------------------------------------
    # scope=video_file
    # ------------------------------------------------------------------

    async def _delete_video_file(self, video_id: str, result: DeleteResult) -> None:
        """Delete video + audio GCS files; nullify cloud_url on video and audio records."""

        session_factory = get_session_factory()
        async with session_factory() as session:
            # 1. Fetch video with audio eagerly loaded
            video = await self._get_video(session, video_id)
            if video is None:
                result.status = "failed"
                result.errors.append(f"Video '{video_id}' not found in database.")
                return

            gcs_deleter = GCSDeleter(video_id)
            gcs_deleted = {"video_files": 0, "audio_files": 0}

            # 2. Delete video GCS file
            if video.cloud_url:
                if gcs_deleter.delete_video_file(video.cloud_url):
                    gcs_deleted["video_files"] += 1
                else:
                    msg = f"Failed to delete video GCS file: {video.cloud_url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            # 3. Delete audio GCS file
            audio_url = self._resolve_audio_url(video, video_id)
            if audio_url:
                if gcs_deleter.delete_audio_file(audio_url):
                    gcs_deleted["audio_files"] += 1
                else:
                    msg = f"Failed to delete audio GCS file: {audio_url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            result.deleted["gcs"] = gcs_deleted

            # 4. Nullify video.cloud_url in DB
            video.cloud_url = None

            # 5. Nullify audio.cloud_url in DB if Audio record exists
            if video.audio is not None:
                video.audio.cloud_url = None

            # 6. Delete temp video and audio files
            try:
                from app.services.temp_file_manager import cleanup_video as cleanup_temp_video
                temp_result = await cleanup_temp_video(video_id)
                result.deleted["temp"] = temp_result
            except Exception as e:
                msg = f"Temp file cleanup failed: {e}"
                logger.error(msg)
                result.errors.append(msg)
                result.status = "partial"

            # 7. Commit DB changes
            await session.commit()
            result.deleted["database"] = {"cloud_url_nullified": True}
            logger.info(f"Removed GCS video/audio files for {video_id}; cloud_url set to NULL in DB")

    # ------------------------------------------------------------------
    # scope=moments
    # ------------------------------------------------------------------

    async def _delete_moments(
        self,
        video_id: str,
        moment_ids: Optional[list[str]],
        force: bool,
        result: DeleteResult,
    ) -> None:
        """Delete specific or all moments with their clips and clip thumbnails."""

        # 1. Pipeline check
        if not force:
            pipeline_status = await get_pipeline_status(video_id)
            if pipeline_status and pipeline_status.get("status") in ("processing", "pending", "queued"):
                result.status = "failed"
                result.errors.append(
                    f"Cannot delete moments while pipeline is active "
                    f"(status: {pipeline_status.get('status')}). Use force=true to delete anyway."
                )
                return

        session_factory = get_session_factory()
        async with session_factory() as session:
            # 2. Verify video exists
            video = await self._get_video(session, video_id)
            if video is None:
                result.status = "failed"
                result.errors.append(f"Video '{video_id}' not found in database.")
                return

            # 3. Resolve target moments
            target_moments = await self._resolve_target_moments(session, video.id, moment_ids)

            if not target_moments:
                result.deleted["moments"] = 0
                result.deleted["gcs"] = {"clip_files": 0, "clip_thumbnail_files": 0}
                logger.info(f"No moments to delete for video {video_id}")
                return

            # 4. Collect GCS URLs before any DB deletion
            clip_urls: list[str] = []
            thumbnail_urls: list[str] = []
            for moment in target_moments:
                if moment.clip and moment.clip.cloud_url:
                    clip_urls.append(moment.clip.cloud_url)
                if moment.clip and moment.clip.thumbnails:
                    for thumb in moment.clip.thumbnails:
                        if thumb.cloud_url:
                            thumbnail_urls.append(thumb.cloud_url)

            gcs_deleter = GCSDeleter(video_id)
            gcs_deleted = {"clip_files": 0, "clip_thumbnail_files": 0}

            # 5. Delete clip GCS files
            for url in clip_urls:
                if gcs_deleter.delete_by_url(url):
                    gcs_deleted["clip_files"] += 1
                else:
                    msg = f"Failed to delete clip GCS file: {url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            # 6. Delete clip thumbnail GCS files
            for url in thumbnail_urls:
                if gcs_deleter.delete_by_url(url):
                    gcs_deleted["clip_thumbnail_files"] += 1
                else:
                    msg = f"Failed to delete clip thumbnail GCS file: {url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            result.deleted["gcs"] = gcs_deleted

            # 7. Delete moment DB records (CASCADE handles clips, clip thumbnails, children)
            target_ids = [m.id for m in target_moments]
            del_stmt = delete(Moment).where(Moment.id.in_(target_ids))
            del_result = await session.execute(del_stmt)
            await session.commit()

            result.deleted["moments"] = del_result.rowcount
            logger.info(
                f"Deleted {del_result.rowcount} moment(s) for video {video_id} "
                f"(CASCADE removed clips and thumbnails)"
            )

    # ------------------------------------------------------------------
    # scope=refined_moments
    # ------------------------------------------------------------------

    async def _delete_refined_moments(self, video_id: str, result: DeleteResult) -> None:
        """Delete all refined moments with their clips and clip thumbnails."""

        session_factory = get_session_factory()
        async with session_factory() as session:
            # 1. Verify video exists
            video = await self._get_video(session, video_id)
            if video is None:
                result.status = "failed"
                result.errors.append(f"Video '{video_id}' not found in database.")
                return

            # 2. Query all refined moments with clips and thumbnails eagerly loaded
            stmt = (
                select(Moment)
                .where(
                    Moment.video_id == video.id,
                    Moment.is_refined == True,  # noqa: E712
                )
                .options(
                    selectinload(Moment.clip).selectinload(Clip.thumbnails),
                )
            )
            moments_result = await session.execute(stmt)
            refined_moments = list(moments_result.scalars().all())

            if not refined_moments:
                result.deleted["moments"] = 0
                result.deleted["gcs"] = {"clip_files": 0, "clip_thumbnail_files": 0}
                logger.info(f"No refined moments found for video {video_id}")
                return

            # 3. Collect GCS URLs before any DB deletion
            clip_urls: list[str] = []
            thumbnail_urls: list[str] = []
            for moment in refined_moments:
                if moment.clip and moment.clip.cloud_url:
                    clip_urls.append(moment.clip.cloud_url)
                if moment.clip and moment.clip.thumbnails:
                    for thumb in moment.clip.thumbnails:
                        if thumb.cloud_url:
                            thumbnail_urls.append(thumb.cloud_url)

            gcs_deleter = GCSDeleter(video_id)
            gcs_deleted = {"clip_files": 0, "clip_thumbnail_files": 0}

            # 4. Delete clip GCS files
            for url in clip_urls:
                if gcs_deleter.delete_by_url(url):
                    gcs_deleted["clip_files"] += 1
                else:
                    msg = f"Failed to delete refined moment clip GCS file: {url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            # 5. Delete clip thumbnail GCS files
            for url in thumbnail_urls:
                if gcs_deleter.delete_by_url(url):
                    gcs_deleted["clip_thumbnail_files"] += 1
                else:
                    msg = f"Failed to delete refined moment clip thumbnail GCS file: {url}"
                    logger.warning(msg)
                    result.errors.append(msg)
                    result.status = "partial"

            result.deleted["gcs"] = gcs_deleted

            # 6. Delete refined moment DB records (CASCADE handles clips + thumbnails)
            refined_ids = [m.id for m in refined_moments]
            del_stmt = delete(Moment).where(Moment.id.in_(refined_ids))
            del_result = await session.execute(del_stmt)
            await session.commit()

            result.deleted["moments"] = del_result.rowcount
            logger.info(
                f"Deleted {del_result.rowcount} refined moment(s) for video {video_id} "
                f"(CASCADE removed their clips and thumbnails)"
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_video(self, session: AsyncSession, video_id: str) -> Optional[Video]:
        """
        Fetch the Video record by string identifier with the audio relationship
        eagerly loaded (needed for audio cloud_url in all scopes).
        """
        stmt = (
            select(Video)
            .where(Video.identifier == video_id)
            .options(selectinload(Video.audio))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    def _resolve_audio_url(self, video: Video, video_id: str) -> Optional[str]:
        """
        Return the audio GCS path from the Audio DB record if it exists,
        otherwise fall back to the convention-based path for videos uploaded
        before Phase 2 was deployed.
        """
        if video.audio and video.audio.cloud_url:
            return video.audio.cloud_url
        # Convention fallback: audio/{video_id}/{video_id}.wav
        fallback = f"{self.settings.gcs_audio_prefix}{video_id}/{video_id}.wav"
        logger.debug(f"No Audio DB record found for {video_id}; using convention path: {fallback}")
        return fallback

    async def _resolve_target_moments(
        self,
        session: AsyncSession,
        video_db_id: int,
        moment_ids: Optional[list[str]],
    ) -> list[Moment]:
        """
        Resolve the list of Moment ORM objects to be deleted.

        If moment_ids is provided, fetches those specific moments by string identifier.
        For each root moment (is_refined=False) in that set, also fetches its children
        (is_refined=True, parent_id=root.id) and adds them.

        If moment_ids is None, returns all moments for the video.

        All returned moments have clip and clip.thumbnails eagerly loaded.
        """
        if moment_ids is None:
            # All moments for the video
            stmt = (
                select(Moment)
                .where(Moment.video_id == video_db_id)
                .options(
                    selectinload(Moment.clip).selectinload(Clip.thumbnails),
                )
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        # Specific moments by string identifier
        stmt = (
            select(Moment)
            .where(
                Moment.video_id == video_db_id,
                Moment.identifier.in_(moment_ids),
            )
            .options(
                selectinload(Moment.clip).selectinload(Clip.thumbnails),
            )
        )
        result = await session.execute(stmt)
        requested = list(result.scalars().all())

        # For each root moment in the requested set, fetch its refined children
        root_ids = [m.id for m in requested if not m.is_refined]
        if root_ids:
            children_stmt = (
                select(Moment)
                .where(
                    Moment.parent_id.in_(root_ids),
                    Moment.is_refined == True,  # noqa: E712
                )
                .options(
                    selectinload(Moment.clip).selectinload(Clip.thumbnails),
                )
            )
            children_result = await session.execute(children_stmt)
            children = list(children_result.scalars().all())

            # Merge, avoiding duplicates (a child may have been explicitly requested too)
            existing_ids = {m.id for m in requested}
            for child in children:
                if child.id not in existing_ids:
                    requested.append(child)
                    existing_ids.add(child.id)

        return requested
