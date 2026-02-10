# VideoMoments: Cloud Storage & Database Migration Plan

**Document Status:** Complete  
**Created:** February 7, 2026  
**Target Stack:** PostgreSQL 15+ (Database) + Google Cloud Storage (Files)  
**Schema Reference:** `database/SCHEMA.md`

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current State Analysis](#current-state-analysis)
3. [Target State](#target-state)
4. [Migration Overview](#migration-overview)
5. [Phase 1: Database Foundation](#phase-1-database-foundation)
6. [Phase 2: Videos to Cloud + Database](#phase-2-videos-to-cloud--database)
7. [Phase 3: Video Streaming from Cloud](#phase-3-video-streaming-from-cloud)
8. [Phase 4: Transcripts to Database](#phase-4-transcripts-to-database)
9. [Phase 5: Prompts & Generation Configs to Database](#phase-5-prompts--generation-configs-to-database)
10. [Phase 6: Moments to Database](#phase-6-moments-to-database)
11. [Phase 7: Clips to Cloud + Database](#phase-7-clips-to-cloud--database)
12. [Phase 8: Thumbnails to Cloud + Database](#phase-8-thumbnails-to-cloud--database)
13. [Phase 9: Pipeline History to Database](#phase-9-pipeline-history-to-database)
14. [Phase 10: URL Registry Elimination](#phase-10-url-registry-elimination)
15. [Phase 11: Temp File Management & Cleanup Scheduler](#phase-11-temp-file-management--cleanup-scheduler)
16. [Phase 12: Final Cleanup & Legacy Removal](#phase-12-final-cleanup--legacy-removal)
17. [Dependency Graph](#dependency-graph)
18. [Risk & Rollback Strategy](#risk--rollback-strategy)

---

## Executive Summary

This plan migrates VideoMoments from **local file storage + JSON persistence** to **Google Cloud Storage + PostgreSQL**. The migration is broken into 12 phases, each designed to be an independent, AI-agent-executable task. Phases are ordered by dependency -- each phase builds on completed prior phases, and every phase leaves the application in a working state.

**Key Principles:**
- Each phase is independently testable and deployable
- No data loss -- dual-write where needed during transition
- Application remains functional after every phase
- Each phase is scoped for a single AI coding session

---

## Current State Analysis

### Data Storage Map

| Data | Current Storage | Format | Location |
|------|----------------|--------|----------|
| Videos | Local filesystem | `.mp4`, `.mov`, `.avi`, `.mkv` | `static/videos/` |
| Moments | Local JSON files | `.json` per video | `static/moments/{video_id}.json` |
| Transcripts | Local JSON files | `.json` per video | `static/transcripts/{video_id}.json` |
| Audio | Local filesystem | `.wav` | `static/audios/{video_id}.wav` |
| Clips | Local filesystem | `.mp4` | `static/moment_clips/{stem}_{moment_id}_clip.mp4` |
| Thumbnails | Local filesystem | `.jpg` | `static/thumbnails/{video_filename}.jpg` |
| URL Registry | Local JSON file | Single `.json` | `static/url_registry.json` |
| Pipeline History | Redis | Hash/Stream | Redis keys |
| Job Status | Redis | Hash | Redis keys |

### Existing Cloud Integration (Partial)

Already in place via `GCSUploader` (`app/services/pipeline/upload_service.py`):
- Audio files uploaded to GCS for remote transcription (Parakeet)
- Clips uploaded to GCS for AI refinement (Qwen3-VL-FP8)
- Signed URL generation for both
- GCS downloader exists (`app/services/gcs_downloader.py`) for downloading videos from URLs/GCS URIs

### Key Files That Will Change

| File | Purpose | Impact |
|------|---------|--------|
| `app/core/config.py` | Settings | Add DB config, GCS video prefix |
| `app/main.py` | App setup | Add DB init, remove static mounts |
| `app/utils/video.py` | Video lookup | Replace filesystem scan with DB query |
| `app/services/moments_service.py` | Moments I/O | Replace JSON with DB |
| `app/services/transcript_service.py` | Transcript I/O | Replace JSON with DB |
| `app/services/audio_service.py` | Audio extraction | Use temp directory |
| `app/services/video_clipping_service.py` | Clip creation | Use temp dir + GCS upload |
| `app/services/thumbnail_service.py` | Thumbnail creation | Use temp dir + GCS upload |
| `app/api/endpoints/videos.py` | Video streaming | Return GCS signed URLs |
| `app/api/endpoints/moments.py` | Moments CRUD | Use DB repository |
| `app/api/endpoints/transcripts.py` | Transcript endpoints | Use DB repository |
| `app/api/endpoints/clips.py` | Clip endpoints | Use DB repository |
| `app/repositories/base.py` | Base repo (JSON) | Replace with SQLAlchemy base |
| `app/repositories/moments_repository.py` | Moments repo | Rewrite for PostgreSQL |
| `app/repositories/transcript_repository.py` | Transcript repo | Rewrite for PostgreSQL |
| `app/services/pipeline/orchestrator.py` | Pipeline engine | Update all file references |
| `app/services/pipeline/upload_service.py` | GCS upload | Add video upload capability |
| `app/services/url_registry.py` | URL→ID mapping | Replace with DB query |
| `app/services/video_delete_service.py` | Deletion logic | Update for cloud + DB |

---

## Target State

### After Migration Complete

| Data | Storage | Format | Reference |
|------|---------|--------|-----------|
| Videos | GCS | `.mp4` | `videos.cloud_url` in DB |
| Moments | PostgreSQL | Table rows | `moments` table |
| Transcripts | PostgreSQL | Table rows + JSONB | `transcripts` table |
| Audio | Temp folder only | `.wav` (ephemeral) | Deleted after processing |
| Clips | GCS | `.mp4` | `clips.cloud_url` in DB |
| Thumbnails | GCS | `.jpg` | `thumbnails.cloud_url` in DB |
| URL Registry | Eliminated | N/A | Use `videos.source_url` column |
| Pipeline History | PostgreSQL | Table rows | `pipeline_history` table |
| Prompts | PostgreSQL | Table rows | `prompts` table |
| Generation Configs | PostgreSQL | Table rows | `generation_configs` table |

### Architecture After Migration

```
┌──────────────────────────────────────────────────────────────────┐
│                        API Layer (Endpoints)                      │
│  Returns GCS signed URLs for video/clip/thumbnail streaming       │
│  Returns DB data for moments/transcripts/pipeline history         │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Service Layer                                │
│  Video processing: download to temp → process → upload to GCS     │
│  Data operations: read/write PostgreSQL via repositories          │
└────────────┬──────────────────────────────────┬─────────────────┘
             │                                  │
             ▼                                  ▼
┌────────────────────────┐         ┌────────────────────────────┐
│   PostgreSQL Database   │         │  Google Cloud Storage       │
│   ─────────────────     │         │  ──────────────────         │
│   videos (metadata)     │         │  videos/*.mp4               │
│   transcripts           │         │  clips/*.mp4                │
│   moments               │         │  thumbnails/*.jpg           │
│   clips (metadata)      │         │                             │
│   thumbnails (metadata) │         │                             │
│   prompts               │         │                             │
│   generation_configs    │         │                             │
│   pipeline_history      │         │                             │
└────────────────────────┘         └────────────────────────────┘
             │
             ▼
┌────────────────────────┐         ┌────────────────────────────┐
│       Redis             │         │  Temp Directory (local)     │
│   ─────────────────     │         │  ──────────────────         │
│   Pipeline status       │         │  Processing workspace       │
│   Job locks             │         │  Cleaned every 24 hours     │
│   Model configs         │         │                             │
│   Stream messages       │         │                             │
└────────────────────────┘         └────────────────────────────┘
```

---

## Migration Overview

### Phase Dependency Order

```
Phase 1: Database Foundation
    │
    ▼
Phase 2: Videos → Cloud + DB         ← Base entity (all FKs depend on this)
    │
    ├──► Phase 3: Video Streaming     ← Can run after Phase 2
    │
    ├──► Phase 4: Transcripts → DB    ← Depends on videos table
    │         │
    │         ▼
    │    Phase 5: Prompts & Configs   ← Depends on transcripts table
    │         │
    │         ▼
    │    Phase 6: Moments → DB        ← Depends on videos + generation_configs
    │         │
    │         ▼
    │    Phase 7: Clips → Cloud + DB  ← Depends on moments + videos
    │         │
    │         ▼
    │    Phase 8: Thumbnails          ← Depends on videos + clips
    │
    ├──► Phase 9: Pipeline History    ← Depends on videos + generation_configs
    │
    ├──► Phase 10: URL Registry       ← Depends on videos table
    │
    ▼
Phase 11: Temp File Management        ← After all processing migrated
    │
    ▼
Phase 12: Final Cleanup               ← Last step
```

### Estimated Effort Per Phase

| Phase | Description | Complexity | Files Changed |
|-------|-------------|------------|---------------|
| 1 | Database Foundation | Medium | 5-7 new files |
| 2 | Videos → Cloud + DB | High | 8-10 files |
| 3 | Video Streaming from Cloud | Medium | 3-4 files |
| 4 | Transcripts → DB | Medium | 5-6 files |
| 5 | Prompts & Configs → DB | Medium | 4-5 new files |
| 6 | Moments → DB | High | 6-8 files |
| 7 | Clips → Cloud + DB | Medium | 5-6 files |
| 8 | Thumbnails → Cloud + DB | Low | 3-4 files |
| 9 | Pipeline History → DB | Medium | 4-5 files |
| 10 | URL Registry Elimination | Low | 3-4 files |
| 11 | Temp File Management | Medium | 5-6 files |
| 12 | Final Cleanup | Low | 10+ files (deletions) |

---

## Phase 1: Database Foundation

### Goal
Set up PostgreSQL connectivity, SQLAlchemy models, Alembic migrations, and the database session management layer. No application logic changes -- just the infrastructure.

### What Exactly To Do

1. **Install dependencies**: Add `sqlalchemy`, `asyncpg`, `alembic`, `psycopg2-binary` to `requirements.txt`

2. **Add database settings** to `app/core/config.py`:
   - `database_url` (PostgreSQL connection string)
   - `database_pool_size`, `database_max_overflow`, `database_pool_timeout`
   - `database_echo` (SQL logging for debug)

3. **Create database module** at `app/database/`:
   - `app/database/__init__.py`
   - `app/database/session.py` -- async SQLAlchemy engine + session factory using `asyncpg`
   - `app/database/base.py` -- SQLAlchemy `DeclarativeBase` class with common columns
   - `app/database/dependencies.py` -- FastAPI dependency `get_db()` that yields async sessions

4. **Create SQLAlchemy ORM models** at `app/database/models/`:
   - `app/database/models/__init__.py`
   - `app/database/models/video.py` -- `Video` model (matches `database/SCHEMA.md` Table 1)
   - `app/database/models/transcript.py` -- `Transcript` model (Table 2)
   - `app/database/models/moment.py` -- `Moment` model (Table 3)
   - `app/database/models/prompt.py` -- `Prompt` model (Table 4)
   - `app/database/models/generation_config.py` -- `GenerationConfig` model (Table 5)
   - `app/database/models/clip.py` -- `Clip` model (Table 6)
   - `app/database/models/thumbnail.py` -- `Thumbnail` model (Table 7)
   - `app/database/models/pipeline_history.py` -- `PipelineHistory` model (Table 8)

5. **Initialize Alembic**:
   - Run `alembic init alembic` in `moments-backend/`
   - Configure `alembic.ini` with database URL
   - Configure `alembic/env.py` to import all models and use async engine
   - Generate initial migration: `alembic revision --autogenerate -m "create_all_tables"`
   - Run migration: `alembic upgrade head`

6. **Add database lifecycle to `app/main.py`**:
   - Import and initialize database engine on startup
   - Add database health check to `/health` endpoint
   - Close engine on shutdown

7. **Verify**: Run the application, check `/health` returns `"database": "connected"`, verify all tables exist in PostgreSQL

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| MODIFY | `requirements.txt` | Add SQLAlchemy, asyncpg, alembic |
| MODIFY | `app/core/config.py` | Add database settings |
| CREATE | `app/database/__init__.py` | Database package |
| CREATE | `app/database/session.py` | Engine + session factory |
| CREATE | `app/database/base.py` | Declarative base |
| CREATE | `app/database/dependencies.py` | FastAPI dependency |
| CREATE | `app/database/models/__init__.py` | Models package |
| CREATE | `app/database/models/video.py` | Video ORM model |
| CREATE | `app/database/models/transcript.py` | Transcript ORM model |
| CREATE | `app/database/models/moment.py` | Moment ORM model |
| CREATE | `app/database/models/prompt.py` | Prompt ORM model |
| CREATE | `app/database/models/generation_config.py` | GenerationConfig ORM model |
| CREATE | `app/database/models/clip.py` | Clip ORM model |
| CREATE | `app/database/models/thumbnail.py` | Thumbnail ORM model |
| CREATE | `app/database/models/pipeline_history.py` | PipelineHistory ORM model |
| CREATE | `alembic.ini` | Alembic config |
| CREATE | `alembic/env.py` | Alembic environment |
| MODIFY | `app/main.py` | Add DB startup/shutdown |

### Verification Checklist

- [ ] PostgreSQL is running and accessible
- [ ] `alembic upgrade head` creates all 8 tables
- [ ] `/health` endpoint returns `"database": "connected"`
- [ ] All tables have correct columns, types, constraints, and indexes as per `database/SCHEMA.md`
- [ ] Foreign key relationships are correct
- [ ] Application starts without errors

### AI Agent Prompt

```
CONTEXT:
I'm building a FastAPI application called VideoMoments at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/. I need to set up PostgreSQL database infrastructure. The application currently uses no database -- all data is stored in JSON files and Redis.

SCHEMA REFERENCE:
Read the file database/SCHEMA.md (path: /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/database/SCHEMA.md) -- it contains the complete schema definition for all 8 tables: Videos, Transcripts, Moments, Prompts, Generation Configs, Clips, Thumbnails, and Pipeline History.

EXISTING CONFIG:
Read app/core/config.py for the current settings pattern (Pydantic Settings).
Read app/main.py for the current startup/shutdown pattern.
Read requirements.txt for current dependencies.

TASK:
1. Add these dependencies to requirements.txt: sqlalchemy[asyncio], asyncpg, alembic, psycopg2-binary (for Alembic migrations which need sync driver). Use latest versions.

2. Add database settings to app/core/config.py:
   - database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/videomoments"
   - database_sync_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/videomoments"  (for Alembic)
   - database_pool_size: int = 5
   - database_max_overflow: int = 10
   - database_pool_timeout: int = 30
   - database_echo: bool = False

3. Create app/database/ package with:
   - __init__.py (export key objects)
   - session.py: Create async SQLAlchemy engine using create_async_engine with the config's database_url. Create async_sessionmaker. Provide get_async_session() async generator. Provide init_db() and close_db() functions.
   - base.py: Create DeclarativeBase class.
   - dependencies.py: Create FastAPI dependency get_db() that yields an AsyncSession.

4. Create app/database/models/ with one file per table, matching SCHEMA.md exactly:
   - video.py: Video model with all columns, indexes, and relationships from Table 1
   - transcript.py: Transcript model from Table 2 (JSONB columns for word_timestamps and segment_timestamps)
   - moment.py: Moment model from Table 3 (self-referencing FK via parent_id)
   - prompt.py: Prompt model from Table 4
   - generation_config.py: GenerationConfig model from Table 5
   - clip.py: Clip model from Table 6
   - thumbnail.py: Thumbnail model from Table 7 (CHECK constraint for video_id XOR clip_id)
   - pipeline_history.py: PipelineHistory model from Table 8
   - __init__.py: Import all models so Alembic can discover them

   IMPORTANT for models:
   - Use JSONB type from sqlalchemy.dialects.postgresql for JSONB columns
   - Implement all CHECK constraints from the schema
   - Implement all indexes including partial unique indexes for thumbnails
   - Implement GIN index on transcripts.full_text for full-text search
   - All ON DELETE behaviors (CASCADE, SET NULL) must match the schema
   - Use server_default=func.now() for created_at columns

5. Initialize Alembic:
   - Run `alembic init alembic` in the moments-backend directory
   - Configure alembic.ini to use database_sync_url from settings
   - Configure alembic/env.py to:
     a. Import all models from app.database.models
     b. Use the Base.metadata for autogenerate
     c. Read sqlalchemy.url from app settings (not hardcoded in alembic.ini)
   - Generate migration: alembic revision --autogenerate -m "create_all_tables"
   - Verify the generated migration file has all 8 tables with correct columns

6. Update app/main.py:
   - Import init_db, close_db from app.database.session
   - Call await init_db() in startup_event()
   - Call await close_db() in shutdown_event()
   - Update /health endpoint to check database connectivity (try a simple SELECT 1 query)

7. Run `alembic upgrade head` to create all tables

DO NOT modify any existing application logic, endpoints, or services. This phase is ONLY about database infrastructure setup. The existing JSON-based storage should continue to work unchanged.

Verify the application starts cleanly with both Redis and PostgreSQL, and /health shows both as connected.
```

---

## Phase 2: Videos to Cloud + Database

### Goal
Upload all existing local videos to GCS and populate the `videos` database table. Modify the video download pipeline to upload videos to GCS and store metadata in the database. After this phase, the `videos` table is the source of truth for what videos exist.

### What Exactly To Do

1. **Add GCS video prefix** to config:
   - `gcs_videos_prefix: str = "videos/"` in `app/core/config.py`

2. **Extend `GCSUploader`** in `app/services/pipeline/upload_service.py`:
   - Add `upload_video()` method: uploads a local video file to `gs://bucket/videos/{identifier}/{filename}` and returns `(gcs_path, signed_url)`
   - Add `get_video_signed_url()` method: generates a signed URL for an existing video in GCS

3. **Create database video repository** at `app/repositories/video_db_repository.py`:
   - `create(identifier, cloud_url, source_url, title, duration, file_size, codec_info)` -- inserts row, returns Video model
   - `get_by_identifier(identifier)` -- returns Video or None
   - `get_by_id(id)` -- returns Video or None
   - `get_by_source_url(source_url)` -- for duplicate detection
   - `list_all()` -- returns all videos
   - `delete(id)` -- deletes video row (cascades)
   - `update(id, **fields)` -- partial update
   - All methods accept an `AsyncSession`

4. **Create a one-time migration script** at `scripts/migrate_videos_to_cloud.py`:
   - Scans `static/videos/` for all video files
   - For each video:
     a. Extract metadata using ffprobe (duration, codecs, resolution, frame rate, file size)
     b. Upload to GCS: `gs://bucket/videos/{video_stem}/{video_stem}.mp4`
     c. Insert row into `videos` table with `identifier = video_stem`, `cloud_url = gs://...`, metadata columns filled
   - Skip if video with same identifier already exists in DB
   - Print progress and summary

5. **Modify `execute_video_download()`** in `app/services/pipeline/orchestrator.py`:
   - After downloading a video to local temp path:
     a. Extract metadata via ffprobe
     b. Upload to GCS using `upload_video()`
     c. Insert into `videos` table
     d. Keep local copy for ongoing pipeline processing (will be cleaned up in Phase 11)
   - If video already exists in DB (by `source_url` or `identifier`), skip download and use existing `cloud_url`

6. **Modify `app/api/endpoints/videos.py`**:
   - `GET /api/videos` -- query `videos` DB table instead of scanning filesystem
   - `GET /api/videos/{video_id}` -- query by `identifier` from DB instead of filesystem lookup
   - `GET /api/videos/{video_id}/stream` -- keep working for now (Phase 3 will change this)
   - Keep the filesystem-based streaming as a fallback during transition

7. **Modify `app/utils/video.py`**:
   - Add `get_video_from_db(identifier, session)` -- queries DB for video record
   - Keep existing `get_video_files()` and `get_video_by_id()` as fallback during transition
   - Add deprecation warnings to filesystem-based functions

8. **Run migration script** to populate the database with all existing videos

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| MODIFY | `app/core/config.py` | Add gcs_videos_prefix |
| MODIFY | `app/services/pipeline/upload_service.py` | Add upload_video(), get_video_signed_url() |
| CREATE | `app/repositories/video_db_repository.py` | Database CRUD for videos |
| CREATE | `scripts/migrate_videos_to_cloud.py` | One-time data migration |
| MODIFY | `app/services/pipeline/orchestrator.py` | Upload to GCS + insert to DB after download |
| MODIFY | `app/api/endpoints/videos.py` | Query DB for video list/details |
| MODIFY | `app/utils/video.py` | Add DB-backed lookup, deprecate filesystem |

### Verification Checklist

- [ ] All existing videos appear in `videos` table with correct metadata
- [ ] All existing videos uploaded to GCS at correct paths
- [ ] `GET /api/videos` returns videos from database
- [ ] `GET /api/videos/{id}` returns video metadata from database
- [ ] Video streaming still works (local fallback)
- [ ] New pipeline downloads create DB entries + upload to GCS
- [ ] Duplicate URL detection works via `source_url` column

### AI Agent Prompt

```
CONTEXT:
I'm migrating the VideoMoments application from local file storage to GCS + PostgreSQL. Phase 1 (database foundation) is complete -- all 8 tables exist in PostgreSQL with SQLAlchemy models at app/database/models/.

The application is at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- Videos stored locally in static/videos/ directory
- Videos identified by filename stem (e.g., "motivation.mp4" → video_id: "motivation")
- Video listing done by scanning filesystem in app/utils/video.py (get_video_files())
- Video metadata extracted on-the-fly using cv2 or ffprobe
- GCS upload already works for audio and clips via app/services/pipeline/upload_service.py (GCSUploader class)
- GCS downloader exists at app/services/gcs_downloader.py

SCHEMA REFERENCE:
Read database/SCHEMA.md for the videos table definition (Table 1). Key columns: id, identifier, source_url, cloud_url, title, duration_seconds, file_size_kb, video_codec, audio_codec, resolution, frame_rate, created_at.

FILES TO READ FIRST:
- app/core/config.py (settings, GCS config)
- app/services/pipeline/upload_service.py (existing GCS upload code)
- app/utils/video.py (current filesystem-based video lookup)
- app/api/endpoints/videos.py (current video endpoints)
- app/services/pipeline/orchestrator.py (video download in pipeline, especially execute_video_download)
- app/database/models/video.py (the SQLAlchemy model you'll use)
- app/database/session.py (how to get database sessions)
- app/database/dependencies.py (FastAPI dependency for DB session)

TASK:
1. Add gcs_videos_prefix: str = "videos/" to config.py Settings class.

2. Extend GCSUploader in upload_service.py:
   - Add async method upload_video(self, local_path: Path, identifier: str) -> Tuple[str, str]: 
     Uploads to gs://bucket/videos/{identifier}/{filename}, returns (gcs_path, signed_url)
   - Add async method get_video_signed_url(self, identifier: str, filename: str) -> Optional[str]:
     Generates a signed URL for an existing video blob

3. Create app/repositories/video_db_repository.py with async methods:
   - async create(session, identifier, cloud_url, source_url=None, title=None, duration_seconds=None, file_size_kb=None, video_codec=None, audio_codec=None, resolution=None, frame_rate=None) -> Video
   - async get_by_identifier(session, identifier) -> Optional[Video]
   - async get_by_id(session, id) -> Optional[Video]
   - async get_by_source_url(session, source_url) -> Optional[Video]
   - async list_all(session) -> List[Video]
   - async delete_by_identifier(session, identifier) -> bool
   - async update(session, id, **fields) -> Optional[Video]
   Use SQLAlchemy async select/insert patterns.

4. Create scripts/migrate_videos_to_cloud.py:
   - Standalone async script (use asyncio.run)
   - Create its own database session and GCS uploader
   - Scan static/videos/ for all video files
   - For each video:
     a. Extract metadata using ffprobe subprocess (duration, video_codec, audio_codec, resolution, frame_rate, file_size)
     b. Upload to GCS at videos/{stem}/{filename}
     c. Insert into videos table (skip if identifier already exists)
   - Print progress: "Migrated {n}/{total}: {filename}"
   - Print summary at end

5. Modify execute_video_download() in orchestrator.py:
   - After downloading video to local path, extract metadata via ffprobe
   - Upload to GCS using upload_video()
   - Create database entry using video_db_repository.create()
   - Store the video's db id in the pipeline context for use by later stages
   - If a video with the same source_url already exists in DB, skip download, just return existing record

6. Modify app/api/endpoints/videos.py:
   - GET /api/videos: Query the videos table (list_all) instead of scanning filesystem. Return list of video objects with fields: id, identifier, title, duration_seconds, cloud_url, source_url, created_at, has_transcript (computed by checking if transcript exists), has_moments (computed by checking if moments exist).
   - GET /api/videos/{video_id}: Query by identifier from DB. Return video details.
   - GET /api/videos/{video_id}/stream: Keep the current local filesystem streaming code as-is for now (Phase 3 will replace this). Add a check: if video not found locally, try getting a signed URL from GCS and return a redirect.
   - Add the db session dependency (Depends(get_db)) to the modified endpoints.

7. In app/utils/video.py:
   - Add import warnings at the top
   - Add deprecation warnings to get_video_files() and get_video_by_id() 
   - Keep them functional (they're still used by stream endpoint as fallback)

IMPORTANT:
- Keep all existing functionality working. The local fallbacks must remain during transition.
- Use the get_db dependency from app/database/dependencies.py for endpoint DB access.
- For the migration script and orchestrator, create sessions directly using async_sessionmaker.
- Do NOT modify moments, transcripts, or other services -- only video-related code.
- Test by running the migration script, then checking GET /api/videos returns data from database.
```

---

## Phase 3: Video Streaming from Cloud

### Goal
Replace local video file serving with GCS signed URL streaming. The frontend will receive a signed URL that points directly to GCS, eliminating the need for the backend to proxy video bytes. For processing (FFmpeg operations), videos are downloaded to a temporary directory.

### What Exactly To Do

1. **Modify `GET /api/videos/{video_id}/stream`** in `app/api/endpoints/videos.py`:
   - Query DB for video by identifier
   - Generate a GCS signed URL for the video (use `get_video_signed_url()`)
   - Return a `RedirectResponse` (HTTP 302) to the signed URL
   - Remove local file streaming logic entirely
   - The signed URL expires after `gcs_signed_url_expiry_hours` (configurable, default 1 hour)

2. **Add video URL endpoint** in `app/api/endpoints/videos.py`:
   - `GET /api/videos/{video_id}/url` -- returns `{ "url": "<signed_url>", "expires_in_seconds": 3600 }`
   - This is useful for the frontend to get a fresh URL when the previous one expires

3. **Modify frontend `VideoPlayer.jsx`**:
   - The `getVideoStreamUrl()` still works because it calls `/api/videos/{id}/stream` which now redirects
   - HTML5 `<video>` element follows redirects automatically, so no code change needed
   - BUT: If the signed URL expires during playback, the video will fail. Add error handling:
     a. On video `error` event, try fetching a new URL from `/api/videos/{id}/url`
     b. Update the `src` attribute with the new URL
     c. Resume from the last `currentTime`

4. **Add temp directory for processing** to config:
   - `temp_processing_dir: Path = Path("temp/processing")` in `app/core/config.py`
   - Create helper function `get_temp_video_path(identifier: str) -> Path` in `app/utils/video.py`
   - This is used when FFmpeg needs a local file (audio extraction, clip creation)

5. **Modify video processing functions** to download from GCS when needed:
   - In `app/services/audio_service.py` -- `extract_audio_from_video()`:
     - Accept either a local `Path` or a `cloud_url` string
     - If cloud_url provided, download to temp dir first using `GCSDownloader`
     - Extract audio from the temp file
     - Keep the temp video file (cleanup handled by Phase 11)
   - In `app/services/video_clipping_service.py` -- `extract_video_clip()`:
     - Accept either a local `Path` or look up cloud_url from DB
     - If no local file exists, download from GCS to temp dir
     - Create clip from temp file

6. **Update pipeline orchestrator** to track temp file paths:
   - After video download/upload in `execute_video_download()`, store temp path in pipeline context
   - Pass temp path to audio extraction and clip extraction stages
   - Temp files remain until cleanup (Phase 11)

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| MODIFY | `app/core/config.py` | Add temp_processing_dir |
| MODIFY | `app/api/endpoints/videos.py` | Replace stream with redirect, add /url endpoint |
| MODIFY | `app/utils/video.py` | Add get_temp_video_path() |
| MODIFY | `app/services/audio_service.py` | Support cloud_url input |
| MODIFY | `app/services/video_clipping_service.py` | Support cloud_url input |
| MODIFY | `app/services/pipeline/orchestrator.py` | Track temp paths |
| MODIFY | `moments-frontend/src/components/VideoPlayer.jsx` | Handle URL expiry |

### Verification Checklist

- [ ] `GET /api/videos/{video_id}/stream` returns HTTP 302 redirect to GCS signed URL
- [ ] Frontend video playback works via GCS URL (video loads and plays)
- [ ] Video seeking works (GCS supports range requests natively)
- [ ] Audio extraction works with cloud video (downloads to temp, extracts)
- [ ] Clip extraction works with cloud video
- [ ] Pipeline still runs end-to-end with cloud video

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments from local file storage to GCS + PostgreSQL. Phase 1 (database) and Phase 2 (videos in DB + GCS) are complete. All videos are now in GCS and registered in the videos database table with cloud_url values.

Application is at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/.

CURRENT STATE:
- Videos exist in GCS at gs://bucket/videos/{identifier}/{filename}
- Videos table in DB has cloud_url for each video
- GCSUploader has get_video_signed_url() method
- Video streaming endpoint still serves from local filesystem
- Frontend uses getVideoStreamUrl(videoId) which hits /api/videos/{videoId}/stream

FILES TO READ FIRST:
- app/api/endpoints/videos.py (current stream endpoint with local file serving)
- app/services/pipeline/upload_service.py (GCSUploader with signed URL generation)
- app/services/audio_service.py (uses local video_path for ffmpeg)
- app/services/video_clipping_service.py (uses local video_path for ffmpeg)
- app/services/pipeline/orchestrator.py (pipeline stages that use video files)
- app/core/config.py (settings)
- app/utils/video.py (video path utilities)
- moments-frontend/src/components/VideoPlayer.jsx (video playback)
- moments-frontend/src/services/api.js (getVideoStreamUrl function)

TASK:
1. Add to config.py: temp_processing_dir: Path = Path("temp/processing")

2. Rewrite the stream_video endpoint in videos.py:
   - Remove all local file reading code (open(), FileResponse, StreamingResponse with file ranges)
   - Query the videos DB table for the video by identifier
   - If not found, return 404
   - Generate a signed URL using GCSUploader.get_video_signed_url()
   - Return RedirectResponse(url=signed_url, status_code=302)
   - The browser/video player will follow the redirect to GCS which natively supports range requests

3. Add a new endpoint GET /api/videos/{video_id}/url in videos.py:
   - Query DB for video
   - Generate signed URL
   - Return { "url": signed_url, "expires_in_seconds": int(settings.gcs_signed_url_expiry_hours * 3600) }
   - This lets the frontend refresh URLs when they expire

4. In app/utils/video.py, add:
   - get_temp_video_path(identifier: str) -> Path: Returns Path("temp/processing/{identifier}.mp4"), creates parent dirs
   - ensure_local_video(identifier: str, cloud_url: str) -> Path: Checks if file exists in temp dir; if not, downloads from GCS to temp dir; returns local path. Use GCSDownloader for the download.

5. Modify audio_service.py extract_audio_from_video():
   - Add optional parameter cloud_url: Optional[str] = None
   - If cloud_url is provided and local video_path doesn't exist, download from GCS to temp dir first
   - Use the local temp path for ffmpeg (ffmpeg cannot read from URLs directly for wav extraction with the current pipeline)
   - Rest of the function stays the same

6. Modify video_clipping_service.py extract_video_clip():
   - Add optional parameter cloud_url: Optional[str] = None
   - If cloud_url provided and local video_path doesn't exist, download to temp
   - Use local temp path for ffmpeg
   - After clip is extracted, upload clip to GCS (this already partially exists)

7. Update orchestrator.py:
   - In execute_video_download(): After download + GCS upload, store the local temp path in pipeline context
   - In execute_audio_extraction(): Use the temp path from context, pass cloud_url as fallback
   - In clip extraction stages: Use temp path from context, pass cloud_url as fallback
   - The temp video file should persist through the entire pipeline run (it's shared across stages)

8. Modify moments-frontend/src/components/VideoPlayer.jsx:
   - The <video> element's src uses getVideoStreamUrl(video.id) which calls /api/videos/{id}/stream
   - HTML5 video follows 302 redirects, so basic playback works without changes
   - BUT add error recovery for expired URLs:
     a. Add a handleVideoError callback on the video element's 'error' event
     b. When error occurs, call the new /api/videos/{video.id}/url endpoint to get a fresh URL
     c. Set the new URL as the video src
     d. Call video.load() and then video.play() from the saved currentTime
     e. Only retry once to avoid infinite loops
   - Keep changes minimal -- only add error handling, don't restructure the component

IMPORTANT:
- GCS natively supports HTTP Range requests, so video seeking works automatically via signed URLs
- The redirect approach means the backend does NOT proxy video bytes -- GCS serves directly
- Keep the temp files around -- Phase 11 will add automated cleanup
- Don't change any moments, transcripts, or other unrelated code
```

---

## Phase 4: Transcripts to Database

### Goal
Migrate transcript storage from JSON files (`static/transcripts/*.json`) to the PostgreSQL `transcripts` table. The transcript's full text goes in a `TEXT` column, and word/segment timestamps go in `JSONB` columns.

### What Exactly To Do

1. **Create database transcript repository** at `app/repositories/transcript_db_repository.py`:
   - `create(session, video_id, full_text, word_timestamps, segment_timestamps, language, number_of_words, number_of_segments, transcription_service, processing_time_seconds)` -- inserts row
   - `get_by_video_id(session, video_id)` -- returns Transcript or None
   - `get_by_video_identifier(session, identifier)` -- joins with videos table to find by identifier
   - `exists(session, video_id)` -- returns bool
   - `delete_by_video_id(session, video_id)` -- deletes transcript

2. **Create one-time migration script** at `scripts/migrate_transcripts_to_db.py`:
   - Reads all JSON files from `static/transcripts/`
   - For each transcript JSON:
     a. Parse the JSON structure (has `text`, `word_timestamps[]`, `segments[]`)
     b. Look up the video in DB by identifier (filename stem)
     c. Insert into `transcripts` table with `video_id` FK
     d. Compute `number_of_words` from word_timestamps length
     e. Compute `number_of_segments` from segments length
   - Skip if transcript already exists for that video_id
   - Print progress and summary

3. **Modify `app/services/transcript_service.py`**:
   - Replace `load_transcript()` to query DB instead of reading JSON file
   - Replace `save_transcript()` to insert/update DB instead of writing JSON file
   - Keep function signatures compatible (accept audio_filename, map to video identifier internally)
   - Update `get_transcript_path()` to be deprecated (add warning)
   - Update `check_transcript_exists()` to query DB

4. **Modify `app/repositories/transcript_repository.py`**:
   - Replace the `BaseRepository` (file-based) implementation with DB-backed implementation
   - Or alternatively, redirect all calls to `transcript_db_repository.py`

5. **Modify `app/api/endpoints/transcripts.py`**:
   - `GET /api/videos/{video_id}/transcript` -- query DB instead of reading JSON
   - Response format should match what frontend expects: `{ "text": "...", "word_timestamps": [...], "segment_timestamps": [...] }`
   - Add `Depends(get_db)` for database session
   - `POST /api/videos/{video_id}/process-transcript` -- after transcription completes, save to DB (the transcript service already does the save, so this mostly works if transcript_service.save_transcript is updated)

6. **Update the pipeline orchestrator** transcript handling:
   - In `execute_transcription()`: After transcription completes, `save_transcript()` now writes to DB
   - Store the transcript's DB `id` in pipeline context for use by generation config creation

7. **Remove static mount** for transcripts in `app/main.py`:
   - Remove `app.mount("/static/transcripts", ...)` -- transcripts are now served from DB, not filesystem

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `app/repositories/transcript_db_repository.py` | DB CRUD for transcripts |
| CREATE | `scripts/migrate_transcripts_to_db.py` | One-time JSON → DB migration |
| MODIFY | `app/services/transcript_service.py` | Replace JSON I/O with DB ops |
| MODIFY | `app/repositories/transcript_repository.py` | Redirect to DB implementation |
| MODIFY | `app/api/endpoints/transcripts.py` | Query DB, add session dependency |
| MODIFY | `app/services/pipeline/orchestrator.py` | Track transcript DB id |
| MODIFY | `app/main.py` | Remove /static/transcripts mount |

### Verification Checklist

- [ ] Migration script runs and all existing transcripts are in the database
- [ ] `GET /api/videos/{video_id}/transcript` returns data from database
- [ ] Transcript processing (pipeline) saves to database
- [ ] Frontend captions display correctly with DB-sourced data
- [ ] Full-text search works via GIN index on `full_text` column
- [ ] No JSON files are read by any active code path

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments from JSON file storage to PostgreSQL. Phases 1-3 are complete: database is set up, videos are in GCS + DB, streaming works from cloud. Now I need to migrate transcripts from JSON files to the database.

Application is at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- Transcripts stored as JSON in static/transcripts/{video_id}.json
- JSON structure: { "text": "full transcript...", "word_timestamps": [{"word": "Hello", "start": 0.0, "end": 0.5}, ...], "segments": [{"text": "Hello world.", "start": 0.0, "end": 2.0}, ...], "language": "en" }
- transcript_service.py has load_transcript() and save_transcript() that read/write JSON files
- transcript_repository.py uses BaseRepository (file-based) for I/O
- The pipeline orchestrator calls transcript_service for transcription
- Frontend fetches GET /api/videos/{video_id}/transcript and uses segment_timestamps for captions

SCHEMA:
Read database/SCHEMA.md Table 2 (Transcripts). Columns: id, video_id (FK UNIQUE), full_text (TEXT), word_timestamps (JSONB), segment_timestamps (JSONB), language, number_of_words, number_of_segments, transcription_service, processing_time_seconds, created_at.

FILES TO READ FIRST:
- database/SCHEMA.md (Table 2 for transcript schema)
- app/services/transcript_service.py (current JSON load/save, see load_transcript() and save_transcript())
- app/repositories/transcript_repository.py (current file-based repo)
- app/repositories/base.py (BaseRepository with read_json/write_json)
- app/api/endpoints/transcripts.py (transcript API endpoints)
- app/services/pipeline/orchestrator.py (where transcription happens in pipeline)
- app/database/models/transcript.py (SQLAlchemy model)
- app/database/dependencies.py (get_db dependency)
- app/main.py (static file mounts)

TASK:
1. Create app/repositories/transcript_db_repository.py:
   - Async functions (not a class -- use module-level async functions that accept AsyncSession as first param):
     - async create(session, video_id, full_text, word_timestamps, segment_timestamps, ...) -> Transcript
     - async get_by_video_id(session, video_id: int) -> Optional[Transcript]
     - async get_by_video_identifier(session, identifier: str) -> Optional[Transcript] (joins videos table)
     - async exists_for_video(session, video_id: int) -> bool
     - async delete_by_video_id(session, video_id: int) -> bool
   - Use SQLAlchemy 2.0 style (select(), session.execute(), session.scalars())

2. Create scripts/migrate_transcripts_to_db.py:
   - Standalone async script
   - Scan static/transcripts/ for all .json files
   - For each file:
     a. Parse JSON (handle both "segments" and "segment_timestamps" keys -- the JSON may use either)
     b. Look up video in DB by identifier = filename stem (without .json)
     c. If video found and no transcript exists for it yet, insert transcript
     d. Map JSON fields: "text" → full_text, "word_timestamps" → word_timestamps, "segments"/"segment_timestamps" → segment_timestamps
     e. Count words and segments for the count columns
   - Skip files whose video doesn't exist in DB
   - Print progress

3. Modify app/services/transcript_service.py:
   - Replace load_transcript(audio_filename) internals:
     - Extract video identifier from audio_filename (stem)
     - Get a DB session
     - Call transcript_db_repository.get_by_video_identifier(session, identifier)
     - Return data in the same dict format the callers expect: {"text": ..., "word_timestamps": [...], "segment_timestamps": [...]}
   - Replace save_transcript(audio_filename, transcription_data) internals:
     - Extract video identifier from audio_filename
     - Look up video_id from videos table
     - Call transcript_db_repository.create()
     - Remove the JSON file write
   - Replace check_transcript_exists():
     - Query DB instead of checking file existence
   - Keep function signatures the same for backward compatibility
   - For getting DB sessions in service functions (not endpoints), use the async_sessionmaker directly from app.database.session

4. Update app/repositories/transcript_repository.py:
   - Can either gut the class and redirect to transcript_db_repository, or mark it deprecated
   - The important thing is that any code importing from this file still works

5. Update app/api/endpoints/transcripts.py:
   - GET /api/videos/{video_id}/transcript: Add Depends(get_db) for session. Query DB via transcript_db_repository. Return the transcript data in the expected format.
   - POST /api/videos/{video_id}/process-transcript: This triggers transcription and ultimately calls save_transcript() which now writes to DB. Verify the flow works.
   - GET /api/videos/{video_id}/transcription-status: This uses Redis for status -- no changes needed.

6. Update orchestrator.py execute_transcription():
   - After save_transcript() succeeds, query the DB to get the transcript's DB id
   - Store transcript_id in pipeline context (other phases will need it for generation_configs FK)

7. Remove the static/transcripts mount from app/main.py:
   - Delete the lines that mount /static/transcripts

IMPORTANT:
- The word_timestamps JSON format: [{"word": "Hello", "start": 0.0, "end": 0.5}, ...]
- The segment_timestamps JSON format: [{"text": "Hello world.", "start": 0.0, "end": 2.0}, ...]  
- These go into JSONB columns as-is (PostgreSQL handles JSON natively)
- Keep the existing JSON files on disk -- don't delete them. They serve as backups.
- Frontend expects the response to have "segment_timestamps" (not "segments") for captions
- The transcription service (Parakeet) returns results that get saved -- make sure that flow works end-to-end
```

---

## Phase 5: Prompts & Generation Configs to Database

### Goal
Implement the `prompts` and `generation_configs` tables with DB repositories. These are new tables -- no migration of existing data needed, but the pipeline must start creating records in these tables when generating moments.

### What Exactly To Do

1. **Create prompt repository** at `app/repositories/prompt_db_repository.py`:
   - `create_or_get(session, user_prompt, system_prompt)` -- computes SHA-256 hash, inserts if not exists, returns Prompt
   - `get_by_hash(session, prompt_hash)` -- lookup by hash
   - `get_by_id(session, id)` -- lookup by ID

2. **Create generation config repository** at `app/repositories/generation_config_db_repository.py`:
   - `create_or_get(session, prompt_id, model, operation_type, transcript_id, temperature, top_p, top_k, min_moment_length, max_moment_length, min_moments, max_moments)` -- computes config_hash (excluding transcript_id), inserts if not exists, returns GenerationConfig
   - `get_by_hash(session, config_hash)` -- lookup by hash
   - `get_by_id(session, id)` -- lookup by ID
   - Hash calculation: SHA-256 of `prompt_id + model + operation_type + temperature + top_p + top_k + min_moment_length + max_moment_length + min_moments + max_moments` (note: `transcript_id` is NOT included in hash)

3. **Modify moment generation flow** in `app/services/ai/generation_service.py`:
   - When generating moments, before calling the AI model:
     a. Get or create the prompt record (user_prompt + system_prompt)
     b. Get or create the generation config record
   - After generation completes, associate the config ID with the generated moments
   - Pass the `generation_config_id` to the moment save function

4. **Modify refinement flow** in `app/services/ai/refinement_service.py`:
   - Same pattern: create prompt + config records for refinement operations
   - `operation_type` = "refinement" for these

5. **Update pipeline orchestrator** to pass config context:
   - Track `generation_config_id` in pipeline context
   - Pass it to subsequent stages that create moments

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `app/repositories/prompt_db_repository.py` | Prompt CRUD with dedup |
| CREATE | `app/repositories/generation_config_db_repository.py` | Config CRUD with dedup |
| MODIFY | `app/services/ai/generation_service.py` | Create prompt + config records |
| MODIFY | `app/services/ai/refinement_service.py` | Create prompt + config records |
| MODIFY | `app/services/pipeline/orchestrator.py` | Pass config IDs through pipeline |

### Verification Checklist

- [ ] Running a pipeline creates prompt and generation_config records in DB
- [ ] Duplicate prompts are deduplicated by hash
- [ ] Duplicate configs are deduplicated by hash
- [ ] Config hash correctly excludes transcript_id
- [ ] Refinement creates separate config records with operation_type="refinement"
- [ ] generation_config_id is tracked in pipeline context

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments to PostgreSQL. Phases 1-4 complete: database set up, videos and transcripts in DB. Now I need to implement the prompts and generation_configs tables for tracking AI generation parameters.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

SCHEMA:
Read database/SCHEMA.md:
- Table 4 (Prompts): id, user_prompt, system_prompt, prompt_hash (SHA-256 for dedup), created_at
- Table 5 (Generation Configs): id, prompt_id (FK), transcript_id (FK nullable), model, operation_type, temperature, top_p, top_k, min_moment_length, max_moment_length, min_moments, max_moments, config_hash (SHA-256 for dedup, EXCLUDES transcript_id), created_at

FILES TO READ FIRST:
- database/SCHEMA.md (Tables 4 and 5 for schema details)
- app/services/ai/generation_service.py (moment generation -- especially how prompts are built and AI is called)
- app/services/ai/refinement_service.py (moment refinement flow)
- app/services/pipeline/orchestrator.py (pipeline stages, how config params flow)
- app/database/models/prompt.py (SQLAlchemy model)
- app/database/models/generation_config.py (SQLAlchemy model)
- app/database/dependencies.py (how to get DB session)

TASK:
1. Create app/repositories/prompt_db_repository.py:
   - import hashlib for SHA-256
   - async def compute_prompt_hash(user_prompt: str, system_prompt: str) -> str:
       Returns SHA-256 hex digest of user_prompt + system_prompt concatenation
   - async def create_or_get(session, user_prompt: str, system_prompt: str) -> Prompt:
       Compute hash, check if exists by hash, if yes return existing, if no create new
   - async def get_by_id(session, id: int) -> Optional[Prompt]

2. Create app/repositories/generation_config_db_repository.py:
   - async def compute_config_hash(prompt_id, model, operation_type, temperature, top_p, top_k, min_moment_length, max_moment_length, min_moments, max_moments) -> str:
       SHA-256 of all params concatenated (NOT transcript_id -- this is critical, see SCHEMA.md notes)
   - async def create_or_get(session, prompt_id, model, operation_type, transcript_id=None, temperature=None, top_p=None, top_k=None, min_moment_length=None, max_moment_length=None, min_moments=None, max_moments=None) -> GenerationConfig:
       Compute hash, check if exists, create if not. Set transcript_id on the record regardless of hash match.
   - async def get_by_id(session, id: int) -> Optional[GenerationConfig]

3. Modify generation_service.py:
   - Find where the AI model is called for moment generation (look for where user_prompt and system_prompt are assembled)
   - Before the AI call, create or get the prompt record and generation config record
   - After moments are parsed from AI response, include generation_config_id in each moment dict
   - This will be used in Phase 6 when moments are saved to DB
   - For now, add generation_config_id to the moment dict alongside existing fields

4. Modify refinement_service.py:
   - Same pattern as generation: create prompt + config before AI call
   - Use operation_type="refinement"
   - Include generation_config_id in refined moment data

5. Update orchestrator.py:
   - In execute_moment_generation(): capture generation_config_id from generation service result, store in pipeline context
   - In refinement stages: capture generation_config_id similarly

IMPORTANT:
- The hash for configs must EXCLUDE transcript_id (read the SCHEMA.md design notes carefully)
- Prompts are immutable once created (no update operations)
- Use get_async_session from app.database.session for getting DB sessions in services
- This phase creates the infrastructure that Phase 6 (moments) and Phase 9 (pipeline history) will depend on
- Don't break existing moment generation/refinement -- only ADD database record creation alongside existing logic
```

---

## Phase 6: Moments to Database

### Goal
Migrate moment storage from JSON files (`static/moments/*.json`) to the PostgreSQL `moments` table. This is one of the most impactful changes as moments are frequently read, written, and updated.

### What Exactly To Do

1. **Create database moment repository** at `app/repositories/moment_db_repository.py`:
   - `create(session, identifier, video_id, start_time, end_time, title, is_refined, parent_id, generation_model, generation_config_id, score, scoring_model, scored_at)` -- inserts row
   - `get_by_identifier(session, identifier)` -- returns Moment or None
   - `get_by_video_id(session, video_id)` -- returns list of moments for a video
   - `get_by_video_identifier(session, video_identifier)` -- joins with videos table
   - `get_originals_for_video(session, video_id)` -- returns only non-refined moments
   - `get_refined_for_moment(session, parent_id)` -- returns refined versions
   - `update_score(session, moment_id, score, scoring_model)` -- updates score fields
   - `delete_by_video_id(session, video_id)` -- deletes all moments for a video
   - `delete_by_identifier(session, identifier)` -- deletes single moment

2. **Create one-time migration script** at `scripts/migrate_moments_to_db.py`:
   - Reads all JSON files from `static/moments/`
   - For each moments JSON (which is an array of moment objects):
     a. Look up the video in DB by identifier = filename stem
     b. For each moment in the array:
        - Map fields: `id` → `identifier`, `start_time`, `end_time`, `title`, `is_refined`, `model_name` → `generation_model`
        - Insert into `moments` table with `video_id` FK
     c. Handle parent_id mapping: refined moments reference parent by identifier, need to map to DB id
   - Two-pass approach: first insert all original moments, then insert refined moments with parent_id references
   - Skip if moments already exist for that video

3. **Modify `app/services/moments_service.py`**:
   - Replace `load_moments()` to query DB instead of reading JSON
   - Replace `save_moments()` to batch insert/update DB instead of writing JSON
   - Replace individual moment operations (add, update, delete) with DB operations
   - Keep function signatures compatible where possible
   - Remove file locking code (DB handles concurrency)

4. **Modify `app/repositories/moments_repository.py`**:
   - Replace file-based `BaseRepository` implementation with DB-backed calls
   - Redirect to `moment_db_repository.py`

5. **Modify `app/api/endpoints/moments.py`**:
   - `GET /api/videos/{video_id}/moments` -- query DB for moments by video identifier
   - `POST /api/videos/{video_id}/moments` -- insert into DB
   - `POST /api/videos/{video_id}/generate-moments` -- generation creates DB records (via generation_service which already creates them from Phase 5)
   - `DELETE /api/videos/{video_id}/moments/{moment_id}` -- delete from DB
   - Add `Depends(get_db)` for database session

6. **Update moment generation service** output:
   - `generation_service.py`: After AI response parsing, create moment records in DB (instead of appending to JSON)
   - Each moment gets a generated `identifier` (the hex ID currently used as `id` in JSON)
   - Associate `generation_config_id` from Phase 5
   - `refinement_service.py`: Create refined moment record with `parent_id` pointing to original moment's DB id

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `app/repositories/moment_db_repository.py` | DB CRUD for moments |
| CREATE | `scripts/migrate_moments_to_db.py` | One-time JSON → DB migration |
| MODIFY | `app/services/moments_service.py` | Replace JSON I/O with DB ops |
| MODIFY | `app/repositories/moments_repository.py` | Redirect to DB implementation |
| MODIFY | `app/api/endpoints/moments.py` | Query DB, add session dependency |
| MODIFY | `app/services/ai/generation_service.py` | Save moments to DB |
| MODIFY | `app/services/ai/refinement_service.py` | Save refined moments to DB |

### Verification Checklist

- [ ] Migration script runs and all existing moments are in the database
- [ ] `GET /api/videos/{video_id}/moments` returns data from database
- [ ] Moment generation (pipeline) creates DB records
- [ ] Moment refinement creates DB records with correct parent_id
- [ ] Frontend MomentsList displays moments correctly from DB data
- [ ] Moment deletion works via DB
- [ ] Adding a manual moment works via DB
- [ ] No JSON files are read by any active code path

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments to PostgreSQL. Phases 1-5 complete: database set up, videos in cloud+DB, transcripts in DB, prompts and generation_configs tables implemented. Now I need to migrate moments from JSON files to database.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- Moments stored as JSON in static/moments/{video_id}.json
- Each JSON file contains an array of moment objects
- Moment JSON structure: { "id": "0658152d253996fe", "title": "...", "start_time": 10.5, "end_time": 45.2, "is_refined": false, "parent_id": null, "model_name": "qwen3_vl_fp8", ... }
- moments_service.py has load_moments() and save_moments() for file I/O with FileLock
- Moments CRUD in moments.py endpoint and moments_repository.py (file-based)
- Generation service creates moments and saves via save_moments()
- Refinement service creates refined moments with parent_id reference

SCHEMA:
Read database/SCHEMA.md Table 3 (Moments). Columns: id, identifier (unique), video_id (FK), start_time, end_time, title, is_refined, parent_id (self-ref FK), generation_model, generation_config_id (FK), score, scoring_model, scored_at, created_at, updated_at.

FILES TO READ FIRST:
- database/SCHEMA.md (Table 3 for moments schema)
- app/services/moments_service.py (load_moments, save_moments, get_moments_file_path)
- app/repositories/moments_repository.py (file-based CRUD)
- app/api/endpoints/moments.py (moments API endpoints - all of them)
- app/services/ai/generation_service.py (where moments are created after AI call)
- app/services/ai/refinement_service.py (where refined moments are created)
- app/database/models/moment.py (SQLAlchemy model)
- One JSON file from static/moments/ to see the actual data structure

TASK:
1. Create app/repositories/moment_db_repository.py:
   Async functions accepting AsyncSession:
   - create(session, identifier, video_id, start_time, end_time, title, is_refined=False, parent_id=None, generation_model=None, generation_config_id=None, score=None, scoring_model=None, scored_at=None) -> Moment
   - bulk_create(session, moments_data: List[dict]) -> List[Moment] (for batch insert of generated moments)
   - get_by_identifier(session, identifier) -> Optional[Moment]
   - get_by_video_id(session, video_id: int) -> List[Moment] (ordered by start_time)
   - get_by_video_identifier(session, video_identifier: str) -> List[Moment] (join with videos)
   - get_originals_for_video(session, video_id: int) -> List[Moment] (where is_refined=False)
   - get_refined_for_parent(session, parent_id: int) -> List[Moment]
   - update_score(session, moment_id: int, score: int, scoring_model: str) -> Optional[Moment]
   - delete_by_identifier(session, identifier: str) -> bool
   - delete_all_for_video(session, video_id: int) -> int (returns count deleted)

2. Create scripts/migrate_moments_to_db.py:
   - Standalone async script
   - Scan static/moments/ for all .json files
   - For each file:
     a. Parse JSON array of moments
     b. Look up video in DB by identifier = filename stem (without .json)
     c. If video not found, skip with warning
     d. Two-pass insert:
        Pass 1: Insert all original moments (is_refined=False or absent)
        Pass 2: Insert refined moments -- look up parent_id by matching the parent's identifier in the moments table
     e. Map JSON fields to DB columns:
        - "id" → identifier
        - "title" → title
        - "start_time" → start_time
        - "end_time" → end_time
        - "is_refined" → is_refined (default False)
        - "parent_id" → look up in DB by identifier to get numeric parent_id
        - "model_name" → generation_model
        - generation_config_id → NULL (historical moments don't have configs)
        - score, scoring_model, scored_at → NULL (will be populated later if scored)
   - Print progress and summary

3. Modify app/services/moments_service.py:
   - Replace load_moments(video_filename):
     Extract identifier from video_filename stem
     Get DB session from async_sessionmaker
     Call moment_db_repository.get_by_video_identifier(session, identifier)
     Convert Moment objects to dicts matching the format callers expect
     Return list of dicts
   - Replace save_moments(video_filename, moments_list):
     For new moments (no DB id), insert via bulk_create
     For existing moments (with DB id), update if changed
     Remove all file write code and FileLock usage
   - Replace individual moment operations to use DB

4. Modify app/repositories/moments_repository.py:
   - Redirect all methods to moment_db_repository functions
   - Or gut the class and delegate to DB repo

5. Modify app/api/endpoints/moments.py:
   - Add Depends(get_db) to all endpoints that need DB
   - GET /api/videos/{video_id}/moments: Query DB via moment_db_repository.get_by_video_identifier()
   - POST /api/videos/{video_id}/moments: Create moment in DB
   - DELETE: Delete from DB
   - Generation status endpoint: Keep as-is (uses Redis)
   - Fix the undefined 'status' variable bug on line ~341 while you're in this file

6. Update generation_service.py:
   - After parsing AI response into moment dicts, create DB records via moment_db_repository.bulk_create()
   - Each moment already has a generated identifier (hex ID)
   - Set generation_config_id from the config created in Phase 5
   - Remove the call to save_moments() for JSON file writing

7. Update refinement_service.py:
   - After refinement, create the refined moment in DB with:
     - is_refined=True
     - parent_id = the DB id of the original moment (look up by identifier)
     - generation_config_id from Phase 5's refinement config
   - Remove JSON file updates

IMPORTANT:
- Moments are frequently read (every time video loads in frontend) -- DB queries must be efficient
- The frontend expects moments as a list of objects with fields: id (use identifier), title, start_time, end_time, is_refined, parent_id, model_name (map from generation_model)
- Keep the response format compatible with what the frontend's MomentsList component expects
- Don't delete JSON files -- keep as backups
- Remove FileLock imports and usage -- PostgreSQL handles concurrency
```

---

## Phase 7: Clips to Cloud + Database

### Goal
Ensure all clips are stored in GCS and tracked in the `clips` database table. Remove local clip file dependency. Clips are already partially uploaded to GCS; this phase makes GCS the single source of truth and adds DB tracking.

### What Exactly To Do

1. **Create database clip repository** at `app/repositories/clip_db_repository.py`:
   - `create(session, moment_id, video_id, cloud_url, start_time, end_time, padding_left, padding_right, file_size_kb, format, video_codec, audio_codec, resolution)` -- inserts row
   - `get_by_moment_id(session, moment_id)` -- returns Clip or None
   - `get_by_video_id(session, video_id)` -- returns list of clips for a video
   - `get_by_video_identifier(session, identifier)` -- joins with videos
   - `delete_by_moment_id(session, moment_id)` -- deletes clip
   - `delete_all_for_video(session, video_id)` -- deletes all clips for a video

2. **Modify `app/services/video_clipping_service.py`**:
   - `extract_video_clip()`: After creating clip locally + uploading to GCS:
     a. Extract clip metadata (file size, codec, resolution) via ffprobe
     b. Insert clip record into DB with `cloud_url`, padding info, and metadata
     c. Delete local clip file after successful GCS upload + DB insert
   - `get_clip_url()`: Return GCS signed URL instead of local path
   - `check_clip_exists()`: Query DB instead of filesystem
   - `delete_all_clips_for_video()`: Delete from GCS + DB (not local filesystem)
   - `extract_clips_for_video()`: Update batch extraction to save DB records

3. **Modify `app/api/endpoints/clips.py`**:
   - Add `Depends(get_db)` for database session
   - Clip-related queries should use DB repository
   - Return GCS signed URLs for clip playback

4. **Modify clip serving**:
   - Remove the static mount for `/moment_clips` in `app/main.py`
   - Add endpoint `GET /api/clips/{clip_id}/url` that returns a signed URL
   - Or modify existing clip URL generation to return signed URLs

5. **Create migration script** at `scripts/migrate_clips_to_cloud.py`:
   - For existing local clips in `static/moment_clips/`:
     a. Parse filename to extract video_stem and moment_id
     b. Look up moment and video in DB
     c. Upload to GCS if not already there
     d. Insert clip record in DB
   - Skip clips whose moment or video doesn't exist in DB

6. **Update pipeline orchestrator** clip extraction:
   - After clips are extracted and uploaded, verify DB records exist
   - Remove any local file path tracking for clips

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `app/repositories/clip_db_repository.py` | DB CRUD for clips |
| CREATE | `scripts/migrate_clips_to_cloud.py` | One-time migration |
| MODIFY | `app/services/video_clipping_service.py` | GCS + DB, remove local deps |
| MODIFY | `app/api/endpoints/clips.py` | Query DB, signed URLs |
| MODIFY | `app/main.py` | Remove /moment_clips mount |
| MODIFY | `app/services/pipeline/orchestrator.py` | DB-backed clip tracking |

### Verification Checklist

- [ ] Migration script uploads existing clips to GCS and creates DB records
- [ ] New clip extraction creates GCS files + DB records
- [ ] Clip URLs returned by API are GCS signed URLs
- [ ] Frontend can play clips via signed URLs
- [ ] Local clip files are not needed
- [ ] Clip deletion removes from GCS + DB

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments to GCS + PostgreSQL. Phases 1-6 complete: database set up, videos in cloud, transcripts and moments in DB. Now I need to fully migrate clips to GCS + database tracking.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- Clips created locally at static/moment_clips/{video_stem}_{moment_id}_clip.mp4
- Some clips already uploaded to GCS during pipeline execution (via GCSUploader.upload_clip())
- Clips served locally via static file mount: /moment_clips/
- No database tracking of clips
- video_clipping_service.py handles extraction, existence checks, URL generation
- Frontend accesses clips via moment.clip_url (local URLs currently)

SCHEMA:
Read database/SCHEMA.md Table 6 (Clips). Columns: id, moment_id (FK UNIQUE), video_id (FK), cloud_url, start_time, end_time, padding_left, padding_right, file_size_kb, format, video_codec, audio_codec, resolution, created_at.

FILES TO READ FIRST:
- database/SCHEMA.md (Table 6 for clips schema)
- app/services/video_clipping_service.py (complete file -- clip extraction, URL generation, deletion)
- app/services/pipeline/upload_service.py (GCSUploader.upload_clip method)
- app/api/endpoints/clips.py (clip API endpoints)
- app/services/pipeline/orchestrator.py (clip extraction in pipeline)
- app/database/models/clip.py (SQLAlchemy model)
- app/main.py (static mount for moment_clips)

TASK:
1. Create app/repositories/clip_db_repository.py:
   Async functions:
   - create(session, moment_id, video_id, cloud_url, start_time, end_time, padding_left, padding_right, file_size_kb=None, format=None, video_codec=None, audio_codec=None, resolution=None) -> Clip
   - get_by_moment_id(session, moment_id: int) -> Optional[Clip]
   - get_by_moment_identifier(session, moment_identifier: str) -> Optional[Clip] (join with moments)
   - get_by_video_id(session, video_id: int) -> List[Clip]
   - get_by_video_identifier(session, video_identifier: str) -> List[Clip]
   - delete_by_moment_id(session, moment_id: int) -> bool
   - delete_all_for_video(session, video_id: int) -> int

2. Modify video_clipping_service.py:
   - extract_video_clip(): After FFmpeg creates the clip:
     a. Get clip metadata via ffprobe (file_size, video_codec, audio_codec, resolution)
     b. Upload to GCS via GCSUploader.upload_clip() (already exists)
     c. Create DB record via clip_db_repository.create() with cloud_url from GCS upload
     d. Delete the local clip file after successful GCS upload + DB insert
   - get_clip_url(): Instead of returning local path, generate GCS signed URL
   - check_clip_exists(): Query DB via clip_db_repository.get_by_moment_identifier() instead of checking local file
   - delete_all_clips_for_video(): Delete from GCS (via GCSUploader.delete_clips_for_video()) and DB, not local filesystem
   - get_clip_gcs_signed_url_async(): Update to use DB cloud_url to generate signed URL
   
3. Create scripts/migrate_clips_to_cloud.py:
   - Scan static/moment_clips/ for .mp4 files
   - Parse filename pattern: {video_stem}_{moment_id}_clip.mp4
   - For each clip:
     a. Look up moment in DB by identifier = moment_id portion
     b. Look up video in DB by identifier = video_stem portion
     c. If both exist and no clip record in DB:
        - Upload to GCS if not already there
        - Create clip DB record with cloud_url
     d. Skip with warning if moment/video not found
   - Print progress

4. Modify app/api/endpoints/clips.py:
   - Add Depends(get_db) to endpoints
   - Return clip info from DB with cloud signed URLs instead of local paths

5. Remove static mount for /moment_clips from app/main.py

6. Update orchestrator.py clip extraction stages:
   - Verify clip DB records are created after extraction
   - Remove local file path tracking for clips

IMPORTANT:
- Clips have a 1:1 relationship with moments (UNIQUE on moment_id)
- Clips always reference root moments, never refined moments
- padding_left and padding_right are the actual padding used after boundary adjustment
- clip start_time = moment.start_time - padding_left, end_time = moment.end_time + padding_right
- After successful GCS upload + DB insert, the local clip file should be deleted
- Frontend MomentCard.jsx uses moment.clip_url -- make sure the API response includes the GCS signed URL for clip_url
```

---

## Phase 8: Thumbnails to Cloud + Database

### Goal
Migrate thumbnail storage to GCS and track in the `thumbnails` database table. Remove local thumbnail file dependency.

### What Exactly To Do

1. **Create database thumbnail repository** at `app/repositories/thumbnail_db_repository.py`:
   - `create_for_video(session, video_id, cloud_url, file_size_kb)` -- inserts row
   - `create_for_clip(session, clip_id, cloud_url, file_size_kb)` -- inserts row
   - `get_by_video_id(session, video_id)` -- returns Thumbnail or None
   - `get_by_clip_id(session, clip_id)` -- returns Thumbnail or None
   - `delete_by_video_id(session, video_id)` -- deletes thumbnail

2. **Extend `GCSUploader`** in `app/services/pipeline/upload_service.py`:
   - Add `gcs_thumbnails_prefix: str = "thumbnails/"` to config
   - Add `upload_thumbnail()` method: uploads to `gs://bucket/thumbnails/{type}/{id}.jpg`
   - Add `get_thumbnail_signed_url()` method

3. **Modify `app/services/thumbnail_service.py`**:
   - After generating thumbnail locally:
     a. Upload to GCS
     b. Insert DB record
     c. Delete local file
   - `get_thumbnail_url()`: Return GCS signed URL from DB record

4. **Modify `app/api/endpoints/videos.py`**:
   - `GET /api/videos/{video_id}/thumbnail`: Return redirect to GCS signed URL (instead of serving local file)

5. **Create migration script** at `scripts/migrate_thumbnails_to_cloud.py`:
   - Upload existing thumbnails from `static/thumbnails/` to GCS
   - Create DB records

6. **Remove static mount** for thumbnails in `app/main.py`

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `app/repositories/thumbnail_db_repository.py` | DB CRUD for thumbnails |
| CREATE | `scripts/migrate_thumbnails_to_cloud.py` | One-time migration |
| MODIFY | `app/core/config.py` | Add gcs_thumbnails_prefix |
| MODIFY | `app/services/pipeline/upload_service.py` | Add thumbnail upload |
| MODIFY | `app/services/thumbnail_service.py` | GCS + DB, remove local |
| MODIFY | `app/api/endpoints/videos.py` | Thumbnail redirect to GCS |
| MODIFY | `app/main.py` | Remove /static/thumbnails mount |

### Verification Checklist

- [ ] Existing thumbnails migrated to GCS + DB
- [ ] New thumbnail creation uploads to GCS + creates DB record
- [ ] Thumbnail endpoint returns GCS signed URL
- [ ] Frontend VideoCard displays thumbnails from GCS
- [ ] Local thumbnail files not needed

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments to GCS + PostgreSQL. Phases 1-7 complete. Videos, transcripts, moments, and clips are all in cloud/DB. Now I need to migrate thumbnails.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- Thumbnails stored locally at static/thumbnails/{video_filename}.jpg
- Served via static file mount at /static/thumbnails/
- thumbnail_service.py generates thumbnails using FFmpeg frame extraction
- Frontend VideoCard.jsx uses getThumbnailUrl(video.id) or video.thumbnail_url

SCHEMA:
Read database/SCHEMA.md Table 7 (Thumbnails). Key: Either video_id OR clip_id must be set (CHECK constraint), never both. 1:1 relationship with video or clip via partial unique indexes.

FILES TO READ FIRST:
- database/SCHEMA.md (Table 7 for thumbnails schema)
- app/services/thumbnail_service.py (current thumbnail generation and serving)
- app/api/endpoints/videos.py (thumbnail endpoint)
- app/database/models/thumbnail.py (SQLAlchemy model)
- app/services/pipeline/upload_service.py (GCSUploader to extend)
- app/core/config.py (for adding gcs_thumbnails_prefix)
- moments-frontend/src/components/VideoCard.jsx (how thumbnails are loaded)

TASK:
1. Add gcs_thumbnails_prefix: str = "thumbnails/" to config.py

2. Create app/repositories/thumbnail_db_repository.py:
   - create_for_video(session, video_id, cloud_url, file_size_kb=None) -> Thumbnail
   - create_for_clip(session, clip_id, cloud_url, file_size_kb=None) -> Thumbnail
   - get_by_video_id(session, video_id) -> Optional[Thumbnail]
   - get_by_clip_id(session, clip_id) -> Optional[Thumbnail]
   - delete_by_video_id(session, video_id) -> bool
   - delete_by_clip_id(session, clip_id) -> bool

3. Extend GCSUploader in upload_service.py:
   - upload_thumbnail(self, local_path, entity_type: str, entity_id: str) -> Tuple[str, str]
     Uploads to gs://bucket/thumbnails/{entity_type}/{entity_id}.jpg
     entity_type = "video" or "clip"
   - get_thumbnail_signed_url(self, entity_type, entity_id) -> Optional[str]

4. Modify thumbnail_service.py:
   - After generating thumbnail with FFmpeg, upload to GCS and create DB record
   - Delete local file after successful upload
   - get_thumbnail_url(): Query DB for cloud_url, generate signed URL

5. Modify videos.py GET /api/videos/{video_id}/thumbnail:
   - Query DB for thumbnail by video_id
   - If found, redirect to GCS signed URL
   - If not found, generate thumbnail on-demand (download video to temp, extract frame, upload, create record)

6. Create scripts/migrate_thumbnails_to_cloud.py:
   - Scan static/thumbnails/ for .jpg files
   - Upload each to GCS, create DB records
   - Map video_filename.jpg → video identifier → video_id FK

7. Remove /static/thumbnails mount from app/main.py

IMPORTANT:
- The CHECK constraint ensures either video_id OR clip_id is set, never both
- Partial unique indexes ensure 1:1 relationship
- Frontend may need slight adjustment if thumbnail URLs change format, but since we're returning a redirect from the same endpoint, it should work
```

---

## Phase 9: Pipeline History to Database

### Goal
Move pipeline run tracking from Redis to the PostgreSQL `pipeline_history` table. Redis continues to be used for real-time status during execution, but completed/failed runs are persisted in the database.

### What Exactly To Do

1. **Create database pipeline history repository** at `app/repositories/pipeline_history_db_repository.py`:
   - `create(session, identifier, video_id, generation_config_id, pipeline_type, status, started_at)` -- inserts row
   - `update_status(session, id, status, completed_at, duration_seconds, total_moments_generated, total_clips_created, error_stage, error_message)` -- partial update
   - `get_by_identifier(session, identifier)` -- returns PipelineHistory or None
   - `get_by_video_id(session, video_id)` -- returns history list
   - `get_by_video_identifier(session, identifier)` -- joins with videos
   - `get_recent(session, limit)` -- returns recent pipeline runs

2. **Modify pipeline orchestrator** and worker:
   - At pipeline start: create DB record with `status="running"`
   - At each stage completion: update record (increment counts)
   - At pipeline completion: update with `status="completed"`, `completed_at`, `duration_seconds`, totals
   - At pipeline failure: update with `status="failed"`, `error_stage`, `error_message`

3. **Modify `app/services/pipeline/redis_history.py`** (if it exists) or create a wrapper:
   - `archive_active_to_history()`: Instead of archiving to Redis, write to database
   - Keep Redis for real-time status during execution

4. **Modify `app/api/endpoints/pipeline.py`**:
   - `GET /api/pipeline/{video_id}/history` -- query database instead of Redis
   - Keep `GET /api/pipeline/{video_id}/status` -- this uses Redis for real-time (no change)

5. **Create migration script** at `scripts/migrate_pipeline_history_to_db.py`:
   - Read existing pipeline history from Redis
   - Insert into database table
   - Skip if already exists by identifier

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `app/repositories/pipeline_history_db_repository.py` | DB CRUD for pipeline history |
| CREATE | `scripts/migrate_pipeline_history_to_db.py` | Redis → DB migration |
| MODIFY | `app/services/pipeline/orchestrator.py` | Write to DB during execution |
| MODIFY | `app/workers/pipeline_worker.py` | DB records on start/end |
| MODIFY | `app/api/endpoints/pipeline.py` | Query DB for history |

### Verification Checklist

- [ ] Pipeline start creates DB record
- [ ] Pipeline completion updates DB record
- [ ] Pipeline failure updates DB record with error details
- [ ] `GET /api/pipeline/{video_id}/history` returns data from database
- [ ] Real-time status still works via Redis
- [ ] Existing history migrated from Redis to DB

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments to PostgreSQL. Phases 1-8 complete. All file-based data is now in cloud/DB. Now I need to move pipeline history from Redis to PostgreSQL for durable persistence.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- Pipeline history stored in Redis hashes/streams
- Pipeline real-time status tracked in Redis during execution
- archive_active_to_history() moves active status to history after completion
- Pipeline history expires with TTL (24h) in Redis
- History endpoint reads from Redis

SCHEMA:
Read database/SCHEMA.md Table 8 (Pipeline History). Columns: id, identifier, video_id (FK), generation_config_id (FK), pipeline_type, status, started_at, completed_at, duration_seconds, total_moments_generated, total_clips_created, error_stage, error_message, created_at.

FILES TO READ FIRST:
- database/SCHEMA.md (Table 8)
- app/services/pipeline/orchestrator.py (pipeline execution, see how stages run and status updates)
- app/workers/pipeline_worker.py (pipeline worker, how jobs start/end)
- app/api/endpoints/pipeline.py (pipeline API, especially history endpoint)
- Look for any redis_history.py or status.py in app/services/pipeline/ for current Redis-based history
- app/database/models/pipeline_history.py (SQLAlchemy model)

TASK:
1. Create app/repositories/pipeline_history_db_repository.py:
   - create(session, identifier, video_id, generation_config_id, pipeline_type, status="running", started_at=None) -> PipelineHistory
   - update_status(session, history_id, status=None, completed_at=None, duration_seconds=None, total_moments_generated=None, total_clips_created=None, error_stage=None, error_message=None) -> PipelineHistory
   - get_by_identifier(session, identifier) -> Optional[PipelineHistory]
   - get_by_video_id(session, video_id) -> List[PipelineHistory] (ordered by started_at DESC)
   - get_by_video_identifier(session, video_identifier) -> List[PipelineHistory]
   - get_recent(session, limit=20) -> List[PipelineHistory]
   - Use SQLAlchemy 2.0 patterns

2. Modify pipeline_worker.py:
   - When starting a pipeline job: create DB record with status="running"
   - When completing: update DB record with status="completed", duration, totals
   - When failing: update DB record with status="failed", error details
   - Generate identifier format: "pipeline:{video_identifier}:{timestamp}"

3. Modify orchestrator.py:
   - At each stage completion, update the pipeline history record with running totals
   - Pass the pipeline_history DB id through the execution context

4. Modify pipeline.py endpoints:
   - GET /api/pipeline/{video_id}/history: Query database via pipeline_history_db_repository
   - Add Depends(get_db)
   - Keep GET /api/pipeline/{video_id}/status using Redis (real-time, changes fast)
   - The pattern: Redis for real-time status during execution, DB for durable history after completion

5. Create scripts/migrate_pipeline_history_to_db.py:
   - Connect to Redis and read existing pipeline history entries
   - Insert into PostgreSQL pipeline_history table
   - Match video identifiers to video DB ids
   - Skip entries that can't be mapped

IMPORTANT:
- Redis remains for REAL-TIME pipeline status (it changes every second during execution)
- Database is for DURABLE history (permanent record of completed/failed runs)
- After pipeline completes, the Redis status can expire -- the DB record persists forever
- The identifier format should be human-readable: "pipeline:{video_id}:{epoch_ms}"
```

---

## Phase 10: URL Registry Elimination

### Goal
Remove the JSON-based URL registry (`static/url_registry.json`) and replace it with database queries on the `videos` table's `source_url` column. This was the original purpose of the URL registry -- mapping download URLs to video identifiers to detect duplicates.

### What Exactly To Do

1. **Modify `app/services/url_registry.py`**:
   - Replace `_load()` and `_save()` (JSON file I/O) with database queries
   - `register(url, video_id)`: This is now handled by `videos.source_url` column -- no separate registry needed
   - `lookup(url)` → `SELECT identifier FROM videos WHERE source_url = $1`
   - `get_id_for_url(url)` → same DB query
   - Or better: deprecate the entire URLRegistry class and replace callers with direct DB queries

2. **Update callers** in `app/api/endpoints/generate_moments.py`:
   - Replace `url_registry.lookup(url)` with `video_db_repository.get_by_source_url(session, url)`
   - If video exists with that source_url, reuse it instead of re-downloading

3. **Update callers** in `app/services/pipeline/orchestrator.py`:
   - Replace URL registry lookups with DB queries

4. **Delete the JSON file**: `static/url_registry.json` (after migration)

5. **Remove config reference**: Delete `url_registry_file` from `app/core/config.py`

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| MODIFY | `app/services/url_registry.py` | Replace with DB queries or deprecate |
| MODIFY | `app/api/endpoints/generate_moments.py` | Use DB for URL lookup |
| MODIFY | `app/services/pipeline/orchestrator.py` | Use DB for URL lookup |
| MODIFY | `app/core/config.py` | Remove url_registry_file setting |
| DELETE | `static/url_registry.json` | No longer needed |

### Verification Checklist

- [ ] Duplicate URL detection works via database query
- [ ] Generate moments with URL reuses existing video from DB
- [ ] URL registry JSON file is no longer read or written
- [ ] No references to url_registry_file in config

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments to PostgreSQL. Phases 1-9 complete. All data is in cloud/DB. Now I need to eliminate the JSON-based URL registry -- its functionality is replaced by the videos.source_url column in the database.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- URL registry at static/url_registry.json maps download URLs to video identifiers
- Used for duplicate detection: "Has this URL already been downloaded?"
- URLRegistry class in app/services/url_registry.py handles JSON file I/O with FileLock
- Called from generate_moments.py and orchestrator.py
- The videos DB table now has source_url column with an index for fast lookup

FILES TO READ FIRST:
- app/services/url_registry.py (complete file - see the class methods)
- app/api/endpoints/generate_moments.py (see where URLRegistry is used)
- app/services/pipeline/orchestrator.py (see URL registry usage in download flow)
- app/repositories/video_db_repository.py (get_by_source_url method exists from Phase 2)
- app/core/config.py (url_registry_file setting)

TASK:
1. The goal is to replace url_registry.py with database queries. Two approaches:
   
   Option A (preferred): Deprecate URLRegistry class entirely. Replace all callers with direct calls to video_db_repository.get_by_source_url().
   
   Option B: Rewrite URLRegistry to use DB internally (keeps the interface but changes implementation).

   Go with Option A unless the URLRegistry interface is deeply embedded.

2. In generate_moments.py:
   - Find where URLRegistry.lookup() or similar is called
   - Replace with: video = await video_db_repository.get_by_source_url(session, url)
   - If video exists, use video.identifier instead of re-downloading

3. In orchestrator.py:
   - Find URL registry calls in execute_video_download()
   - Replace with DB query for existing video by source_url
   - If found, skip download and use existing cloud_url

4. Remove url_registry_file from config.py Settings class

5. Add deprecation warning to url_registry.py or delete it entirely if no other code uses it

6. Delete static/url_registry.json file (use the shell to remove it)

IMPORTANT:
- The key functional requirement: given a URL, determine if we've already downloaded and processed that video
- This is now served by: SELECT * FROM videos WHERE source_url = '{url}'
- The source_url column has an index (idx_videos_source_url) for fast lookup
- Don't break the generate_moments endpoint flow -- it must still work for both new URLs and re-used videos
```

---

## Phase 11: Temp File Management & Cleanup Scheduler

### Goal
Implement a structured temp file management system. All processing operations (video download, audio extraction, clip creation) use a temp directory. A background scheduler automatically cleans up files older than 24 hours.

### What Exactly To Do

1. **Create temp file manager** at `app/services/temp_file_manager.py`:
   - `get_temp_dir(purpose: str, identifier: str) -> Path`: Creates `temp/{purpose}/{identifier}/` (e.g., `temp/videos/motivation/`, `temp/audio/motivation/`)
   - `register_temp_file(path: Path)`: Track a temp file for cleanup
   - `cleanup_old_files(max_age_hours: int = 24)`: Delete all temp files older than max_age
   - `cleanup_all()`: Delete everything in temp/
   - `get_temp_stats()`: Return count and total size of temp files

2. **Add cleanup configuration** to `app/core/config.py`:
   - `temp_cleanup_interval_hours: float = 6.0` (run cleanup every 6 hours)
   - `temp_max_age_hours: float = 24.0` (delete files older than 24 hours)

3. **Start background cleanup task** in `app/main.py`:
   - On startup, create an asyncio task that runs cleanup on the configured interval
   - Log cleanup results (files removed, space freed)

4. **Update all processing services** to use temp file manager:
   - `audio_service.py`: Extract audio to `temp/audio/{identifier}/` instead of `static/audios/`
   - `video_clipping_service.py`: Create clips in `temp/clips/{identifier}/` instead of `static/moment_clips/`
   - `thumbnail_service.py`: Create thumbnails in `temp/thumbnails/{identifier}/` instead of `static/thumbnails/`
   - Pipeline orchestrator: Download videos to `temp/videos/{identifier}/`
   - After each operation, upload to GCS (already done in prior phases), then the temp file remains for the cleanup scheduler

5. **Remove old static directories** from config:
   - Deprecate `audios_dir`, `thumbnails_dir`, `moment_clips_dir` in config
   - Keep `static_dir` for any remaining static assets

6. **Add cleanup API endpoint** (optional):
   - `POST /api/admin/cleanup-temp` -- trigger manual cleanup
   - `GET /api/admin/temp-stats` -- view temp directory usage

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `app/services/temp_file_manager.py` | Temp file lifecycle management |
| MODIFY | `app/core/config.py` | Add cleanup config |
| MODIFY | `app/main.py` | Start cleanup background task |
| MODIFY | `app/services/audio_service.py` | Use temp dir for audio |
| MODIFY | `app/services/video_clipping_service.py` | Use temp dir for clips |
| MODIFY | `app/services/thumbnail_service.py` | Use temp dir for thumbnails |
| MODIFY | `app/services/pipeline/orchestrator.py` | Use temp dir for downloads |
| MODIFY | `app/api/endpoints/admin.py` | Add cleanup endpoints |

### Verification Checklist

- [ ] All processing operations use temp directory
- [ ] Background cleanup runs every 6 hours
- [ ] Files older than 24 hours are deleted
- [ ] Pipeline still works end-to-end with temp files
- [ ] Temp stats endpoint works
- [ ] No files written to static/ directories (except static assets if any)

### AI Agent Prompt

```
CONTEXT:
I'm migrating VideoMoments to GCS + PostgreSQL. Phases 1-10 complete. All data is in cloud/DB. Processing still creates local temp files (videos for FFmpeg, audio, clips before GCS upload). Now I need a proper temp file management system with automatic cleanup.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- Videos downloaded to temp/processing/ for FFmpeg operations
- Audio extracted to static/audios/ (needs to change to temp)
- Clips created locally before GCS upload (may already use temp from Phase 7)
- Thumbnails created locally before GCS upload (may already use temp from Phase 8)
- No automatic cleanup of temp files
- Old static/ directories still exist but are mostly unused

FILES TO READ FIRST:
- app/core/config.py (current paths configuration)
- app/services/audio_service.py (audio extraction output paths)
- app/services/video_clipping_service.py (clip creation output paths)
- app/services/thumbnail_service.py (thumbnail creation output paths)
- app/services/pipeline/orchestrator.py (video download path)
- app/main.py (startup tasks)
- app/api/endpoints/admin.py (existing admin endpoints)

TASK:
1. Create app/services/temp_file_manager.py:
   - Use pathlib.Path for all operations
   - TEMP_BASE_DIR = Path("temp") (relative to backend root)
   
   Functions:
   - get_temp_dir(purpose: str, identifier: str) -> Path:
     Creates and returns temp/{purpose}/{identifier}/ directory
     Purposes: "videos", "audio", "clips", "thumbnails"
   
   - get_temp_file_path(purpose: str, identifier: str, filename: str) -> Path:
     Returns full path: temp/{purpose}/{identifier}/{filename}
     Creates parent directory if needed
   
   - async cleanup_old_files(max_age_hours: float = 24.0) -> dict:
     Walk temp/ directory recursively
     Delete files with mtime older than max_age_hours
     Delete empty directories after file cleanup
     Return {"files_deleted": int, "bytes_freed": int, "dirs_removed": int}
   
   - async cleanup_all() -> dict:
     Delete everything in temp/
     Return stats
   
   - async get_temp_stats() -> dict:
     Return {"total_files": int, "total_size_bytes": int, "by_purpose": {...}}

2. Add to config.py:
   - temp_base_dir: Path = Path("temp")
   - temp_cleanup_interval_hours: float = 6.0
   - temp_max_age_hours: float = 24.0

3. Add background cleanup to app/main.py startup:
   - Create an asyncio task that loops:
     a. Sleep for temp_cleanup_interval_hours
     b. Call cleanup_old_files()
     c. Log results
   - Cancel the task on shutdown

4. Modify audio_service.py:
   - Change get_audio_path() to use temp_file_manager.get_temp_file_path("audio", identifier, f"{identifier}.wav")
   - After audio is uploaded to GCS (for transcription), the temp file stays for cleanup scheduler
   - Remove references to static/audios/ directory

5. Modify video_clipping_service.py:
   - Change get_clip_path() to use temp_file_manager.get_temp_file_path("clips", video_identifier, clip_filename)
   - After clip uploaded to GCS + DB record created (Phase 7), temp file stays for cleanup
   - Remove references to static/moment_clips/ directory

6. Modify thumbnail_service.py:
   - Change output path to use temp_file_manager.get_temp_file_path("thumbnails", identifier, f"{identifier}.jpg")
   - After thumbnail uploaded to GCS + DB record created (Phase 8), temp file stays for cleanup
   - Remove references to static/thumbnails/ directory

7. Modify orchestrator.py:
   - Use temp_file_manager.get_temp_file_path("videos", identifier, filename) for video downloads
   - Consistent with Phase 3's temp path but now using the formal manager

8. Add admin endpoints in admin.py:
   - POST /api/admin/cleanup-temp: Trigger cleanup_old_files(), return stats
   - GET /api/admin/temp-stats: Return get_temp_stats() result

9. Remove the static file mount for /static/audios from app/main.py (audio is temp now, not served)

IMPORTANT:
- Audio files are ONLY needed temporarily for transcription upload to GCS -- they're never served to the frontend
- The cleanup runs periodically but temp files can also be cleaned up immediately after processing if desired
- During an active pipeline, the temp video file must NOT be cleaned up (it's needed across stages)
- The cleanup should only delete files older than max_age_hours, so active processing files (< 24h old) are safe
- Keep static/ directory for any remaining needs (like static assets served by frontend)
```

---

## Phase 12: Final Cleanup & Legacy Removal

### Goal
Remove all deprecated code, unused static directories, file-based repository infrastructure, and legacy patterns. Clean up imports, remove dead code, and ensure the codebase is clean.

### What Exactly To Do

1. **Remove deprecated `JobRepository`**:
   - Delete `app/repositories/job_repository.py` (already marked DEPRECATED)
   - Remove all `job_repo = JobRepository()` instantiations from service files
   - Remove all calls to `job_repo.create_job()`, `job_repo.update_status()`, etc.

2. **Remove file-based `BaseRepository`**:
   - Delete `app/repositories/base.py` (JSON file I/O no longer needed)
   - Remove imports from `moments_repository.py` and `transcript_repository.py`

3. **Clean up old repository files**:
   - Either delete `app/repositories/moments_repository.py` and `app/repositories/transcript_repository.py` (file-based versions)
   - Or ensure they only delegate to DB repositories (from earlier phases)

4. **Remove deprecated config paths** from `app/core/config.py`:
   - Remove or deprecate: `videos_dir`, `audios_dir`, `transcripts_dir`, `moments_dir`, `thumbnails_dir`, `moment_clips_dir`
   - These local paths are no longer the source of truth

5. **Remove remaining static file mounts** from `app/main.py`:
   - Any remaining mounts for directories that are now in GCS/DB

6. **Remove deprecated video utility functions** from `app/utils/video.py`:
   - Remove `get_video_files()` (filesystem scan)
   - Remove `get_video_by_filename()`, `get_video_by_id()` (filesystem lookup)
   - Keep only DB-backed functions

7. **Clean up imports**:
   - Remove unused imports across all modified files
   - Remove `import json` where JSON file I/O no longer occurs
   - Remove `from filelock import FileLock` where file locking no longer needed

8. **Remove FileLock dependency** from `requirements.txt` if no longer used anywhere

9. **Update `app/services/video_delete_service.py`**:
   - Remove local file deletion logic (videos, audio, clips, thumbnails, moments JSON, transcript JSON)
   - Replace with: delete from DB (cascading deletes handle related records), delete from GCS
   - The cascade delete in DB automatically removes transcripts, moments, clips, thumbnails, pipeline history

10. **Remove empty static directories**:
    - `static/audios/`, `static/moments/`, `static/transcripts/`, `static/thumbnails/`, `static/moment_clips/`
    - Keep `static/` directory itself if needed for other purposes
    - Keep `static/videos/` temporarily if any local videos still exist (or remove after confirming all are in GCS)

11. **Update README.md** with new architecture:
    - Document the cloud-first architecture
    - Document environment variables for database and GCS
    - Document the temp file cleanup system
    - Remove references to static file directories

12. **Run linters and fix any issues**:
    - Run `ruff` or `flake8` to catch dead imports and unused variables
    - Fix any type errors from removed code

### Files Created/Modified

| Action | File | Purpose |
|--------|------|---------|
| DELETE | `app/repositories/job_repository.py` | Deprecated, unused |
| DELETE | `app/repositories/base.py` | File-based I/O, replaced by DB |
| MODIFY | `app/repositories/moments_repository.py` | Clean up or delete |
| MODIFY | `app/repositories/transcript_repository.py` | Clean up or delete |
| MODIFY | `app/core/config.py` | Remove deprecated path settings |
| MODIFY | `app/main.py` | Remove static mounts, clean startup |
| MODIFY | `app/utils/video.py` | Remove filesystem functions |
| MODIFY | `app/services/video_delete_service.py` | Cloud + DB deletion |
| MODIFY | Multiple service files | Remove job_repo, clean imports |
| MODIFY | `requirements.txt` | Remove filelock if unused |
| MODIFY | `README.md` | Update architecture docs |

### Verification Checklist

- [ ] No references to `static/audios/`, `static/moments/`, `static/transcripts/`, `static/thumbnails/`, `static/moment_clips/` in Python code
- [ ] No `json.load()` or `json.dump()` calls for data persistence (OK for API request/response parsing)
- [ ] No `FileLock` usage
- [ ] No `JobRepository` instantiation
- [ ] `BaseRepository` (file-based) not imported anywhere
- [ ] Video deletion works via cascade deletes + GCS cleanup
- [ ] Application starts cleanly with no deprecation warnings
- [ ] All endpoints work correctly
- [ ] Linter passes with no errors

### AI Agent Prompt

```
CONTEXT:
I've completed a 11-phase migration of VideoMoments from local file storage to GCS + PostgreSQL. All data now lives in cloud storage (videos, clips, thumbnails) and PostgreSQL (metadata, transcripts, moments, pipeline history). This final phase removes all legacy code.

Application at /Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/.

CURRENT STATE:
- All data operations use PostgreSQL (repositories in app/repositories/*_db_repository.py)
- All files served from GCS via signed URLs
- Temp files managed by temp_file_manager.py with auto-cleanup
- BUT: Old file-based code still exists (deprecated but not removed)
- Old static/ directories may still exist
- JobRepository (deprecated) still instantiated in some services
- FileRepository (base.py) still exists
- Old filesystem-based video utilities still in video.py

FILES TO READ AND AUDIT:
- app/repositories/job_repository.py (DELETE this)
- app/repositories/base.py (DELETE this)
- app/repositories/moments_repository.py (verify it delegates to DB or DELETE)
- app/repositories/transcript_repository.py (verify it delegates to DB or DELETE)
- app/core/config.py (remove deprecated path settings)
- app/main.py (clean up remaining static mounts)
- app/utils/video.py (remove filesystem scan functions)
- app/services/video_delete_service.py (update for cloud+DB deletion)
- requirements.txt (remove filelock if unused)
- Search across ALL Python files for: "job_repo", "JobRepository", "BaseRepository", "FileLock", "json.load", "json.dump", "static/audios", "static/moments", "static/transcripts", "static/thumbnails", "static/moment_clips", "get_video_files", "get_video_by_filename"

TASK:
1. Delete app/repositories/job_repository.py entirely

2. Delete app/repositories/base.py entirely  

3. Clean up app/repositories/moments_repository.py:
   - If it delegates to moment_db_repository.py, keep it as a thin wrapper
   - If it still has file-based code, rewrite to delegate to DB repo
   - Or delete it if nothing imports from it (check first)

4. Clean up app/repositories/transcript_repository.py:
   - Same as above

5. Search ALL .py files for "job_repo" and "JobRepository":
   - Remove all instantiations: job_repo = JobRepository()
   - Remove all calls to job_repo methods
   - Remove imports of JobRepository
   - Files likely affected: audio_service.py, transcript_service.py, generation_service.py, refinement_service.py, pipeline_worker.py

6. Search ALL .py files for "BaseRepository":
   - Remove all imports and usage
   
7. Search ALL .py files for "FileLock" and "filelock":
   - Remove imports and usage
   - If no more usage, remove "filelock" from requirements.txt

8. In app/core/config.py:
   - Remove or comment out: videos_dir, audios_dir, transcripts_dir, moments_dir, thumbnails_dir, moment_clips_dir, url_registry_file
   - These paths are no longer the source of truth
   - Keep static_dir if still needed

9. In app/main.py:
   - Remove any remaining static file mounts (audios, transcripts, thumbnails, moment_clips)
   - Should already be mostly done from earlier phases but do a final check

10. In app/utils/video.py:
    - Remove get_video_files() (filesystem scan)
    - Remove get_video_by_filename() (filesystem lookup)  
    - Remove get_video_by_id() that uses filesystem
    - Remove get_videos_directory() that returns static/videos path
    - Keep only DB-backed functions added in Phase 2

11. Rewrite app/services/video_delete_service.py:
    - Remove ALL local file deletion methods (_delete_video_file, _delete_audio_file, _delete_transcript_file, _delete_moments_file, _delete_thumbnail_file, _delete_clips)
    - Replace with:
      a. Delete from database: DELETE FROM videos WHERE identifier = '{id}' (CASCADE handles all related records)
      b. Delete from GCS: Remove video blob, clip blobs, thumbnail blobs
      c. Clean up temp files if any exist for this video
    - The SQL CASCADE automatically deletes: transcripts, moments, clips, thumbnails, pipeline_history

12. Clean up imports across all modified files:
    - Remove unused imports (json, Path where only used for file I/O, os for file ops)
    - Run: search for "import json" and verify json is still needed in each file
    - Remove any "from pathlib import Path" where Path is no longer used

13. Update README.md:
    - Document new architecture: PostgreSQL + GCS + Redis
    - Document required environment variables: DATABASE_URL, GCS_BUCKET_NAME, GCS_SERVICE_ACCOUNT_FILE, REDIS_HOST
    - Document temp file system and cleanup
    - Remove references to static/ file directories
    - Add setup instructions for PostgreSQL + Alembic migrations

IMPORTANT:
- Before deleting any file, search the entire codebase for imports of that file
- Test the application after changes: start it up, hit /health, verify key endpoints work
- The CASCADE deletes in PostgreSQL are critical -- verify they work for video deletion
- Don't remove json module if it's used for API request/response handling (only remove if used for file I/O)
```

---

## Dependency Graph

```
┌─────────────────────────────────────────────────────────────────────┐
│                        PHASE DEPENDENCIES                            │
│                                                                       │
│  Phase 1 ─────────► Phase 2 ─────────► Phase 3                      │
│  (DB Setup)         (Videos)           (Streaming)                   │
│                       │                                               │
│                       ├──────────────► Phase 4                       │
│                       │                (Transcripts)                  │
│                       │                  │                            │
│                       │                  ▼                            │
│                       │               Phase 5                        │
│                       │                (Prompts/Configs)              │
│                       │                  │                            │
│                       │                  ▼                            │
│                       │               Phase 6                        │
│                       │                (Moments)                     │
│                       │                  │                            │
│                       │                  ▼                            │
│                       │               Phase 7                        │
│                       │                (Clips)                       │
│                       │                  │                            │
│                       │                  ▼                            │
│                       │               Phase 8                        │
│                       │                (Thumbnails)                   │
│                       │                                               │
│                       ├──────────────► Phase 9                       │
│                       │                (Pipeline History)             │
│                       │                                               │
│                       └──────────────► Phase 10                      │
│                                        (URL Registry)                │
│                                                                       │
│  Phases 3-10 ─────► Phase 11                                        │
│                      (Temp Files)                                    │
│                         │                                             │
│                         ▼                                             │
│                      Phase 12                                        │
│                      (Cleanup)                                       │
│                                                                       │
│  PARALLELIZABLE: Phase 3 + Phase 4 can run in parallel               │
│  PARALLELIZABLE: Phase 9 + Phase 10 can run in parallel              │
└─────────────────────────────────────────────────────────────────────┘
```

### What Can Run In Parallel

| Parallel Group | Phases | Reason |
|----------------|--------|--------|
| Group A | Phase 3 + Phase 4 | Both depend only on Phase 2, no interdependency |
| Group B | Phase 9 + Phase 10 | Both depend on Phase 2, independent of Phases 6-8 |

---

## Risk & Rollback Strategy

### Risk Matrix

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Data loss during migration | Low | Critical | Run migration scripts before switching code; keep JSON files as backups |
| GCS signed URL expiry mid-playback | Medium | Medium | Frontend auto-refreshes URL on error; configurable expiry |
| Database connection failures | Low | High | Connection pooling, health checks, Redis fallback for status |
| Slow video start (GCS latency) | Medium | Low | GCS CDN, regional buckets, pregenerate signed URLs |
| Migration script bugs | Medium | Medium | Run on staging first; scripts are idempotent (skip existing) |

### Rollback Plan

Each phase can be rolled back independently:

1. **Phase 1**: Drop all tables, remove Alembic. Application unchanged.
2. **Phase 2**: Delete from videos table. Local files still exist.
3. **Phase 3**: Revert stream endpoint to local file serving. Frontend unchanged.
4. **Phase 4**: Revert transcript service to JSON reads. JSON files still exist.
5. **Phase 5**: Drop prompts + generation_configs tables. Generation still works without them.
6. **Phase 6**: Revert moments service to JSON reads. JSON files still exist.
7. **Phase 7**: Revert clip service to local files. Local clips still exist until Phase 12.
8. **Phase 8**: Revert thumbnail service to local files.
9. **Phase 9**: Revert pipeline history to Redis reads.
10. **Phase 10**: Restore url_registry.py from git.
11. **Phase 11**: Revert to static/ directories.
12. **Phase 12**: Restore deleted files from git.

### Key Backup Strategy

**Before starting migration:**
- Back up `static/` directory entirely
- Back up Redis data (RDB snapshot)
- Keep all JSON files until Phase 12 is complete and verified
- Migration scripts are idempotent -- safe to re-run

---

## Summary

| Phase | What Moves | From | To |
|-------|------------|------|-----|
| 1 | Nothing (infrastructure) | N/A | PostgreSQL tables created |
| 2 | Video files + metadata | `static/videos/` + filesystem | GCS + `videos` table |
| 3 | Video streaming | Local file serving | GCS signed URL redirect |
| 4 | Transcript data | `static/transcripts/*.json` | `transcripts` table |
| 5 | AI config data | Ephemeral/inline | `prompts` + `generation_configs` tables |
| 6 | Moment data | `static/moments/*.json` | `moments` table |
| 7 | Clip files + metadata | `static/moment_clips/` | GCS + `clips` table |
| 8 | Thumbnail files + metadata | `static/thumbnails/` | GCS + `thumbnails` table |
| 9 | Pipeline history | Redis | `pipeline_history` table |
| 10 | URL→video mapping | `static/url_registry.json` | `videos.source_url` column |
| 11 | Temp file management | Ad-hoc in `static/` | Structured `temp/` with auto-cleanup |
| 12 | Legacy code removal | Dead code in codebase | Clean codebase |

**Total: 12 phases, each independently executable by an AI coding agent with the provided prompts.**

---

*End of Migration Plan*
