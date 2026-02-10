# Phase 1: Database Foundation

**Phase Status:** Not Started  
**Created:** February 8, 2026  
**Prerequisite:** PostgreSQL 15+ installed and running  
**Working Directory:** `moments-backend/`  
**Risk Level:** Low (no existing functionality is modified)

---

## What Is This Phase About?

Right now, VideoMoments stores everything in **local files and Redis**:
- Moments are saved as `.json` files in `static/moments/`
- Transcripts are saved as `.json` files in `static/transcripts/`
- Video metadata is discovered by scanning the `static/videos/` folder at runtime
- Pipeline history lives in Redis (ephemeral -- lost on restart)
- URL-to-video mapping is a single `url_registry.json` file

This works for development, but it does not scale and is fragile. A proper database (PostgreSQL) gives us structured storage, relationships between data, efficient queries, and reliability.

**Phase 1 is purely infrastructure.** We are setting up the "plumbing" so that future phases can use the database. After this phase:
- PostgreSQL will be connected and ready
- All 8 database tables will exist (but they will be empty)
- The app will still use JSON files and Redis as before -- nothing changes for the user
- The `/health` endpoint will report both Redis and PostgreSQL status

**Think of it like building the foundation of a house before putting up walls.**

---

## What Does NOT Change

This is important to understand. Phase 1 does **not** touch:

- Any existing API endpoints (videos, moments, transcripts, clips, pipeline, etc.)
- Any existing services (audio, transcription, clipping, AI generation, etc.)
- How moments/transcripts are read/written (still JSON files)
- How the pipeline works (still Redis-based)
- How videos are found (still filesystem scan)
- The frontend -- it sees zero difference

The app will work exactly the same as before. We are just adding database connectivity alongside everything that already exists.

---

## Prerequisites (Before You Start)

### 1. PostgreSQL Must Be Running

You need PostgreSQL 15+ installed and running on your machine. Verify with:

```bash
# Check if PostgreSQL is running
pg_isready
# Expected output: /tmp:5432 - accepting connections
```

### 2. Create the Database

```bash
# Connect to PostgreSQL
psql -U postgres

# Create the database
CREATE DATABASE videomoments;

# Verify it exists
\l
# You should see "videomoments" in the list

# Exit
\q
```

The default connection details we will use:
- **Host:** localhost
- **Port:** 5432
- **User:** postgres
- **Password:** postgres
- **Database:** videomoments

If your PostgreSQL uses different credentials, you will override them via environment variables later.

### 3. Virtual Environment Is Active

Make sure you are working inside the project's virtual environment before installing new packages.

---

## Step-by-Step Breakdown

### Step 1: Install New Python Dependencies

**What:** Add 4 new packages to `requirements.txt`  
**Why:** We need libraries to talk to PostgreSQL and manage database schema changes  
**File Modified:** `moments-backend/requirements.txt`

| Package | What It Does |
|---------|-------------|
| `sqlalchemy[asyncio]` | ORM (Object-Relational Mapper) -- lets us define tables as Python classes and query them with Python code instead of raw SQL |
| `asyncpg` | The async PostgreSQL driver -- this is what actually sends queries to PostgreSQL. It works with Python's `async/await` (which FastAPI uses) |
| `alembic` | Database migration tool -- tracks and applies schema changes (like git for your database structure) |
| `psycopg2-binary` | A synchronous PostgreSQL driver -- needed only by Alembic because Alembic's migration runner does not support async drivers |

**Current `requirements.txt`:**

```
fastapi==0.104.1
uvicorn[standard]==0.24.0
pydantic-settings==2.1.0
aiofiles==23.2.1
opencv-python==4.8.1.78
numpy<2.0
requests==2.31.0
httpx==0.27.0
psutil==5.9.6
python-json-logger==2.0.7
redis==5.0.1
filelock==3.13.1
google-cloud-storage==2.14.0
```

**After this step, we add at the bottom:**

```
# Database (PostgreSQL)
sqlalchemy[asyncio]
asyncpg
alembic
psycopg2-binary
```

Then run `pip install -r requirements.txt` to install them.

**How to verify:** Run `python -c "import sqlalchemy; import asyncpg; import alembic; print('All installed')"` -- should print "All installed" without errors.

---

### Step 2: Add Database Settings to Config

**What:** Add 6 new configuration fields to the `Settings` class  
**Why:** The app needs to know how to connect to PostgreSQL (URL, connection pool settings)  
**File Modified:** `moments-backend/app/core/config.py`

**New settings to add to the `Settings` class:**

| Setting | Default Value | What It Means |
|---------|--------------|---------------|
| `database_url` | `postgresql+asyncpg://postgres:postgres@localhost:5432/videomoments` | Connection string for the async driver. Format: `dialect+driver://user:password@host:port/database` |
| `database_sync_url` | `postgresql+psycopg2://postgres:postgres@localhost:5432/videomoments` | Same database, but using the sync driver. Only used by Alembic for running migrations |
| `database_pool_size` | `5` | How many persistent database connections to keep open. 5 means "at any time, up to 5 queries can run simultaneously without waiting" |
| `database_max_overflow` | `10` | When all 5 pool connections are busy, allow up to 10 temporary extra connections. These are closed when no longer needed |
| `database_pool_timeout` | `30` | If all connections (5 + 10 = 15) are busy, wait up to 30 seconds for one to free up before raising an error |
| `database_echo` | `False` | When True, SQLAlchemy prints every SQL query to the console. Useful for debugging, but very noisy in production |

**Why two URLs (async vs sync)?**

FastAPI runs async code using `asyncpg`. But Alembic (the migration tool) runs sync code and needs `psycopg2`. Both connect to the **same database** -- they just use different Python drivers. You will never interact with the sync URL in application code; it is only for Alembic.

**All settings can be overridden with environment variables.** For example:
```bash
export DATABASE_URL="postgresql+asyncpg://myuser:mypass@db.example.com:5432/videomoments"
```

---

### Step 3: Create the Database Module

**What:** Create a new `app/database/` package with 4 files  
**Why:** This is the "plumbing layer" between FastAPI and PostgreSQL  
**Files Created:** 4 new files

#### File Structure After This Step

```
moments-backend/app/database/
├── __init__.py          # Makes this a Python package, exports key objects
├── base.py              # Defines the "Base" class all models inherit from
├── session.py           # Creates and manages the database connection
└── dependencies.py      # FastAPI dependency for injecting DB sessions into endpoints
```

#### 3a. `base.py` -- The Foundation for All Models

This file defines a single class called `Base` using SQLAlchemy's `DeclarativeBase`. Every database model (Video, Transcript, Moment, etc.) will inherit from this class. It is the common ancestor.

**What `DeclarativeBase` does:**
- It tells SQLAlchemy "any class that inherits from me represents a database table"
- It collects metadata about all tables (column names, types, constraints) so Alembic can auto-generate migrations
- It provides the `Base.metadata` object that Alembic reads to know what the database should look like

**In simple terms:** `Base` is the blueprint registry. Every model registers itself here, and the migration tool reads this registry to create/modify tables.

#### 3b. `session.py` -- The Database Connection Manager

This is the most important file. It creates and manages the connection to PostgreSQL.

**Key components:**

| Component | What It Does |
|-----------|-------------|
| `engine` | A connection pool to PostgreSQL. Created once at startup, reused for all queries. Like a highway with multiple lanes (pool_size) to the database |
| `async_sessionmaker` | A factory that creates database sessions. A "session" is like a shopping cart -- you add queries to it, and when you're done, you either commit (save all changes) or rollback (discard all changes) |
| `init_db()` | Called once at app startup. Creates the engine. Does NOT create tables (that is Alembic's job) |
| `close_db()` | Called once at app shutdown. Closes all database connections gracefully |
| `get_async_session()` | An async generator that creates a session, gives it to you, and ensures it is properly closed when you are done -- even if an error occurs |

**How a session works (simplified flow):**

```
1. FastAPI receives a request
2. get_db() dependency creates a new session
3. Your endpoint code uses the session to read/write data
4. If everything succeeds → session.commit() saves changes
5. If an error occurs → session.rollback() discards changes
6. Session is always closed at the end
```

**Connection pooling explained:**

Without a pool, every request would open a new connection to PostgreSQL (slow -- takes ~50ms) and close it when done. With a pool, we keep 5 connections permanently open. When a request needs the database, it "borrows" a connection from the pool, uses it, and returns it. This is much faster (~0.1ms to borrow vs ~50ms to create).

```
Without pool:                    With pool (pool_size=5):
                                 ┌─ Connection 1 ─┐
Request → Open → Query → Close   │  Connection 2   │
Request → Open → Query → Close   │  Connection 3   │ ← Borrow & Return
Request → Open → Query → Close   │  Connection 4   │
                                 │  Connection 5   │
                                 └─────────────────┘
```

#### 3c. `dependencies.py` -- FastAPI Integration

This file contains a single function: `get_db()`. It is a FastAPI "dependency" -- a pattern FastAPI uses to inject shared resources into your endpoint functions.

**How it will be used in future phases:**

```python
# In an endpoint (future -- NOT Phase 1):
@router.get("/api/videos")
async def list_videos(db: AsyncSession = Depends(get_db)):
    # 'db' is a ready-to-use database session
    # It was created automatically and will be cleaned up automatically
    result = await db.execute(select(Video))
    return result.scalars().all()
```

In Phase 1 we create this dependency but we do **not** use it in any endpoints yet. That happens in Phase 2+.

#### 3d. `__init__.py` -- Package Exports

This file imports and re-exports the key objects so other parts of the app can do:

```python
from app.database import Base, get_db, init_db, close_db
```

Instead of:

```python
from app.database.base import Base
from app.database.session import init_db, close_db
from app.database.dependencies import get_db
```

---

### Step 4: Create All 8 ORM Models

**What:** Create one Python file per database table, inside `app/database/models/`  
**Why:** These Python classes define what each table looks like -- columns, types, constraints, indexes, and relationships  
**Files Created:** 9 new files (8 models + 1 `__init__.py`)

#### What Is an ORM Model?

An ORM (Object-Relational Mapper) model is a Python class that mirrors a database table. Instead of writing raw SQL like:

```sql
CREATE TABLE videos (
    id SERIAL PRIMARY KEY,
    identifier VARCHAR(255) UNIQUE NOT NULL,
    ...
);
```

You write a Python class:

```python
class Video(Base):
    __tablename__ = "videos"
    id = Column(Integer, primary_key=True)
    identifier = Column(String(255), unique=True, nullable=False)
    ...
```

SQLAlchemy + Alembic read these classes and generate the SQL for you. This keeps the schema definition in Python code (version-controlled, type-checked) instead of separate SQL files.

#### File Structure

```
moments-backend/app/database/models/
├── __init__.py              # Imports all models (so Alembic discovers them)
├── video.py                 # Table 1: Videos
├── transcript.py            # Table 2: Transcripts
├── moment.py                # Table 3: Moments
├── prompt.py                # Table 4: Prompts
├── generation_config.py     # Table 5: Generation Configs
├── clip.py                  # Table 6: Clips
├── thumbnail.py             # Table 7: Thumbnails
└── pipeline_history.py      # Table 8: Pipeline History
```

#### Model-by-Model Breakdown

##### Model 1: `video.py` -- Videos Table

The central entity. Every other table connects back to a video.

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Auto-incrementing primary key (database manages this) |
| `identifier` | `String(255)` | `VARCHAR(255)` | Business ID like "motivation", "jspz-aaa". Used in URLs and APIs. Must be unique |
| `source_url` | `Text` | `TEXT` | Where the video was originally downloaded from (YouTube URL, etc.). Can be null for manually added videos |
| `cloud_url` | `Text` | `TEXT` | GCS path like `gs://bucket/videos/motivation.mp4`. Required (NOT NULL) |
| `title` | `String(500)` | `VARCHAR(500)` | Human-readable title. Optional |
| `duration_seconds` | `Float` | `FLOAT` | Video length from ffprobe. Optional |
| `file_size_kb` | `BigInteger` | `BIGINT` | File size in KB. Optional |
| `video_codec` | `String(50)` | `VARCHAR(50)` | "h264", "vp9", etc. Optional |
| `audio_codec` | `String(50)` | `VARCHAR(50)` | "aac", "opus", etc. Optional |
| `resolution` | `String(20)` | `VARCHAR(20)` | "1920x1080". Optional |
| `frame_rate` | `Float` | `FLOAT` | 30.0, 60.0. Optional |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set to current time when row is inserted |

**Indexes:**
- `UNIQUE` on `identifier` -- no two videos can have the same identifier
- `INDEX` on `source_url` -- fast duplicate URL detection
- `INDEX` on `created_at` -- fast sorting by date

**Relationships (defined in Python, not as DB columns):**
- `transcripts` -- list of related Transcript objects (in practice, always 0 or 1)
- `moments` -- list of related Moment objects
- `clips` -- list of related Clip objects
- `thumbnails` -- list of related Thumbnail objects
- `pipeline_runs` -- list of related PipelineHistory objects

These relationships let you do things like `video.moments` to get all moments for a video without writing a JOIN query.

##### Model 2: `transcript.py` -- Transcripts Table

One-to-one with Videos. Each video has exactly one transcript (or none if not yet transcribed).

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Primary key |
| `video_id` | `Integer` | `INTEGER` | FK to `videos.id`. **UNIQUE** (enforces 1:1). **ON DELETE CASCADE** (delete video → delete transcript) |
| `full_text` | `Text` | `TEXT` | Complete transcript text. Required |
| `word_timestamps` | `JSONB` | `JSONB` | Array of `{word, start, end}` objects. Required |
| `segment_timestamps` | `JSONB` | `JSONB` | Array of `{text, start, end}` objects. Required |
| `language` | `String(10)` | `VARCHAR(10)` | Default "en" |
| `number_of_words` | `Integer` | `INTEGER` | Total word count |
| `number_of_segments` | `Integer` | `INTEGER` | Total segment count |
| `transcription_service` | `String(50)` | `VARCHAR(50)` | "whisper", "parakeet", etc. |
| `processing_time_seconds` | `Float` | `FLOAT` | How long transcription took |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set |

**Special: JSONB columns.** PostgreSQL's JSONB type stores JSON data in a binary format that is fast to query. We use it for timestamps because each video has a different number of words/segments, so a fixed-column approach would not work.

**Special: GIN index on `full_text`.** This creates a full-text search index so you can search across all transcripts efficiently (e.g., "find all videos that mention 'machine learning'").

**No `updated_at` column.** Transcripts are immutable -- once created, they are never modified. If re-transcription is needed, the old transcript is deleted and a new one is created.

##### Model 3: `moment.py` -- Moments Table

AI-identified segments within a video. A video can have many moments.

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Primary key |
| `identifier` | `String(20)` | `VARCHAR(20)` | Business ID like "0658152d253996fe". Unique |
| `video_id` | `Integer` | `INTEGER` | FK to `videos.id`. **ON DELETE CASCADE** |
| `start_time` | `Float` | `FLOAT` | Start timestamp in seconds. Required |
| `end_time` | `Float` | `FLOAT` | End timestamp in seconds. Required. Must be > start_time (CHECK constraint) |
| `title` | `String(500)` | `VARCHAR(500)` | AI-generated title. Required |
| `is_refined` | `Boolean` | `BOOLEAN` | Default False. True if this is a refined version |
| `parent_id` | `Integer` | `INTEGER` | FK to `moments.id` (self-reference!). **ON DELETE SET NULL**. Only set for refined moments |
| `generation_model` | `String(100)` | `VARCHAR(100)` | Which AI model generated this |
| `generation_config_id` | `Integer` | `INTEGER` | FK to `generation_configs.id`. **ON DELETE CASCADE** |
| `score` | `Integer` | `INTEGER` | 0-10 importance score. Optional (CHECK constraint: 0-10) |
| `scoring_model` | `String(100)` | `VARCHAR(100)` | Which AI model scored this |
| `scored_at` | `DateTime` | `TIMESTAMP` | When scoring happened |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set |
| `updated_at` | `DateTime` | `TIMESTAMP` | Auto-updated on modification |

**Special: Self-referencing foreign key (`parent_id`).** When a moment is "refined" (re-analyzed by AI for better timestamps), a new moment row is created with `is_refined=True` and `parent_id` pointing to the original moment. This creates a parent-child chain. `ON DELETE SET NULL` means if the parent is deleted, the child keeps existing but loses the reference.

**CHECK constraints:**
- `end_time > start_time` -- a moment cannot end before it starts
- `score >= 0 AND score <= 10` -- score must be in 0-10 range (or null)

##### Model 4: `prompt.py` -- Prompts Table

Reusable prompt templates used for AI generation.

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Primary key |
| `user_prompt` | `Text` | `TEXT` | The user's instruction to the AI. Required |
| `system_prompt` | `Text` | `TEXT` | The system instructions for the AI. Required |
| `prompt_hash` | `String(64)` | `VARCHAR(64)` | SHA-256 hash of user_prompt + system_prompt. **UNIQUE**. Used for deduplication |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set |

**Why the hash?** Instead of comparing two potentially huge text strings to check if a prompt already exists, we compute a 64-character hash and compare that. It is much faster and uses a fixed-size index.

##### Model 5: `generation_config.py` -- Generation Configs Table

Stores the full set of AI generation parameters used for a pipeline run.

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Primary key |
| `prompt_id` | `Integer` | `INTEGER` | FK to `prompts.id`. **ON DELETE CASCADE** |
| `transcript_id` | `Integer` | `INTEGER` | FK to `transcripts.id`. **ON DELETE CASCADE**. Nullable |
| `model` | `String(100)` | `VARCHAR(100)` | AI model name. Required |
| `operation_type` | `String(50)` | `VARCHAR(50)` | "generation" or "refinement". Required |
| `temperature` | `Float` | `FLOAT` | Model temperature (0.0-2.0) |
| `top_p` | `Float` | `FLOAT` | Top-p sampling |
| `top_k` | `Integer` | `INTEGER` | Top-k sampling |
| `min_moment_length` | `Float` | `FLOAT` | Minimum moment duration in seconds |
| `max_moment_length` | `Float` | `FLOAT` | Maximum moment duration in seconds |
| `min_moments` | `Integer` | `INTEGER` | Minimum number of moments to generate |
| `max_moments` | `Integer` | `INTEGER` | Maximum number of moments to generate |
| `config_hash` | `String(64)` | `VARCHAR(64)` | SHA-256 hash for deduplication. **UNIQUE**. Hash excludes `transcript_id` |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set |

**Important: `config_hash` excludes `transcript_id`.** This means the same config (same prompt + same model settings) can be reused across different videos/transcripts without creating duplicate rows.

##### Model 6: `clip.py` -- Clips Table

Video clips extracted from moments with padding.

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Primary key |
| `moment_id` | `Integer` | `INTEGER` | FK to `moments.id`. **UNIQUE** (1:1 relationship). **ON DELETE CASCADE** |
| `video_id` | `Integer` | `INTEGER` | FK to `videos.id`. **ON DELETE CASCADE** |
| `cloud_url` | `Text` | `TEXT` | GCS path. Required |
| `start_time` | `Float` | `FLOAT` | Clip start time (with padding). Required |
| `end_time` | `Float` | `FLOAT` | Clip end time (with padding). Required. CHECK: end > start |
| `padding_left` | `Float` | `FLOAT` | Actual left padding used (after boundary adjustment) |
| `padding_right` | `Float` | `FLOAT` | Actual right padding used (after boundary adjustment) |
| `file_size_kb` | `BigInteger` | `BIGINT` | File size |
| `format` | `String(20)` | `VARCHAR(20)` | "mp4", "webm" |
| `video_codec` | `String(50)` | `VARCHAR(50)` | Codec used |
| `audio_codec` | `String(50)` | `VARCHAR(50)` | Audio codec used |
| `resolution` | `String(20)` | `VARCHAR(20)` | Resolution |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set |

**1:1 with Moments:** The `UNIQUE` constraint on `moment_id` ensures each moment gets at most one clip.

##### Model 7: `thumbnail.py` -- Thumbnails Table

Thumbnail images for videos or clips (but never both at once).

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Primary key |
| `video_id` | `Integer` | `INTEGER` | FK to `videos.id`. Nullable. **ON DELETE CASCADE** |
| `clip_id` | `Integer` | `INTEGER` | FK to `clips.id`. Nullable. **ON DELETE CASCADE** |
| `cloud_url` | `Text` | `TEXT` | GCS path. Required |
| `file_size_kb` | `BigInteger` | `BIGINT` | File size |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set |

**Special: CHECK constraint (XOR).** Exactly ONE of `video_id` or `clip_id` must be set:

```sql
CHECK (
    (video_id IS NOT NULL AND clip_id IS NULL) OR
    (video_id IS NULL AND clip_id IS NOT NULL)
)
```

This means a thumbnail belongs to either a video or a clip, but never both and never neither.

**Special: Partial unique indexes.** Two unique indexes ensure 1:1:
- `UNIQUE WHERE video_id IS NOT NULL` -- one thumbnail per video
- `UNIQUE WHERE clip_id IS NOT NULL` -- one thumbnail per clip

##### Model 8: `pipeline_history.py` -- Pipeline History Table

Tracks complete pipeline executions.

| Column | Python Type | DB Type | Notes |
|--------|------------|---------|-------|
| `id` | `Integer` | `SERIAL` | Primary key |
| `identifier` | `String(100)` | `VARCHAR(100)` | Business ID like "pipeline:motivation:1767610697193". Unique |
| `video_id` | `Integer` | `INTEGER` | FK to `videos.id`. **ON DELETE CASCADE** |
| `generation_config_id` | `Integer` | `INTEGER` | FK to `generation_configs.id`. **ON DELETE CASCADE** |
| `pipeline_type` | `String(50)` | `VARCHAR(50)` | "full", "moments_only", "clips_only". Required |
| `status` | `String(20)` | `VARCHAR(20)` | "running", "completed", "failed", "partial". Required |
| `started_at` | `DateTime` | `TIMESTAMP` | When pipeline started. Required |
| `completed_at` | `DateTime` | `TIMESTAMP` | When finished. Null if still running |
| `duration_seconds` | `Float` | `FLOAT` | Total execution time |
| `total_moments_generated` | `Integer` | `INTEGER` | Count of moments created |
| `total_clips_created` | `Integer` | `INTEGER` | Count of clips created |
| `error_stage` | `String(50)` | `VARCHAR(50)` | Which stage failed (if any) |
| `error_message` | `Text` | `TEXT` | Error details (if any) |
| `created_at` | `DateTime` | `TIMESTAMP` | Auto-set |

#### The `__init__.py` File (Critical)

The `app/database/models/__init__.py` file must **import every model class**. This is not optional. Alembic discovers tables by looking at what classes inherit from `Base`. If a model is not imported, Alembic will not see it and will not create the table.

```python
from app.database.models.video import Video
from app.database.models.transcript import Transcript
from app.database.models.moment import Moment
from app.database.models.prompt import Prompt
from app.database.models.generation_config import GenerationConfig
from app.database.models.clip import Clip
from app.database.models.thumbnail import Thumbnail
from app.database.models.pipeline_history import PipelineHistory
```

---

### Step 5: Initialize Alembic (Migration Tool)

**What:** Set up Alembic in the `moments-backend/` directory and generate the first migration  
**Why:** Alembic manages database schema changes over time. It creates SQL migration scripts from your model definitions  
**Files Created:** `alembic.ini` + `alembic/` directory

#### What Is Alembic and Why Do We Need It?

You could create tables by running raw SQL, but then:
- How do you track which changes have been applied?
- How do you apply the same changes to another developer's database?
- How do you add a column later without losing data?

Alembic solves this. It:
1. Reads your SQLAlchemy model classes
2. Compares them to the current database state
3. Generates a Python migration script with the exact SQL needed
4. Applies that migration
5. Records that the migration has been applied

**Think of it like git for your database schema.**

#### Sub-steps

##### 5a. Initialize Alembic

Run this command from `moments-backend/`:

```bash
cd moments-backend
alembic init alembic
```

This creates:

```
moments-backend/
├── alembic.ini               # Alembic configuration file
└── alembic/
    ├── env.py                # How Alembic connects to the database
    ├── script.py.mako        # Template for migration files
    ├── README                # Alembic readme
    └── versions/             # Where migration files are stored
```

##### 5b. Configure `alembic.ini`

The main thing to configure is the database URL. But instead of hardcoding it, we will read it from our app settings in `env.py`. So in `alembic.ini`, we set the URL to a placeholder:

```ini
sqlalchemy.url = postgresql+psycopg2://postgres:postgres@localhost:5432/videomoments
```

Note: This uses `psycopg2` (sync driver), NOT `asyncpg`, because Alembic runs synchronously.

##### 5c. Configure `alembic/env.py`

This is the most important Alembic file. We need to customize it to:

1. **Import our models** so Alembic knows about all 8 tables
2. **Use our app settings** to get the database URL (instead of hardcoding)
3. **Point to `Base.metadata`** so Alembic can compare models vs database

Key changes to make in `env.py`:

```python
# Import all models so they register with Base.metadata
from app.database.models import (
    Video, Transcript, Moment, Prompt,
    GenerationConfig, Clip, Thumbnail, PipelineHistory
)
from app.database.base import Base

# Tell Alembic to use our Base's metadata
target_metadata = Base.metadata
```

```python
# Optionally read the URL from our app settings instead of alembic.ini
from app.core.config import get_settings
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_sync_url)
```

##### 5d. Generate the Initial Migration

```bash
alembic revision --autogenerate -m "create_all_tables"
```

This command:
1. Connects to the database (which currently has no tables)
2. Reads `Base.metadata` (which knows about all 8 tables from our model imports)
3. Compares: database has 0 tables, models define 8 tables
4. Generates a migration script in `alembic/versions/` that creates all 8 tables

**You should inspect the generated migration file** to verify it contains:
- 8 `op.create_table()` calls (one per table)
- All columns with correct types
- All foreign keys with correct `ON DELETE` behavior
- All indexes
- All CHECK constraints

##### 5e. Run the Migration

```bash
alembic upgrade head
```

This actually executes the migration -- creates all 8 tables in PostgreSQL.

**How to verify tables were created:**

```bash
psql -U postgres -d videomoments -c "\dt"
```

Expected output:

```
              List of relations
 Schema |        Name         | Type  |  Owner
--------+---------------------+-------+----------
 public | alembic_version     | table | postgres
 public | clips               | table | postgres
 public | generation_configs  | table | postgres
 public | moments             | table | postgres
 public | pipeline_history    | table | postgres
 public | prompts             | table | postgres
 public | thumbnails          | table | postgres
 public | transcripts         | table | postgres
 public | videos              | table | postgres
(9 rows)
```

Note: 9 tables = 8 our tables + 1 `alembic_version` (Alembic's internal tracking table).

---

### Step 6: Wire Database Into the FastAPI Application

**What:** Connect the database engine on startup and disconnect on shutdown  
**Why:** The app needs to establish a connection pool when it starts and clean it up when it stops  
**File Modified:** `moments-backend/app/main.py`

#### Current Startup/Shutdown Flow

```
STARTUP:
  1. Initialize Redis client
  2. Seed model configs (if Redis empty)
  3. Initialize pipeline consumer group
  4. Start pipeline worker (if worker mode)

SHUTDOWN:
  1. Cleanup resources
  2. Close Redis client
```

#### After Phase 1

```
STARTUP:
  1. Initialize Redis client
  2. Initialize Database connection pool     ← NEW
  3. Seed model configs (if Redis empty)
  4. Initialize pipeline consumer group
  5. Start pipeline worker (if worker mode)

SHUTDOWN:
  1. Cleanup resources
  2. Close Database connection pool           ← NEW
  3. Close Redis client
```

#### Changes to `startup_event()`

Add after Redis initialization:

```python
# Initialize database
from app.database.session import init_db
try:
    await init_db()
    logger.info("Database connection pool initialized")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")
```

#### Changes to `shutdown_event()`

Add before Redis close:

```python
# Close database
from app.database.session import close_db
await close_db()
```

#### Changes to `/health` Endpoint

Currently returns:

```json
{"status": "healthy", "redis": "connected"}
```

After Phase 1, it should return:

```json
{"status": "healthy", "redis": "connected", "database": "connected"}
```

The database health check runs a simple `SELECT 1` query. If it succeeds, the database is connected. If it fails, report "disconnected".

```python
@app.get("/health")
async def health():
    redis_status = "connected" if await async_health_check() else "disconnected"
    
    # Database health check
    db_status = "disconnected"
    try:
        from app.database.session import get_async_session
        async for session in get_async_session():
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
            db_status = "connected"
            break
    except Exception:
        db_status = "disconnected"
    
    return {"status": "healthy", "redis": redis_status, "database": db_status}
```

---

### Step 7: Verify Everything Works

**What:** Run the application and check that everything is working  
**Why:** Confirm nothing is broken and the new infrastructure is operational

#### Verification Checklist

| # | Check | How to Verify | Expected Result |
|---|-------|--------------|-----------------|
| 1 | PostgreSQL is running | `pg_isready` | "accepting connections" |
| 2 | Database exists | `psql -U postgres -c "\l"` | "videomoments" in list |
| 3 | All 8 tables exist | `psql -U postgres -d videomoments -c "\dt"` | 9 tables (8 + alembic_version) |
| 4 | App starts without errors | `cd moments-backend && uvicorn app.main:app --port 7005` | No startup errors |
| 5 | Health check passes | `curl http://localhost:7005/health` | `{"status":"healthy","redis":"connected","database":"connected"}` |
| 6 | Existing features work | Open frontend, browse videos, view moments | Everything works as before |
| 7 | Tables have correct columns | `psql -U postgres -d videomoments -c "\d videos"` | All columns from schema |
| 8 | Foreign keys are correct | Check each table's constraints | All FKs present with correct ON DELETE |
| 9 | Indexes are created | `psql -U postgres -d videomoments -c "\di"` | All indexes from schema |
| 10 | Tables are empty | `SELECT count(*) FROM videos;` | 0 (no data migration in Phase 1) |

---

## Complete File Map

### New Files Created (17 files)

```
moments-backend/
├── alembic.ini                                    # Alembic configuration
├── alembic/
│   ├── env.py                                     # Alembic environment (customized)
│   ├── script.py.mako                             # Migration template (auto-generated)
│   ├── README                                     # Alembic readme (auto-generated)
│   └── versions/
│       └── xxxx_create_all_tables.py              # Auto-generated migration
├── app/
│   └── database/
│       ├── __init__.py                            # Package exports
│       ├── base.py                                # DeclarativeBase
│       ├── session.py                             # Engine + session factory
│       ├── dependencies.py                        # FastAPI get_db() dependency
│       └── models/
│           ├── __init__.py                        # Import all models
│           ├── video.py                           # Video model
│           ├── transcript.py                      # Transcript model
│           ├── moment.py                          # Moment model
│           ├── prompt.py                          # Prompt model
│           ├── generation_config.py               # GenerationConfig model
│           ├── clip.py                            # Clip model
│           ├── thumbnail.py                       # Thumbnail model
│           └── pipeline_history.py                # PipelineHistory model
```

### Existing Files Modified (3 files)

```
moments-backend/
├── requirements.txt                               # + 4 new dependencies
├── app/
│   └── core/
│       └── config.py                              # + 6 database settings
│   └── main.py                                    # + DB init/close + health check
```

---

## Relationship Diagram (How Tables Connect)

```
                    ┌──────────┐
                    │  Videos  │
                    │  (id)    │
                    └────┬─────┘
                         │
          ┌──────────────┼──────────────┬────────────────┐
          │              │              │                │
          ▼              ▼              ▼                ▼
   ┌─────────────┐ ┌──────────┐ ┌────────────┐  ┌──────────────────┐
   │ Transcripts │ │ Moments  │ │   Clips    │  │ Pipeline History │
   │ (video_id)  │ │(video_id)│ │ (video_id) │  │   (video_id)     │
   │   1:1       │ │   N:1    │ │    N:1     │  │     N:1          │
   └──────┬──────┘ └────┬─────┘ └─────┬──────┘  └──────┬───────────┘
          │              │             │                │
          │              │ parent_id   │                │
          │              │ (self-ref)  │                │
          │              │             │                │
          ▼              ▼             ▼                │
   ┌─────────────┐ ┌──────────┐ ┌────────────┐        │
   │  Gen Configs│ │ Moments  │ │ Thumbnails │        │
   │(transcript_ │ │(gen_     │ │ (clip_id)  │        │
   │  id)        │ │ config_  │ │  OR        │        │
   │             │ │ id)      │ │ (video_id) │        │
   └──────┬──────┘ └──────────┘ └────────────┘        │
          │                                            │
          ▼                                            │
   ┌─────────────┐                                     │
   │  Prompts    │◄────────────────────────────────────┘
   │(prompt_id)  │    (gen_config_id)
   └─────────────┘
```

**Delete cascades flow top-down:** Deleting a video automatically deletes its transcripts, moments, clips, pipeline history, and related generation configs. This ensures no orphaned data.

---

## Foreign Key Delete Behaviors (Quick Reference)

| When You Delete... | What Gets Automatically Deleted |
|--------------------|--------------------------------|
| A **Video** | Its transcript, all its moments, all its clips, all its thumbnails, all pipeline history |
| A **Moment** | Its clip (CASCADE). Its children's `parent_id` is set to NULL (SET NULL) |
| A **Clip** | Its thumbnail (CASCADE) |
| A **Prompt** | All generation configs using it (CASCADE) → which cascades to moments and pipeline history |
| A **Transcript** | All generation configs referencing it (CASCADE) |
| A **Generation Config** | All moments using it (CASCADE), all pipeline history using it (CASCADE) |

---

## Potential Issues and Troubleshooting

| Issue | Cause | Solution |
|-------|-------|---------|
| `psycopg2` install fails | Missing PostgreSQL headers | macOS: `brew install postgresql`. Linux: `apt-get install libpq-dev` |
| `alembic upgrade head` fails with "database does not exist" | Database not created | Run `createdb videomoments` or create via `psql` |
| `alembic upgrade head` fails with "connection refused" | PostgreSQL not running | Start PostgreSQL: `brew services start postgresql` (macOS) |
| App starts but `/health` shows database "disconnected" | Wrong connection URL | Check `DATABASE_URL` env var or defaults in config.py |
| `alembic revision --autogenerate` generates empty migration | Models not imported in `env.py` | Ensure `env.py` imports all models from `app.database.models` |
| Import errors when starting app | Dependencies not installed | Run `pip install -r requirements.txt` |
| Permission denied on PostgreSQL | Wrong user/password | Check PostgreSQL auth: `pg_hba.conf`. Use correct credentials |

---

## Glossary

| Term | Simple Explanation |
|------|-------------------|
| **ORM** | Object-Relational Mapper. Lets you use Python classes to represent database tables and Python code to query them (instead of writing raw SQL) |
| **SQLAlchemy** | The most popular Python ORM library. We use it to define models and query the database |
| **Alembic** | A migration tool built for SQLAlchemy. It generates SQL scripts to create/modify tables based on your model changes |
| **Migration** | A script that changes the database schema (creates tables, adds columns, etc.). Migrations are versioned and applied in order |
| **Engine** | SQLAlchemy's connection pool manager. It handles opening/closing connections to PostgreSQL efficiently |
| **Session** | A "conversation" with the database. You use a session to run queries, and then commit (save) or rollback (discard) changes |
| **AsyncSession** | An async version of Session that works with Python's `async/await`. FastAPI requires this |
| **DeclarativeBase** | The parent class all models inherit from. It tells SQLAlchemy "treat my subclasses as database tables" |
| **Foreign Key (FK)** | A column that references another table's primary key. Creates a relationship between tables |
| **CASCADE** | When a parent row is deleted, automatically delete all child rows that reference it |
| **SET NULL** | When a parent row is deleted, set the referencing column to NULL instead of deleting the child |
| **JSONB** | PostgreSQL's binary JSON type. Stores JSON data efficiently with support for indexing and querying |
| **GIN Index** | Generalized Inverted Index. A special index type for full-text search and JSONB data. Much faster than scanning every row |
| **Connection Pool** | A set of pre-opened database connections that are reused across requests (instead of opening/closing a new one each time) |
| **CHECK Constraint** | A rule that validates data before it is inserted. Example: `score >= 0 AND score <= 10` |
| **Partial Unique Index** | A unique constraint that only applies to rows matching a condition. Example: unique `video_id` only WHERE `video_id IS NOT NULL` |

---

## What Comes Next (Phase 2)

After Phase 1 is complete, Phase 2 will:
- Upload existing local videos to Google Cloud Storage
- Populate the (currently empty) `videos` table with metadata
- Modify the video download pipeline to write to both GCS and the database
- Change `GET /api/videos` to query the database instead of scanning the filesystem

Phase 1 gives us the foundation. Phase 2 starts using it.

---

**Document Status:** Ready for review  
**Schema Reference:** `database/SCHEMA.md`  
**Migration Plan Reference:** `CLOUD_DATABASE_MIGRATION_PLAN.md` (Phase 1, lines 211-358)
