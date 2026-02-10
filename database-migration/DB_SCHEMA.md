# VideoMoments Database Schema

**Document Status:** In Progress  
**Last Updated:** February 6, 2026  
**Database:** PostgreSQL 15+

---

## Overview

This document defines the database schema for VideoMoments application using a **cloud-first architecture**. All video files and static assets are stored in cloud storage (GCS/S3), with only metadata and references stored in the database.

---

## Table Definitions

### Table 1: Videos
**Purpose:** Central entity for all video content

**Relationship:** 1:1 with Transcripts, 1:N with Moments

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing database ID | 1, 2, 3 |
| `identifier` | VARCHAR(255) | UNIQUE, NOT NULL | Business identifier (developer-defined) | "motivation", "jspz-aaa" |
| `source_url` | TEXT | NULL | Original download URL | "https://youtube.com/watch?v=abc123" |
| `cloud_url` | TEXT | NOT NULL | Cloud storage URL (GCS/S3) | "gs://bucket/videos/motivation.mp4" |
| `title` | VARCHAR(500) | NULL | Human-readable title | "Why 24 Hours Define Success" |
| `duration_seconds` | FLOAT | NULL | Video duration (from ffprobe) | 120.5 |
| `file_size_kb` | BIGINT | NULL | File size in kilobytes | 50000 |
| `video_codec` | VARCHAR(50) | NULL | Video codec (from ffprobe) | "h264", "vp9" |
| `audio_codec` | VARCHAR(50) | NULL | Audio codec in video file | "aac", "opus" |
| `resolution` | VARCHAR(20) | NULL | Video resolution | "1920x1080", "1280x720" |
| `frame_rate` | FLOAT | NULL | Frames per second | 30.0, 60.0 |
| `created_at` | TIMESTAMP | DEFAULT NOW() | Record creation timestamp | "2026-02-06 10:30:00" |

**Indexes:**
- `idx_videos_identifier` UNIQUE on `identifier`
- `idx_videos_source_url` on `source_url`
- `idx_videos_created_at` on `created_at`

**Design Notes:**
- `id` is numeric primary key for database relationships (all FKs reference this)
- `identifier` is business identifier for APIs, URLs, and user-facing operations
- `source_url` indexed for fast duplicate download detection (replaces URL registry table)
- No local file path tracking (videos downloaded temporarily, processed, then uploaded to cloud)
- Video metadata (codec, resolution, fps) extracted via ffprobe during processing
- Audio codec refers to audio stream within video file, not separate audio files
- No status flags (has_transcript, has_moments) - computed from related tables when needed

---

### Table 2: Transcripts
**Purpose:** One-to-one transcript storage for videos

**Relationship:** 1:1 with Videos (via video_id)

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing ID | 1, 2, 3 |
| `video_id` | INTEGER | UNIQUE, NOT NULL, FK → videos(id) ON DELETE CASCADE | Reference to video (1:1) | 5 |
| `full_text` | TEXT | NOT NULL | Complete transcript text | "Hello world. How are you today?" |
| `word_timestamps` | JSONB | NOT NULL | Array of word-level timestamps | `[{"word": "Hello", "start": 0.0, "end": 0.5}]` |
| `segment_timestamps` | JSONB | NOT NULL | Array of segment timestamps | `[{"text": "Hello world.", "start": 0.0, "end": 2.0}]` |
| `language` | VARCHAR(10) | DEFAULT 'en' | ISO language code | "en", "es", "fr" |
| `number_of_words` | INTEGER | NULL | Total word count | 250 |
| `number_of_segments` | INTEGER | NULL | Total segment count | 45 |
| `transcription_service` | VARCHAR(50) | NULL | Service/model used | "whisper", "assemblyai" |
| `processing_time_seconds` | FLOAT | NULL | Transcription duration | 15.5 |
| `created_at` | TIMESTAMP | DEFAULT NOW() | Record creation timestamp | "2026-02-06 10:31:00" |

**Indexes:**
- `idx_transcripts_video_id` on `video_id`
- `idx_transcripts_full_text` GIN index on `to_tsvector('english', full_text)` for full-text search

**Design Notes:**
- No `updated_at` column - transcripts are immutable once created (write-once)
- `video_id` references numeric `videos.id` (not `videos.identifier`)
- UNIQUE constraint on `video_id` ensures strict 1:1 relationship with videos
- ON DELETE CASCADE automatically removes transcript when parent video is deleted
- Both `word_timestamps` and `segment_timestamps` are required (NOT NULL) - all transcripts must have complete timestamp data
- Full-text search enabled via PostgreSQL GIN index for semantic search
- JSONB format provides flexible storage and efficient querying for timestamp arrays

---

### Table 3: Moments
**Purpose:** AI-identified video segments with timestamps and metadata

**Relationship:** N:1 with Videos (via video_id), Self-referencing for refinements

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing database ID | 1, 2, 3 |
| `identifier` | VARCHAR(20) | UNIQUE, NOT NULL | Business identifier (developer-defined) | "0658152d253996fe" |
| `video_id` | INTEGER | NOT NULL, FK → videos(id) ON DELETE CASCADE | Parent video reference | 5 |
| `start_time` | FLOAT | NOT NULL | Start timestamp in seconds | 10.5 |
| `end_time` | FLOAT | NOT NULL, CHECK (end_time > start_time) | End timestamp in seconds | 45.2 |
| `title` | VARCHAR(500) | NOT NULL | AI-generated moment title | "The Power of Daily Habits" |
| `is_refined` | BOOLEAN | DEFAULT FALSE | Is this a refined moment? | true, false |
| `parent_id` | INTEGER | NULL, FK → moments(id) ON DELETE SET NULL | Original moment if refined | 123 |
| `generation_model` | VARCHAR(100) | NULL | Model that generated moment | "qwen3_vl_fp8" |
| `generation_config_id` | INTEGER | NULL, FK → generation_configs(id) ON DELETE CASCADE | Reference to shared config | 5 |
| `score` | INTEGER | NULL, CHECK (score >= 0 AND score <= 10) | Importance score (0-10) | 7 |
| `scoring_model` | VARCHAR(100) | NULL | Model that scored moment | "qwen3_vl_fp8" |
| `scored_at` | TIMESTAMP | NULL | When scoring completed | "2026-02-06 11:00:00" |
| `created_at` | TIMESTAMP | DEFAULT NOW() | When moment was created | "2026-02-06 10:30:00" |
| `updated_at` | TIMESTAMP | DEFAULT NOW() | Last modification timestamp | "2026-02-06 11:00:00" |

**Indexes:**
- `idx_moments_identifier` UNIQUE on `identifier`
- `idx_moments_video_id` on `video_id`
- `idx_moments_is_refined` on `is_refined`
- `idx_moments_parent_id` on `parent_id`
- `idx_moments_score` on `score` WHERE `score IS NOT NULL`
- `idx_moments_timestamps` on `(start_time, end_time)`

**Design Notes:**
- `id` is numeric primary key for database relationships (all FKs reference this)
- `identifier` is business identifier for APIs, URLs, and user-facing operations
- `video_id` and `parent_id` reference numeric `id` columns (optimized integer FKs)
- Duration computed as `(end_time - start_time)` when needed, not stored
- Self-referencing via `parent_id` for refined moments (original → refined relationship)
- Clips reference the root/parent moment only, not refined versions
- Constraints ensure refined moments have parent and end_time > start_time
- Score is optional (NULL until scoring phase completes)
- Updated when scoring is added after initial moment creation
- ON DELETE CASCADE on generation_config_id - deleting config cascades to moments

---

### Table 4: Prompts
**Purpose:** Store reusable prompt templates (user instructions + system instructions)

**Relationship:** 1:N with Generation Configs

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing ID | 1, 2, 3 |
| `user_prompt` | TEXT | NOT NULL | User's custom instruction | "Find engaging moments about productivity" |
| `system_prompt` | TEXT | NOT NULL | System instructions/template | "You are an AI that identifies key moments..." |
| `prompt_hash` | VARCHAR(64) | UNIQUE, NOT NULL | SHA-256 hash for deduplication | "a1b2c3d4ef567890..." |
| `created_at` | TIMESTAMP | DEFAULT NOW() | When prompt was created | "2026-02-06 10:30:00" |

**Indexes:**
- `idx_prompts_hash` UNIQUE on `prompt_hash`

**Design Notes:**
- `prompt_hash` = SHA256(user_prompt + system_prompt) for deduplication
- Multiple configs can reference same prompt (many-to-one)
- Prompts are immutable once created (no updated_at)
- Hash enables fast duplicate detection without comparing full text
- Fixed-size hash (64 bytes) avoids PostgreSQL index size limits for large prompts

---

### Table 5: Generation Configs
**Purpose:** Store AI generation configuration parameters

**Relationship:** N:1 with Prompts, N:1 with Transcripts, 1:N with Moments

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing ID | 1, 2, 3 |
| `prompt_id` | INTEGER | NOT NULL, FK → prompts(id) ON DELETE CASCADE | Reference to prompt | 5 |
| `transcript_id` | INTEGER | NULL, FK → transcripts(id) ON DELETE CASCADE | Reference to transcript used | 10 |
| `model` | VARCHAR(100) | NOT NULL | AI model identifier | "qwen3_vl_fp8", "minimax" |
| `operation_type` | VARCHAR(50) | NOT NULL | Task type | "generation", "refinement" |
| `temperature` | FLOAT | NULL | Model temperature (0.0-2.0) | 0.7 |
| `top_p` | FLOAT | NULL | Top-p sampling parameter | 0.9 |
| `top_k` | INTEGER | NULL | Top-k sampling parameter | 50 |
| `min_moment_length` | FLOAT | NULL | Min moment duration (seconds) | 30.0 |
| `max_moment_length` | FLOAT | NULL | Max moment duration (seconds) | 90.0 |
| `min_moments` | INTEGER | NULL | Minimum number of moments | 3 |
| `max_moments` | INTEGER | NULL | Maximum number of moments | 10 |
| `config_hash` | VARCHAR(64) | UNIQUE, NOT NULL | SHA-256 hash for deduplication (excludes transcript_id) | "a1b2c3d4ef567890..." |
| `created_at` | TIMESTAMP | DEFAULT NOW() | When config was created | "2026-02-06 10:30:00" |

**Indexes:**
- `idx_generation_configs_hash` UNIQUE on `config_hash`
- `idx_generation_configs_prompt_id` on `prompt_id`
- `idx_generation_configs_transcript_id` on `transcript_id`
- `idx_generation_configs_model` on `model`
- `idx_generation_configs_operation` on `operation_type`

**Design Notes:**
- References prompts table for all prompt text (no duplication)
- References transcripts table for data source (no duplication)
- All model parameters as separate columns (type-safe, queryable)
- `transcript_id` can be NULL for configs that don't use transcripts
- **Config Hash Calculation (IMPORTANT):**
  - `config_hash` = SHA256(prompt_id + model + operation_type + temperature + top_p + top_k + min_moment_length + max_moment_length + min_moments + max_moments)
  - **Hash EXCLUDES `transcript_id`** - This is intentional and critical for config reusability
  - Example: Same prompt + same model params + different videos → Same hash → Reuse config
  - Config with hash "abc123" can be used with transcript_id=1, transcript_id=2, transcript_id=3, etc.
  - `transcript_id` FK is maintained separately for prompt reconstruction, but NOT part of uniqueness
- Before creating new config, check if config_hash exists to reuse existing config
- Complete prompt reconstructed at runtime from: prompt + transcript + config params
- Separate columns instead of JSONB for better type safety and query performance
- ON DELETE CASCADE on prompt_id and transcript_id - deleting prompt or transcript cascades to configs

---

### Table 6: Clips
**Purpose:** Video clips extracted from moments with padding

**Relationship:** 1:1 with Moments (via moment_id), N:1 with Videos

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing ID | 1, 2, 3 |
| `moment_id` | INTEGER | UNIQUE, NOT NULL, FK → moments(id) ON DELETE CASCADE | Reference to root moment (1:1) | 123 |
| `video_id` | INTEGER | NOT NULL, FK → videos(id) ON DELETE CASCADE | Reference to source video | 5 |
| `cloud_url` | TEXT | NOT NULL | Cloud storage URL | "gs://bucket/clips/clip_abc123.mp4" |
| `start_time` | FLOAT | NOT NULL | Actual clip start (with adjusted padding) | 0.0 |
| `end_time` | FLOAT | NOT NULL, CHECK (end_time > start_time) | Actual clip end (with adjusted padding) | 150.0 |
| `padding_left` | FLOAT | NOT NULL | Actual left padding used (adjusted) | 15.0 |
| `padding_right` | FLOAT | NOT NULL | Actual right padding used (adjusted) | 30.0 |
| `file_size_kb` | BIGINT | NULL | Clip file size in kilobytes | 25000 |
| `format` | VARCHAR(20) | NULL | Video container format | "mp4", "webm" |
| `video_codec` | VARCHAR(50) | NULL | Video codec used | "h264", "vp9" |
| `audio_codec` | VARCHAR(50) | NULL | Audio codec used | "aac", "opus" |
| `resolution` | VARCHAR(20) | NULL | Video resolution | "1920x1080" |
| `created_at` | TIMESTAMP | DEFAULT NOW() | When clip was created | "2026-02-06 11:00:00" |

**Indexes:**
- `idx_clips_moment_id` UNIQUE on `moment_id`
- `idx_clips_video_id` on `video_id`

**Design Notes:**
- Entry created ONLY after clip file successfully created and uploaded to cloud
- No status column needed (existence in table = clip exists)
- UNIQUE on `moment_id` ensures one clip per moment (1:1 relationship)
- ON DELETE CASCADE on both FKs - deleting moment or video also deletes clip
- Clips always reference root moment, never refined moments
- `moment_id` and `video_id` reference numeric `id` columns (optimized integer FKs)
- `padding_left` and `padding_right` store actual padding used after boundary adjustments
- Padding automatically adjusted to respect video boundaries (0 to video_duration)
- `start_time` = moment.start_time - padding_left
- `end_time` = moment.end_time + padding_right
- **Duration is NEVER stored** - always calculated in application code as `(end_time - start_time)`
- Duration calculation includes padding (represents actual clip duration)

---

### Table 7: Thumbnails
**Purpose:** Thumbnail images for videos and clips

**Relationship:** 1:1 with Videos OR 1:1 with Clips (mutually exclusive)

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing ID | 1, 2, 3 |
| `video_id` | INTEGER | NULL, FK → videos(id) ON DELETE CASCADE | Video this belongs to | 5 |
| `clip_id` | INTEGER | NULL, FK → clips(id) ON DELETE CASCADE | Clip this belongs to | 10 |
| `cloud_url` | TEXT | NOT NULL | Cloud storage URL | "gs://bucket/thumbnails/thumb_abc.jpg" |
| `file_size_kb` | BIGINT | NULL | File size in kilobytes | 45 |
| `created_at` | TIMESTAMP | DEFAULT NOW() | When created | "2026-02-06 11:00:00" |

**Constraints:**
```sql
CHECK (
    (video_id IS NOT NULL AND clip_id IS NULL) OR 
    (video_id IS NULL AND clip_id IS NOT NULL)
)
```

**Indexes:**
- `idx_thumbnails_video_id` on `video_id`
- `idx_thumbnails_clip_id` on `clip_id`
- `idx_thumbnails_video_id_unique` UNIQUE on `video_id` WHERE `video_id IS NOT NULL`
- `idx_thumbnails_clip_id_unique` UNIQUE on `clip_id` WHERE `clip_id IS NOT NULL`

**Design Notes:**
- Entry created ONLY after thumbnail file successfully created and uploaded to cloud
- Either `video_id` OR `clip_id` must be set, never both, never neither (enforced by CHECK constraint)
- UNIQUE partial indexes ensure 1:1 relationship (one thumbnail per video/clip)
- ON DELETE CASCADE on both FKs - deleting video/clip also deletes its thumbnail
- `video_id` and `clip_id` reference numeric `id` columns (optimized integer FKs)
- Thumbnail dimensions standardized (not stored in DB)
- Created asynchronously after clip creation
- Format can be derived from `cloud_url` filename extension
- `file_size_kb` useful for storage analytics and monitoring

---

### Table 8: Pipeline History
**Purpose:** Track complete pipeline executions and their outcomes

**Relationship:** N:1 with Videos, N:1 with Generation Configs

| Column Name | Data Type | Constraints | Description | Example |
|-------------|-----------|-------------|-------------|---------|
| `id` | SERIAL | PRIMARY KEY | Auto-incrementing database ID | 1, 2, 3 |
| `identifier` | VARCHAR(100) | UNIQUE, NOT NULL | Business identifier (developer-defined) | "pipeline:motivation:1767610697193" |
| `video_id` | INTEGER | NOT NULL, FK → videos(id) ON DELETE CASCADE | Video being processed | 5 |
| `generation_config_id` | INTEGER | NOT NULL, FK → generation_configs(id) ON DELETE CASCADE | Config for entire pipeline | 3 |
| `pipeline_type` | VARCHAR(50) | NOT NULL | Type of pipeline run | "full", "moments_only", "clips_only" |
| `status` | VARCHAR(20) | NOT NULL | Overall pipeline status | "running", "completed", "failed", "partial" |
| `started_at` | TIMESTAMP | NOT NULL | When pipeline started | "2026-02-06 10:00:00" |
| `completed_at` | TIMESTAMP | NULL | When finished | "2026-02-06 10:15:00" |
| `duration_seconds` | FLOAT | NULL | Total execution duration | 900.0 |
| `total_moments_generated` | INTEGER | NULL | Total moments created | 5 |
| `total_clips_created` | INTEGER | NULL | Total clips created | 5 |
| `error_stage` | VARCHAR(50) | NULL | Which stage failed | "moment_generation", "transcription" |
| `error_message` | TEXT | NULL | Error details if failed | "Model timeout after 600s" |
| `created_at` | TIMESTAMP | DEFAULT NOW() | Record creation timestamp | "2026-02-06 10:00:00" |

**Indexes:**
- `idx_pipeline_history_identifier` UNIQUE on `identifier`
- `idx_pipeline_history_video_id` on `video_id`
- `idx_pipeline_history_config_id` on `generation_config_id`
- `idx_pipeline_history_status` on `status`
- `idx_pipeline_history_started_at` on `started_at`

**Design Notes:**
- `id` is numeric primary key for database relationships
- `identifier` is business identifier used for Redis keys, logging, and external references
- One pipeline processes one video (1:1 relationship per execution)
- One config applies to entire pipeline (generation + refinement + clips use same config)
- Record created at pipeline start with status='running', updated progressively as pipeline executes
- `total_moments_generated` and `total_clips_created` updated during pipeline execution
- Status transitions: running → completed/failed/partial
- All foreign keys use numeric `id` columns for optimal performance
- ON DELETE CASCADE on video_id and generation_config_id - deleting video or config cascades to pipeline history

---

## Tables To Be Defined

The following tables are planned but not yet finalized:

- **Phase Tables** - Detailed per-phase tracking (download_phases, transcription_phases, etc.) - Optional for advanced analytics

These will be added in subsequent iterations.

---

## Storage Strategy

### What Goes in Database
- ✅ Video metadata (duration, codecs, resolution)
- ✅ Cloud storage URLs/references
- ✅ Transcripts (text + JSONB timestamps)
- ✅ Moments, clips metadata
- ✅ Processing history and analytics

### What Stays in Cloud Storage
- ✅ Video files (MP4, WebM, etc.)
- ✅ Video clips (generated moments)
- ✅ Thumbnail images
- ✅ Any binary assets

### What's Temporary (Not Stored)
- ❌ Audio files (extracted for transcription, then deleted)
- ❌ Local video downloads (uploaded to cloud, then deleted)
- ❌ Intermediate processing files

---

## Key Design Principles

1. **Cloud-First Architecture** - All permanent files stored in cloud, only references in DB
2. **Immutable Transcripts** - Write-once, never updated (no update_at column)
3. **No Denormalization** - No status flags; compute from relationships when needed
4. **JSONB for Flexibility** - Use JSONB for semi-structured data (timestamps, configs)
5. **Proper Indexing** - GIN indexes for JSONB/full-text, B-tree for common queries
6. **Cascading Deletes** - Automatic cleanup of related records when parent deleted

---

## Schema Refinement TODO

The following items need to be addressed to finalize the schema:

### 1. **Add `config_hash` to Generation Configs Table (Table 5)**
- **Status:** ✅ COMPLETED
- **Priority:** HIGH
- **Description:** Add `config_hash VARCHAR(64) UNIQUE NOT NULL` column for efficient config deduplication
- **Hash calculation:** SHA-256 of all config parameters EXCEPT `transcript_id` (to allow config reuse across videos)
- **Hash components:** `prompt_id + model + operation_type + temperature + top_p + top_k + min_moment_length + max_moment_length + min_moments + max_moments`
- **Purpose:** Before creating new config, check if identical config exists (enables reuse)
- **Note:** `transcript_id` is kept as FK for prompt reconstruction but NOT included in hash
- **Implementation:** Added to Table 5 schema with UNIQUE constraint and index, enhanced design notes with clear examples showing hash excludes transcript_id

---

### 2. **Add `source_url` Index to Videos Table (Table 1)**
- **Status:** ✅ COMPLETED
- **Priority:** MEDIUM
- **Description:** Add `idx_videos_source_url` on `source_url` column
- **Purpose:** Enable fast duplicate download detection (replace URL registry table)
- **Query pattern:** `SELECT id FROM videos WHERE source_url = $1` (should be fast)
- **Implementation:** Added to Table 1 indexes and design notes

---

### 3. **Change `segment_timestamps` to NOT NULL in Transcripts Table (Table 2)**
- **Status:** ✅ COMPLETED
- **Priority:** MEDIUM
- **Description:** Change constraint from `NULL` to `NOT NULL`
- **Rationale:** All transcripts must have both word-level and segment-level timestamps (business requirement)
- **Impact:** Ensures data quality - no incomplete transcripts
- **Implementation:** Updated Table 2 schema, both `word_timestamps` and `segment_timestamps` now NOT NULL

---

### 4. **Add ON DELETE CASCADE to Missing Foreign Keys**
- **Status:** ✅ COMPLETED
- **Priority:** MEDIUM
- **Description:** Explicitly define ON DELETE CASCADE for the following relationships:
  - `generation_configs.prompt_id FK → prompts(id)` - ✅ Added `ON DELETE CASCADE`
  - `generation_configs.transcript_id FK → transcripts(id)` - ✅ Added `ON DELETE CASCADE`
  - `moments.generation_config_id FK → generation_configs(id)` - ✅ Added `ON DELETE CASCADE`
  - `pipeline_history.generation_config_id FK → generation_configs(id)` - ✅ Added `ON DELETE CASCADE`
- **Rationale:** Consistent cascading delete behavior across all relationships
- **Note:** When parent is deleted, all dependent records are automatically cleaned up
- **Implementation:** Updated all FK constraints in Tables 3, 5, and 8 with ON DELETE CASCADE

---

### 5. **Clarify Clips Duration Calculation**
- **Status:** ✅ COMPLETED
- **Priority:** LOW
- **Description:** Update design notes to explicitly state duration is ALWAYS calculated, never stored
- **Calculation:** `duration = end_time - start_time` (computed in application code)
- **Rationale:** Avoid redundant data storage; duration is derivable from stored timestamps
- **Implementation:** Updated Table 6 design notes with clear statement that duration is never stored

---

### 6. **Update Next Steps Section**
- **Status:** ❌ Outdated
- **Priority:** LOW
- **Description:** Remove mentions of tables already defined
- **Current text:** "Define remaining tables (Moments, Clips, Thumbnails, etc.)"
- **Should say:** All core tables defined, ready for implementation

---

## Foreign Key Verification Summary

All foreign key relationships have been verified to have explicit ON DELETE behavior:

| From Table | Column | References | ON DELETE Behavior | ✓ |
|------------|--------|------------|-------------------|---|
| **Transcripts** | video_id | videos(id) | CASCADE | ✅ |
| **Moments** | video_id | videos(id) | CASCADE | ✅ |
| **Moments** | parent_id | moments(id) | SET NULL | ✅ |
| **Moments** | generation_config_id | generation_configs(id) | CASCADE | ✅ |
| **Generation Configs** | prompt_id | prompts(id) | CASCADE | ✅ |
| **Generation Configs** | transcript_id | transcripts(id) | CASCADE | ✅ |
| **Clips** | moment_id | moments(id) | CASCADE | ✅ |
| **Clips** | video_id | videos(id) | CASCADE | ✅ |
| **Thumbnails** | video_id | videos(id) | CASCADE | ✅ |
| **Thumbnails** | clip_id | clips(id) | CASCADE | ✅ |
| **Pipeline History** | video_id | videos(id) | CASCADE | ✅ |
| **Pipeline History** | generation_config_id | generation_configs(id) | CASCADE | ✅ |

**Total: 12 foreign key relationships - all have explicit ON DELETE behavior defined.**

---

## Implementation Checklist

When implementing these changes:

- [x] Add `config_hash` column to Table 5 (Generation Configs)
- [x] Add index to Generation Configs: `idx_generation_configs_hash` UNIQUE on `config_hash`
- [x] Add index to Table 1 (Videos): `idx_videos_source_url` on `source_url`
- [x] Update Table 2 (Transcripts): Change `segment_timestamps` from NULL to NOT NULL
- [x] Add ON DELETE CASCADE to all FK constraints listed in TODO #4
- [x] Update Table 6 (Clips) design notes to clarify duration calculation
- [x] Verify all foreign key relationships have explicit ON DELETE behavior
- [x] Document hash calculation logic excludes `transcript_id` but configs still maintain FK for reconstruction
- [ ] Review and update documentation after changes

---

## Open Questions for Future Consideration

### Optional Tables (Decide Later):
1. **AI Request Logs Table** - Currently stored in `logs/ai_requests/*.json`
   - Contains: Full AI request/response, parsing results, timing data
   - Decision needed: Keep as files or move to database?
   - If database: Enable queryable debugging, analytics on AI performance
   - If files: Simpler, can use TTL-based cleanup

2. **Model Configurations Table** - Currently stored in Redis (seeded from defaults)
   - Contains: Model SSH configs, model_ids, parameters (top_p, top_k)
   - Decision needed: Persist to database or keep Redis-only?
   - If database: Survive Redis restarts, version control on configs
   - If Redis: Simpler, faster access, reset to defaults on restart

---

**Document Status:** Schema design complete, pending refinements listed above  
**Last Updated:** February 6, 2026
