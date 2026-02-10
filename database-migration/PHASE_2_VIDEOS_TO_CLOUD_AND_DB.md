# Phase 2: Videos to Cloud + Database

**Phase Status:** Not Started  
**Created:** February 8, 2026  
**Prerequisite:** Phase 1 (Database Foundation) must be complete  
**Working Directory:** `moments-backend/`  
**Risk Level:** Medium (modifies the pipeline and video listing endpoints)

---

## What Is This Phase About?

Right now, videos in VideoMoments are stored and discovered like this:

1. **Videos live on your local disk** at `moments-backend/static/videos/`
2. **To list all videos**, the app scans that folder and lists every `.mp4` file it finds
3. **To find a specific video**, the app looks for `static/videos/{video_id}.mp4`
4. **Video metadata** (duration, codec, resolution) is extracted on-the-fly using OpenCV every time someone asks for it
5. **URL-to-video mapping** is tracked in a JSON file (`static/url_registry.json`)
6. **When a video is downloaded from a URL**, it is saved directly to `static/videos/`

This approach has several problems:

| Problem | Impact |
|---------|--------|
| Filesystem scan is slow | Every `GET /api/videos` call scans the entire directory |
| No persistent metadata | Duration/codec/resolution is re-computed every request |
| Single-machine storage | Videos are only available on the machine they were downloaded to |
| No structured querying | Can't efficiently search/filter/sort videos |
| JSON URL registry is fragile | File locks, no atomic operations, corruption risk |

**Phase 2 fixes all of this by:**

1. **Uploading all existing videos to Google Cloud Storage (GCS)** -- so they are available from anywhere
2. **Registering every video in the PostgreSQL `videos` table** -- with all metadata stored once
3. **Changing the API endpoints** to read from the database instead of scanning the filesystem
4. **Modifying the pipeline** so new video downloads are also uploaded to GCS and registered in the database

**After Phase 2, the `videos` database table is the single source of truth for what videos exist.**

---

## What Does NOT Change

Phase 2 is carefully scoped. These things remain untouched:

- **Video streaming** -- still served from local files (Phase 3 will change this)
- **Moments, transcripts, clips** -- still JSON files and Redis (Phase 4-9)
- **The frontend** -- sees the same API responses, just sourced from DB instead of filesystem
- **Thumbnails** -- still served from `static/thumbnails/`
- **Pipeline stages** (audio extraction, transcription, AI generation, clipping) -- unchanged
- **Redis** -- still used for pipeline status, job locks, etc.

---

## The Big Picture: Before vs. After

### Before Phase 2

```
Frontend                  Backend                    Storage
────────                  ───────                    ───────
GET /api/videos  ──────►  Scan static/videos/ dir    Local disk only
                          For each .mp4 file:          └── static/videos/
                            - Extract metadata via       ├── motivation.mp4
                              OpenCV (every time)        ├── BillGates.mp4
                            - Check audio/transcript     └── Dogs-Playing.mp4
                          Return list
```

### After Phase 2

```
Frontend                  Backend                    Storage
────────                  ───────                    ───────
GET /api/videos  ──────►  SELECT * FROM videos       PostgreSQL (metadata)
                          Return list from DB          └── videos table
                                                         ├── id=1, identifier=motivation, cloud_url=gs://...
                                                         ├── id=2, identifier=BillGates, cloud_url=gs://...
                                                         └── id=3, identifier=Dogs-Playing, cloud_url=gs://...

                                                     GCS (files)
                                                       └── videos/
                                                           ├── motivation/motivation.mp4
                                                           ├── BillGates/BillGates.mp4
                                                           └── Dogs-Playing/Dogs-Playing.mp4

                                                     Local disk (still present as fallback)
                                                       └── static/videos/ (unchanged)
```

---

## Prerequisites (Before You Start)

### 1. Phase 1 Must Be Complete

Verify by running:

```bash
curl http://localhost:7005/health
# Must return: {"status": "healthy", "redis": "connected", "database": "connected"}
```

And verify the `videos` table exists:

```bash
psql -U postgres -d videomoments -c "\d videos"
# Should show all columns of the videos table
```

### 2. GCS Access Must Work

The app already uses GCS for audio and clip uploads. Verify your credentials work:

```bash
# The app's GCS config is in app/core/config.py:
# gcs_bucket_name = "rumble-ai-bucket-1"
# gcs_service_account_file = (path to JSON key file)
```

### 3. Videos Must Exist Locally

The migration script will scan `static/videos/` and upload everything to GCS. Make sure your videos are there:

```bash
ls moments-backend/static/videos/
# Should show .mp4 files
```

---

## Step-by-Step Breakdown

### Step 1: Add GCS Video Prefix to Config

**What:** Add one new setting to `app/core/config.py`  
**Why:** GCS organizes files by "prefix" (like folders). Audio uses `audio/`, clips use `clips/`, and now videos will use `videos/`  
**File Modified:** `app/core/config.py`

**New setting:**

| Setting | Default Value | What It Means |
|---------|--------------|---------------|
| `gcs_videos_prefix` | `"videos/"` | All video files will be uploaded to `gs://rumble-ai-bucket-1/videos/...` |

**Where it goes in the Settings class** (alongside the existing GCS settings):

```python
# Existing GCS config (already there):
gcs_bucket_name: str = "rumble-ai-bucket-1"
gcs_audio_prefix: str = "audio/"
gcs_clips_prefix: str = "clips/"

# New (add this):
gcs_videos_prefix: str = "videos/"
```

**GCS path structure after upload:**

```
gs://rumble-ai-bucket-1/
├── audio/              ← Already exists (audio files)
│   └── motivation/
│       └── motivation.wav
├── clips/              ← Already exists (video clips)
│   └── motivation/
│       └── motivation_abc123_clip.mp4
└── videos/             ← NEW (full video files)
    └── motivation/
        └── motivation.mp4
```

---

### Step 2: Extend GCSUploader with Video Upload Methods

**What:** Add 2 new methods to the existing `GCSUploader` class  
**Why:** The uploader already handles audio and clips. We are extending it to also handle full video files  
**File Modified:** `app/services/pipeline/upload_service.py`

#### Current State of GCSUploader

The `GCSUploader` class already has:
- `upload_audio()` -- uploads audio to `audio/{video_id}/{video_id}.wav`
- `upload_clip()` -- uploads clips to `clips/{video_id}/{video_id}_{moment_id}_clip.mp4`
- `upload_all_clips()` -- batch uploads all clips for a video
- `generate_signed_url()` -- creates temporary download URLs for GCS objects
- `_upload_file_with_retry()` -- internal method with retry logic and progress tracking
- `delete_clips_for_video()` -- deletes all clips for a video from GCS

#### New Methods to Add

##### `upload_video(local_path, identifier)` -- Upload a video file to GCS

| Parameter | Type | Description |
|-----------|------|-------------|
| `local_path` | `Path` | Path to the local video file (e.g., `static/videos/motivation.mp4`) |
| `identifier` | `str` | Video identifier (e.g., `"motivation"`) |
| Returns | `Tuple[str, str]` | `(gcs_path, signed_url)` |

**What it does:**
1. Checks that the local file exists
2. Constructs the GCS path: `videos/{identifier}/{filename}` (e.g., `videos/motivation/motivation.mp4`)
3. Checks if the file already exists in GCS with matching MD5 (skip re-upload if identical)
4. Uploads the file with retry logic (same pattern as `upload_audio()`)
5. Generates and returns a signed URL

**Why the MD5 check?** Video files can be large (several GB). If the exact same file is already in GCS, we skip the upload entirely. This is identical to how `upload_audio()` already works -- we're reusing the same pattern.

##### `get_video_signed_url(identifier, filename)` -- Get a signed URL for an existing video

| Parameter | Type | Description |
|-----------|------|-------------|
| `identifier` | `str` | Video identifier (e.g., `"motivation"`) |
| `filename` | `str` | Video filename (e.g., `"motivation.mp4"`) |
| Returns | `Optional[str]` | Signed URL or `None` if blob doesn't exist |

**What it does:**
1. Constructs the GCS path: `videos/{identifier}/{filename}`
2. Checks if the blob exists
3. If yes, generates a signed URL (valid for `gcs_signed_url_expiry_hours`, default 1 hour)
4. If no, returns `None`

**Why do we need this?** In Phase 3, when the frontend asks to stream a video, we'll generate a signed URL and redirect the browser directly to GCS. This method prepares for that. Even in Phase 2, we use it in the migration script to verify uploads.

#### Why Extend GCSUploader Instead of Creating a New Class?

| Approach | Pros | Cons |
|----------|------|------|
| **Extend GCSUploader** (chosen) | Reuses existing GCS client initialization, credentials handling, retry logic, signed URL generation. Single place for all GCS operations | Class grows larger over time |
| **Create new VideoGCSUploader** | Cleaner separation of concerns | Duplicates GCS client setup, credentials logic, and retry utilities. Two classes to maintain |

**Decision: Extend.** The uploader's core responsibility is "upload files to GCS and generate signed URLs." Videos are just another file type. The existing retry logic, progress tracking, and MD5 deduplication all apply equally to videos.

---

### Step 3: Create the Video Database Repository

**What:** Create a new file `app/repositories/video_db_repository.py` with async CRUD operations  
**Why:** This is the bridge between application code and the `videos` database table  
**File Created:** `app/repositories/video_db_repository.py`

#### What Is a Repository?

A repository is a design pattern that centralizes all database operations for a specific entity. Instead of writing SQL queries directly in your API endpoints or services, you call repository methods:

```python
# Without repository (SQL scattered everywhere):
result = await db.execute(select(Video).where(Video.identifier == "motivation"))
video = result.scalar_one_or_none()

# With repository (clean, reusable):
video = await video_repo.get_by_identifier(session, "motivation")
```

**Benefits:**
- All video-related database logic is in one file
- Easy to test (mock the repository instead of the database)
- If you change the query logic, you change it in one place
- Endpoints stay clean and focused on HTTP concerns

#### Current Repository Pattern

The project already has repositories, but they are **file-based** (JSON):

```
app/repositories/
├── base.py                    ← BaseRepository (reads/writes JSON files)
├── moments_repository.py      ← Moments stored in JSON
└── transcript_repository.py   ← Transcripts stored in JSON
```

The new `video_db_repository.py` will be **database-based** (SQLAlchemy). It doesn't extend `BaseRepository` because the base class is designed for JSON file operations.

#### Methods in the Repository

| Method | SQL Equivalent | When It's Used |
|--------|---------------|----------------|
| `create(session, identifier, cloud_url, ...)` | `INSERT INTO videos ...` | After uploading a new video to GCS |
| `get_by_identifier(session, identifier)` | `SELECT * FROM videos WHERE identifier = ?` | API: `GET /api/videos/{video_id}` |
| `get_by_id(session, id)` | `SELECT * FROM videos WHERE id = ?` | Internal: look up by numeric DB ID |
| `get_by_source_url(session, source_url)` | `SELECT * FROM videos WHERE source_url = ?` | Pipeline: check if URL was already downloaded |
| `list_all(session)` | `SELECT * FROM videos ORDER BY created_at DESC` | API: `GET /api/videos` |
| `delete_by_identifier(session, identifier)` | `DELETE FROM videos WHERE identifier = ?` | When deleting a video |
| `update(session, id, **fields)` | `UPDATE videos SET ... WHERE id = ?` | When updating video metadata |

#### Key Design Decisions

**Why do all methods accept a `session` parameter?**

In FastAPI, database sessions are managed by the `get_db()` dependency (created in Phase 1). The session is created at the start of a request, used for all operations in that request, and then either committed (success) or rolled back (error). By passing the session explicitly:

- The repository doesn't control transaction boundaries (the endpoint does)
- Multiple repository calls within one request share the same session/transaction
- Testing is easier (you can pass a test session)

```python
# The endpoint controls the transaction:
@router.post("/api/videos")
async def create_video(db: AsyncSession = Depends(get_db)):
    # Same session used for both operations
    video = await video_repo.create(db, identifier="test", cloud_url="gs://...")
    await video_repo.update(db, video.id, title="My Video")
    # Session is committed automatically at the end (or rolled back on error)
```

**Why `get_by_source_url()` for duplicate detection?**

Currently, the `URLRegistry` (a JSON file) tracks which URLs have been downloaded. The `videos.source_url` column replaces this:

```
Before: URLRegistry.lookup_by_url("https://youtube.com/watch?v=abc")
         → Reads JSON file → Returns RegistryEntry or None

After:  video_repo.get_by_source_url(session, "https://youtube.com/watch?v=abc")
         → Database query (indexed) → Returns Video or None
```

The database approach is:
- Faster (indexed column vs JSON file scan)
- Atomic (no file lock needed)
- Reliable (no corruption risk)
- Queryable (you can search/filter URLs easily)

---

### Step 4: Create the One-Time Migration Script

**What:** Create `scripts/migrate_videos_to_cloud.py` -- a standalone script that migrates all existing local videos to GCS + database  
**Why:** Your existing videos in `static/videos/` need to be uploaded to GCS and registered in the database  
**File Created:** `scripts/migrate_videos_to_cloud.py`

#### What This Script Does (High Level)

```
For each video file in static/videos/:
    1. Extract metadata using ffprobe (duration, codecs, resolution, fps, file size)
    2. Upload to GCS at videos/{identifier}/{filename}
    3. Insert a row into the videos table
    4. Print progress
```

#### Why ffprobe Instead of OpenCV (cv2)?

The current codebase uses `cv2.VideoCapture()` to get video duration in the endpoints. The migration script will use `ffprobe` instead. Here's why:

| Feature | OpenCV (cv2) | ffprobe |
|---------|-------------|---------|
| **Duration** | Calculates from frame count / FPS (can be inaccurate for variable-rate videos) | Reads directly from container metadata (accurate) |
| **Video codec** | Not easily accessible | Returns e.g., "h264", "vp9" |
| **Audio codec** | Not available | Returns e.g., "aac", "opus" |
| **Resolution** | Available via `CAP_PROP_FRAME_WIDTH/HEIGHT` | Available |
| **Frame rate** | Available via `CAP_PROP_FPS` | Available (more accurate for variable-rate) |
| **File size** | Must use `os.stat()` separately | Can include via `format=size` |
| **Speed** | Must open entire video, can be slow | Reads only metadata headers (fast) |
| **Dependency** | Requires `opencv-python` (already installed) | Requires FFmpeg installed on system (usually already there since clipping uses it) |

**Decision: ffprobe for the migration script.** We need all metadata fields (codecs, resolution, fps), and ffprobe provides them all in a single fast call.

**ffprobe command used:**

```bash
ffprobe -v quiet -print_format json -show_format -show_streams video.mp4
```

This returns JSON with all metadata. The script parses it to extract the fields we need for the `videos` table.

#### Script Flow (Detailed)

```
1. Initialize:
   - Create async database session (using session.py from Phase 1)
   - Create GCSUploader instance
   - Scan static/videos/ for all video files

2. For each video file:
   a. identifier = file stem (e.g., "motivation" from "motivation.mp4")
   
   b. Check if already in database:
      SELECT * FROM videos WHERE identifier = 'motivation'
      If found → Skip (already migrated)
   
   c. Extract metadata via ffprobe subprocess:
      - duration_seconds: 120.5
      - video_codec: "h264"
      - audio_codec: "aac"
      - resolution: "1920x1080"
      - frame_rate: 30.0
      - file_size_kb: 50000
   
   d. Upload to GCS:
      Upload motivation.mp4 → gs://rumble-ai-bucket-1/videos/motivation/motivation.mp4
      Returns (gcs_path, signed_url)
   
   e. Insert into database:
      INSERT INTO videos (identifier, cloud_url, duration_seconds, ...)
      VALUES ('motivation', 'gs://rumble-ai-bucket-1/videos/motivation/motivation.mp4', 120.5, ...)
   
   f. Print: "Migrated 1/9: motivation.mp4 (120.5s, 48.8 MB)"

3. Print summary:
   "Migration complete: 9 videos migrated, 0 skipped, 0 failed"
```

#### Why a Standalone Script Instead of an Endpoint?

| Approach | Pros | Cons |
|----------|------|------|
| **Standalone script** (chosen) | Runs independently, can be re-run safely, clear start/end, no request timeout limits | Needs to set up its own DB session |
| **API endpoint** | Convenient, uses existing DB session | HTTP timeout risk for large uploads, could be triggered accidentally, mixes concerns |
| **Alembic data migration** | Versioned, runs with schema migrations | Alembic is for schema changes, not data; mixing concerns; hard to debug |

**Decision: Standalone script.** It's a one-time operation that could take minutes (uploading multiple GB of video). It's idempotent (skip if already migrated), so it's safe to re-run.

#### Running the Script

```bash
cd moments-backend
python -m scripts.migrate_videos_to_cloud
```

Or:

```bash
cd moments-backend
python scripts/migrate_videos_to_cloud.py
```

---

### Step 5: Modify the Pipeline's Video Download Stage

**What:** Update `execute_video_download()` in `app/services/pipeline/orchestrator.py`  
**Why:** When the pipeline downloads a new video from a URL, it should also upload it to GCS and register it in the database  
**File Modified:** `app/services/pipeline/orchestrator.py`

#### Current Flow of `execute_video_download()`

```
1. Get video_url from config
2. Set dest_path = static/videos/{video_id}.mp4
3. If file already exists locally → skip
4. Download video from URL to dest_path
5. Register in URLRegistry (JSON file)
```

#### New Flow After Phase 2

```
1. Get video_url from config
2. Check if video already exists in DATABASE (by source_url)
   → If found: skip download entirely, use existing record
3. If not in DB, check if local file exists
   → If exists: use local file (still need to upload to GCS and register in DB)
4. If not local either:
   a. Download video from URL to static/videos/{video_id}.mp4
5. Extract metadata via ffprobe (duration, codecs, resolution, fps, file size)
6. Upload to GCS: gs://bucket/videos/{video_id}/{video_id}.mp4
7. Insert into videos table (identifier, cloud_url, source_url, metadata)
8. Register in URLRegistry (keep for backward compatibility during transition)
```

#### Key Change: Database Duplicate Detection Replaces Filesystem Check

**Before:**
```python
# Current logic:
if dest_path.exists():
    logger.info("Video already exists, skipping download")
    return
```

**After (added before the filesystem check):**
```python
# New logic: check database FIRST
video_record = await video_repo.get_by_source_url(session, video_url)
if video_record:
    logger.info(f"Video already in DB: {video_record.identifier}, skipping download")
    return
```

**Why check the database first?**

The filesystem check only works on the current machine. If you deployed to a new server or cleared local files, the video would be re-downloaded even though it's already in GCS. The database check is machine-independent.

#### How Does the Orchestrator Get a Database Session?

The pipeline orchestrator runs as a background worker, not as part of a FastAPI request. So it can't use the `get_db()` FastAPI dependency. Instead, it creates sessions directly:

```python
from app.database.session import async_session_factory

async with async_session_factory() as session:
    video = await video_repo.create(session, identifier=video_id, ...)
    await session.commit()
```

This is the correct pattern for background tasks -- the FastAPI dependency (`get_db()`) is only for request-handling code.

#### Why Keep the URLRegistry During Transition?

We still call `registry.register()` after the database insert. This is for **backward compatibility**. Other parts of the codebase (that haven't been migrated yet) may still read the URL registry. The URL registry will be fully removed in Phase 10.

**Transition strategy:**
- Phase 2: Write to both DB and URL registry
- Phase 10: Remove URL registry entirely (all lookups will use `videos.source_url`)

---

### Step 6: Modify the Video API Endpoints

**What:** Change `GET /api/videos` and `GET /api/videos/{video_id}` to read from the database  
**Why:** These endpoints currently scan the filesystem, which is slow and doesn't provide rich metadata  
**File Modified:** `app/api/endpoints/videos.py`

#### Current Endpoints

| Endpoint | Current Behavior |
|----------|-----------------|
| `GET /api/videos` | Calls `get_video_files()` → scans `static/videos/` → for each file, checks audio/transcript existence → returns list |
| `GET /api/videos/{video_id}` | Calls `get_video_files()` → finds file by stem match → returns metadata |
| `GET /api/videos/{video_id}/stream` | Finds local file → streams bytes with range request support |
| `GET /api/videos/{video_id}/thumbnail` | Finds local file → generates/returns thumbnail |

#### What Changes

| Endpoint | New Behavior | Changed? |
|----------|-------------|----------|
| `GET /api/videos` | Queries `videos` DB table → returns list with metadata | **Yes** |
| `GET /api/videos/{video_id}` | Queries DB by `identifier` → returns video record | **Yes** |
| `GET /api/videos/{video_id}/stream` | **No change** (Phase 3 will update this) | No |
| `GET /api/videos/{video_id}/thumbnail` | **No change** | No |

#### Detailed Changes to `GET /api/videos`

**Before:**
```python
@router.get("/videos", response_model=list[VideoResponse])
async def list_videos():
    videos_dir = get_videos_directory()
    video_files = get_video_files()          # Scans filesystem
    videos = []
    for video_file in video_files:
        video_id = video_file.stem
        thumbnail_url = get_thumbnail_url(video_file.name)
        has_audio = check_audio_exists(video_file.name)         # Checks filesystem
        has_transcript = check_transcript_exists(audio_filename)  # Checks filesystem
        videos.append(VideoResponse(...))
    return videos
```

**After:**
```python
@router.get("/videos")
async def list_videos(db: AsyncSession = Depends(get_db)):
    videos = await video_repo.list_all(db)   # Single DB query
    result = []
    for video in videos:
        # has_transcript and has_moments are computed from relationships
        # (or with a simple EXISTS subquery)
        result.append({
            "id": video.identifier,
            "filename": f"{video.identifier}.mp4",
            "title": video.title or video.identifier.replace("-", " ").replace("_", " ").title(),
            "thumbnail_url": get_thumbnail_url(f"{video.identifier}.mp4"),
            "has_audio": check_audio_exists(f"{video.identifier}.mp4"),  # Still filesystem (until Phase 4)
            "has_transcript": check_transcript_exists(f"{video.identifier}.mp4"),  # Still filesystem
            "duration_seconds": video.duration_seconds,
            "cloud_url": video.cloud_url,
            "source_url": video.source_url,
            "created_at": video.created_at.isoformat(),
        })
    return result
```

**Key differences:**
- `get_video_files()` (filesystem scan) is replaced with `video_repo.list_all(db)` (DB query)
- Duration is read from the database (stored once during migration), not computed every time
- `cloud_url` and `source_url` are now part of the response
- `has_audio` and `has_transcript` still check the filesystem -- this will change in Phase 4

**Why not compute `has_transcript` from the database?** The transcripts table is populated in Phase 4. Until then, the database has no transcript records, so we must still check the filesystem. This is a **deliberate incremental approach** -- each phase only changes what it's responsible for.

#### Adding `Depends(get_db)` to Endpoints

The `get_db` dependency (created in Phase 1) is how FastAPI gives endpoints a database session:

```python
from app.database.dependencies import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

@router.get("/videos")
async def list_videos(db: AsyncSession = Depends(get_db)):
    # 'db' is a ready-to-use database session
    # FastAPI creates it before calling this function
    # and closes it automatically after the response is sent
    ...
```

**What `Depends(get_db)` does under the hood:**
1. FastAPI sees `Depends(get_db)` in the function signature
2. Before calling `list_videos()`, FastAPI calls `get_db()` which creates an `AsyncSession`
3. The session is passed as the `db` parameter
4. After `list_videos()` returns (or raises), FastAPI closes the session

This is the standard FastAPI dependency injection pattern. Every endpoint that needs database access will have `db: AsyncSession = Depends(get_db)`.

#### Response Schema Considerations

The current `VideoResponse` Pydantic model has:

```python
class VideoResponse(BaseModel):
    id: str
    filename: str
    title: str
    thumbnail_url: Optional[str] = None
    has_audio: Optional[bool] = None
    has_transcript: Optional[bool] = None
```

Phase 2 may need to extend this to include new fields:

```python
class VideoResponse(BaseModel):
    id: str
    filename: str
    title: str
    thumbnail_url: Optional[str] = None
    has_audio: Optional[bool] = None
    has_transcript: Optional[bool] = None
    # New fields from database:
    duration_seconds: Optional[float] = None
    cloud_url: Optional[str] = None
    source_url: Optional[str] = None
    created_at: Optional[str] = None
```

These new fields are optional (with defaults) so the frontend isn't forced to handle them immediately. The frontend can start using them when ready.

**Pros and cons of extending `VideoResponse` vs creating a new schema:**

| Approach | Pros | Cons |
|----------|------|------|
| **Extend VideoResponse** (chosen) | Single schema, backward compatible (new fields optional), simpler | Schema grows over time |
| **Create VideoDBResponse** | Clean separation between old and new | Two schemas for the same entity, confusing. Endpoints need to know which to use |

---

### Step 7: Add Deprecation Warnings to Filesystem Functions

**What:** Add warnings to `get_video_files()` and `get_video_by_id()` in `app/utils/video.py`  
**Why:** These functions will eventually be removed (they scan the filesystem). Deprecation warnings alert other developers (and future you) that these functions are being phased out  
**File Modified:** `app/utils/video.py`

#### What Gets Deprecated

| Function | Current Use | Replacement |
|----------|------------|-------------|
| `get_video_files()` | Called by `list_videos()` endpoint, `get_video()` endpoint, `stream_video()` endpoint, `get_thumbnail()` endpoint | `video_repo.list_all()` (for listing), `video_repo.get_by_identifier()` (for lookup) |
| `get_video_by_id()` | Called by `should_skip_stage()` in orchestrator | `video_repo.get_by_identifier()` |
| `get_video_by_filename()` | Called by `execute_audio_extraction()`, `execute_clip_extraction()` in orchestrator | Will be replaced when those stages are migrated |
| `get_videos_directory()` | Called by `execute_video_download()`, endpoints | Will be removed when local storage is eliminated |

#### What a Deprecation Warning Looks Like

```python
import warnings

def get_video_files():
    """Get list of video files from the videos directory.
    
    .. deprecated::
        Use video_db_repository.list_all() for database-backed video listing.
        This function will be removed after all phases are complete.
    """
    warnings.warn(
        "get_video_files() is deprecated. Use video_db_repository.list_all() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    # ... existing implementation unchanged ...
```

**Why keep them working?** The stream and thumbnail endpoints still need local file paths. These endpoints are not being changed in Phase 2 (stream changes in Phase 3, thumbnails in Phase 8). So the functions must continue to work -- they just warn developers that they should not write new code using them.

---

### Step 8: Run the Migration Script

**What:** Execute the script to upload all videos to GCS and populate the database  
**Why:** This is the actual data migration -- the point where your local videos become cloud-backed and database-tracked

#### Before Running

Verify these counts match:

```bash
# Count local video files:
ls moments-backend/static/videos/*.mp4 | wc -l

# Count videos in database (should be 0):
psql -U postgres -d videomoments -c "SELECT count(*) FROM videos;"
```

#### Running the Script

```bash
cd moments-backend
python -m scripts.migrate_videos_to_cloud
```

Expected output:

```
Starting video migration...
Found 9 video files in static/videos/

Migrating 1/9: motivation.mp4
  - ffprobe: 120.5s, h264/aac, 1920x1080, 30.0fps, 48.8 MB
  - GCS upload: gs://rumble-ai-bucket-1/videos/motivation/motivation.mp4 (12.3s)
  - Database: inserted id=1, identifier=motivation
  ✓ Done

Migrating 2/9: BillGates.mp4
  - ffprobe: 245.0s, h264/aac, 1280x720, 30.0fps, 95.2 MB
  - GCS upload: gs://rumble-ai-bucket-1/videos/BillGates/BillGates.mp4 (25.1s)
  - Database: inserted id=2, identifier=BillGates
  ✓ Done

... (similar for each video) ...

Migration complete:
  Total: 9 videos
  Migrated: 9
  Skipped: 0 (already in database)
  Failed: 0
```

#### After Running

```bash
# Count videos in database (should match local count):
psql -U postgres -d videomoments -c "SELECT count(*) FROM videos;"

# List all videos in database:
psql -U postgres -d videomoments -c "SELECT identifier, duration_seconds, video_codec, resolution FROM videos;"

# Verify GCS uploads (using gsutil):
gsutil ls gs://rumble-ai-bucket-1/videos/
```

#### What If It Fails Partway Through?

The script is **idempotent** -- if you run it again, it skips videos that are already in the database. So if it fails on video 5 of 9, just fix the issue and re-run. Videos 1-4 will be skipped, and 5-9 will be retried.

---

## How ffprobe Metadata Extraction Works

This section explains the technical details of extracting video metadata, since it's a new concept in this phase.

### What Is ffprobe?

`ffprobe` is a command-line tool that comes with FFmpeg. It reads a video file's header/metadata without decoding the entire video. It's fast (takes ~100ms even for multi-GB files) because it only reads the metadata, not the video frames.

### The Command

```bash
ffprobe -v quiet -print_format json -show_format -show_streams "motivation.mp4"
```

| Flag | What It Does |
|------|-------------|
| `-v quiet` | Suppress diagnostic output (only show the data we asked for) |
| `-print_format json` | Output in JSON format (easy to parse in Python) |
| `-show_format` | Show container-level info (duration, file size, format name) |
| `-show_streams` | Show each stream (video stream, audio stream) with codec info |

### Example Output (Simplified)

```json
{
  "streams": [
    {
      "index": 0,
      "codec_type": "video",
      "codec_name": "h264",
      "width": 1920,
      "height": 1080,
      "r_frame_rate": "30/1"
    },
    {
      "index": 1,
      "codec_type": "audio",
      "codec_name": "aac"
    }
  ],
  "format": {
    "duration": "120.500000",
    "size": "50003456"
  }
}
```

### How We Parse It

```python
import subprocess, json

result = subprocess.run(
    ["ffprobe", "-v", "quiet", "-print_format", "json",
     "-show_format", "-show_streams", str(video_path)],
    capture_output=True, text=True
)
data = json.loads(result.stdout)

# Duration (seconds)
duration = float(data["format"]["duration"])  # 120.5

# File size (KB)
file_size_kb = int(data["format"]["size"]) // 1024  # 48831

# Video codec, resolution, FPS
for stream in data["streams"]:
    if stream["codec_type"] == "video":
        video_codec = stream["codec_name"]          # "h264"
        resolution = f"{stream['width']}x{stream['height']}"  # "1920x1080"
        # Parse frame rate (comes as fraction like "30/1")
        num, den = stream["r_frame_rate"].split("/")
        frame_rate = float(num) / float(den)        # 30.0
    elif stream["codec_type"] == "audio":
        audio_codec = stream["codec_name"]           # "aac"
```

---

## Complete File Map

### New Files Created (2 files)

```
moments-backend/
├── app/
│   └── repositories/
│       └── video_db_repository.py         # Database CRUD for videos
└── scripts/
    └── migrate_videos_to_cloud.py         # One-time migration script
```

### Existing Files Modified (5 files)

```
moments-backend/
├── app/
│   ├── core/
│   │   └── config.py                      # + gcs_videos_prefix setting
│   ├── services/
│   │   └── pipeline/
│   │       ├── orchestrator.py            # + GCS upload + DB insert after download
│   │       └── upload_service.py          # + upload_video(), get_video_signed_url()
│   ├── api/
│   │   └── endpoints/
│   │       └── videos.py                  # list/get from DB instead of filesystem
│   └── utils/
│       └── video.py                       # + deprecation warnings
```

### Files NOT Modified (but referenced)

```
moments-backend/
├── app/
│   ├── database/
│   │   ├── models/video.py                # Used by repository (created in Phase 1)
│   │   ├── session.py                     # Used by migration script (created in Phase 1)
│   │   └── dependencies.py               # Used by endpoints (created in Phase 1)
│   ├── services/
│   │   └── url_registry.py               # Still called (backward compat), removed in Phase 10
│   └── models/
│       └── schemas.py                     # VideoResponse may be extended
```

---

## Data Flow Diagrams

### Flow 1: Migration Script (One-Time)

```
┌─────────────────┐     ┌──────────────┐     ┌───────────────────┐
│  Local Disk      │     │   ffprobe    │     │  GCS               │
│  static/videos/  │────►│  (metadata)  │     │  videos/           │
│  motivation.mp4  │     │  duration    │     │  motivation/       │
│                  │     │  codecs      │     │    motivation.mp4  │
│                  │     │  resolution  │     │                    │
│                  │────►│              │     │                    │
│                  │     └──────────────┘     └───────────────────┘
│                  │            │                       ▲
│                  │            ▼                       │
│                  │     ┌──────────────┐               │
│                  │────►│  GCSUploader │───────────────┘
│                  │     │  upload()    │    Upload file
│                  │     └──────────────┘
│                  │            │
│                  │            ▼
│                  │     ┌──────────────────────────┐
│                  │     │  PostgreSQL               │
│                  │     │  INSERT INTO videos (     │
│                  │     │    identifier,            │
│                  │     │    cloud_url,             │
│                  │     │    duration_seconds,      │
│                  │     │    video_codec, ...       │
│                  │     │  )                        │
│                  │     └──────────────────────────┘
└─────────────────┘
```

### Flow 2: Pipeline Video Download (After Phase 2)

```
Pipeline receives URL: "https://example.com/video.mp4"
                               │
                               ▼
                    ┌──────────────────┐
                    │ Check database:  │
                    │ SELECT * FROM    │
                    │ videos WHERE     │
                    │ source_url = ?   │
                    └────────┬─────────┘
                             │
              ┌──────────────┴──────────────┐
              │ Found?                       │ Not found?
              ▼                              ▼
    ┌──────────────────┐         ┌──────────────────────┐
    │ Skip download    │         │ Download to           │
    │ Use existing     │         │ static/videos/        │
    │ cloud_url        │         │ {video_id}.mp4        │
    │ Return           │         └──────────┬────────────┘
    └──────────────────┘                    │
                                            ▼
                                 ┌──────────────────────┐
                                 │ Extract metadata      │
                                 │ (ffprobe)             │
                                 └──────────┬────────────┘
                                            │
                                            ▼
                                 ┌──────────────────────┐
                                 │ Upload to GCS         │
                                 │ (GCSUploader)         │
                                 └──────────┬────────────┘
                                            │
                                            ▼
                                 ┌──────────────────────┐
                                 │ Insert into database  │
                                 │ (video_db_repository) │
                                 └──────────┬────────────┘
                                            │
                                            ▼
                                 ┌──────────────────────┐
                                 │ Register in URL       │
                                 │ registry (compat)     │
                                 └──────────────────────┘
```

### Flow 3: GET /api/videos (After Phase 2)

```
Frontend              Endpoint              Database
────────              ────────              ────────
GET /api/videos  ──►  list_videos()    ──►  SELECT * FROM videos
                      (with get_db())       ORDER BY created_at DESC
                                            │
                                            ▼
                      ◄───────────────────  [Video rows]
                      │
                      │ For each video:
                      │  - check has_audio (filesystem)
                      │  - check has_transcript (filesystem)
                      │  - get thumbnail_url
                      │
                      ▼
                 ◄──  Return JSON list
```

---

## Key Architectural Decisions and Trade-offs

### 1. Dual-Write Strategy (Database + URL Registry)

**What:** During Phase 2, both the database and URL registry are written to.

**Why not remove the URL registry immediately?**

The URL registry is used by the pipeline's `get_video_id_for_url()` method to determine if a URL has already been downloaded and what `video_id` to assign. Other parts of the code may also reference it. Removing it now would require updating all callers across the codebase -- which violates the "each phase is independent" principle.

| Approach | Pros | Cons |
|----------|------|------|
| **Dual-write** (chosen) | Safe, no breaking changes, easy rollback | Temporary redundancy, extra write per download |
| **Remove registry immediately** | Cleaner, no redundancy | Risky: must find and update ALL callers. If we miss one, things break silently |

**The URL registry is removed in Phase 10** after all callers have been migrated to use the database.

### 2. Local Files Are NOT Deleted

**What:** After uploading to GCS, the local video file in `static/videos/` is kept.

**Why not delete local files after upload?**

- The **stream endpoint** (Phase 3) still serves from local files
- The **audio extraction** stage reads the local video file with FFmpeg
- The **clip extraction** stage reads the local video file with FFmpeg
- Deleting now would break all of these

**When are local files removed?** Phase 11 (Temp File Management) introduces a system where:
- Videos are downloaded to a temp directory (not `static/videos/`)
- Processed files are cleaned up after 24 hours
- `static/videos/` is eventually emptied

### 3. `has_transcript` and `has_audio` Still Check the Filesystem

**What:** Even though we now list videos from the database, we still check `static/transcripts/` and `static/audios/` to determine if a video has a transcript or audio file.

**Why not check the database?**

Because the `transcripts` table is empty -- Phase 4 hasn't run yet. The database knows about videos but not about transcripts. So we still use the filesystem check. This will be fixed in Phase 4 when transcripts are migrated.

**Progression:**
- Phase 2: `has_transcript` → check filesystem
- Phase 4: `has_transcript` → check `transcripts` table (EXISTS subquery)

### 4. Why `identifier` Is Used as the "Video ID" in APIs (Not Numeric `id`)

The `videos` table has two ID columns:

| Column | Type | Example | Used For |
|--------|------|---------|----------|
| `id` | `SERIAL` (auto-increment integer) | 1, 2, 3 | Database foreign keys (efficient joins) |
| `identifier` | `VARCHAR(255)` | "motivation", "BillGates" | API URLs, user-facing operations, backward compatibility |

The API uses `identifier` (not `id`) because:
- The current API uses filename stems as IDs (e.g., `/api/videos/motivation`)
- All existing frontend code uses string identifiers
- Changing to numeric IDs would break every API call
- Identifiers are human-readable and meaningful

The numeric `id` is used internally for database relationships (foreign keys). Users never see it.

---

## Potential Issues and Troubleshooting

| Issue | Cause | Solution |
|-------|-------|---------|
| Migration script fails with "ffprobe not found" | FFmpeg/ffprobe not installed | macOS: `brew install ffmpeg`. Linux: `apt-get install ffmpeg` |
| GCS upload fails with "403 Forbidden" | Service account doesn't have write permissions | Check IAM permissions for the service account on the bucket |
| GCS upload fails with "timeout" | Large video file, slow upload | Increase `gcs_upload_timeout_seconds` in config (default 1800s = 30min) |
| Migration script inserts duplicate rows | Script run multiple times | Should not happen (script checks `get_by_identifier` first). If it does, the `UNIQUE` constraint on `identifier` will prevent duplicates |
| `GET /api/videos` returns empty list | Migration script not run yet | Run `python -m scripts.migrate_videos_to_cloud` |
| `GET /api/videos` returns videos without thumbnails | Thumbnails are still filesystem-based | This is expected. Thumbnails are generated from local video files. If local files exist, thumbnails will work |
| Pipeline download creates duplicate DB entry | `source_url` already exists | `get_by_source_url()` check prevents this. If race condition occurs, the `UNIQUE` constraint on `identifier` catches it |
| Local video files are missing after migration | Someone deleted `static/videos/` | Do not delete local files. They are still needed for streaming (Phase 3) and processing (audio/clip extraction) |

---

## Verification Checklist

| # | Check | How to Verify | Expected Result |
|---|-------|--------------|-----------------|
| 1 | Migration script runs | `python -m scripts.migrate_videos_to_cloud` | All videos migrated without errors |
| 2 | Videos in database | `psql -d videomoments -c "SELECT identifier, cloud_url FROM videos;"` | One row per video with GCS path |
| 3 | Videos in GCS | `gsutil ls gs://rumble-ai-bucket-1/videos/` | One folder per video |
| 4 | API returns from DB | `curl http://localhost:7005/api/videos` | Returns video list with `cloud_url` and `duration_seconds` |
| 5 | Single video lookup | `curl http://localhost:7005/api/videos/motivation` | Returns video with all metadata |
| 6 | Streaming still works | Open frontend, play a video | Video plays from local file |
| 7 | Thumbnails still work | Open frontend, check video cards | Thumbnails display correctly |
| 8 | Pipeline download works | Trigger pipeline with a new URL | New video appears in DB + GCS |
| 9 | Duplicate detection works | Trigger pipeline with same URL | Skips download, uses existing DB record |
| 10 | Local files untouched | `ls moments-backend/static/videos/` | All original files still present |

---

## What Comes Next (Phase 3)

Phase 3 will:
- Replace local video streaming with GCS signed URL redirects
- The frontend's `<video>` tag will load directly from GCS (faster, no backend proxy)
- Add a `/api/videos/{id}/url` endpoint for getting fresh signed URLs
- Add a temp processing directory for FFmpeg operations
- Modify audio and clip extraction to download from GCS when local file is absent

Phase 2 sets up the database records and GCS files that Phase 3 will serve.

---

**Document Status:** Ready for review  
**Schema Reference:** `database/SCHEMA.md` (Table 1: Videos)  
**Migration Plan Reference:** `CLOUD_DATABASE_MIGRATION_PLAN.md` (Phase 2, lines 362-522)
