# VideoMoments — System Architecture Overview

> **Audience:** New team members joining the project.
> **Last updated:** February 2026

---

## 1. What is VideoMoments?

VideoMoments is an AI-powered video analysis platform. Given a video URL, it automatically:

1. Downloads the video and stores it in Google Cloud Storage (GCS)
2. Extracts audio and transcribes it using an ASR model (Parakeet)
3. Identifies key "moments" in the video using large language models (LLMs)
4. Extracts short video clips for each moment
5. Refines moment boundaries using a vision-language model that can watch the clips

The result is a set of titled, timestamped highlights — ready for review, editing, or export.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                                    USERS / BROWSER                                  │
└────────────────────────────────────────┬────────────────────────────────────────────┘
                                         │  HTTP (port 3005)
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                          REACT FRONTEND  (moments-frontend)                         │
│                                                                                     │
│   Pages: Dashboard │ Video Library │ Video Detail │ URL Generate │ Pipeline Monitor │
│   Communication: REST API calls via Axios + HTTP polling for status updates         │
└────────────────────────────────────────┬────────────────────────────────────────────┘
                                         │  HTTP REST (port 7005)
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         FASTAPI BACKEND  (moments-backend)                          │
│                                                                                     │
│   Routes: /api/videos │ /api/pipeline │ /api/generate_moments │ /api/admin          │
│   Services: PipelineOrchestrator │ GenerationService │ TranscriptService            │
│   Data Layer: SQLAlchemy (async) Repositories                                       │
├──────────┬──────────────────────────┬───────────────────────────┬───────────────────┤
│          │                          │                           │                   │
│          ▼                          ▼                           ▼                   │
│  ┌───────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐       │
│  │  PostgreSQL   │   │       Redis          │   │  Google Cloud Storage    │       │
│  │  (vision_ai)  │   │  Streams + Hashes    │   │  (media files)           │       │
│  │               │   │                      │   │                          │       │
│  │  8 tables:    │   │  Job queue:          │   │  Videos (.mp4)           │       │
│  │  videos       │   │   pipeline:requests  │   │  Audio  (.wav/.mp3)      │       │
│  │  transcripts  │   │  Status:             │   │  Clips  (.mp4)           │       │
│  │  moments      │   │   pipeline:{id}:*    │   │  Thumbnails (.jpg)       │       │
│  │  clips        │   │  Config:             │   │                          │       │
│  │  prompts      │   │   model:config:*     │   │  Signed URLs for access  │       │
│  │  gen_configs  │   │  Locks, History      │   │                          │       │
│  │  thumbnails   │   │                      │   │                          │       │
│  │  pipe_history │   │                      │   │                          │       │
│  └───────────────┘   └──────────┬───────────┘   └──────────────────────────┘       │
│                                 │                                                   │
└─────────────────────────────────┼───────────────────────────────────────────────────┘
                                  │  Redis Stream (XREADGROUP)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                         PIPELINE WORKER  (same codebase, separate process)           │
│                                                                                     │
│   Consumes jobs from Redis Stream → Executes 8-stage pipeline → Updates status      │
│                                                                                     │
│   Stages:  Download → Audio Extract → Audio Upload → Transcription →                │
│            Moment Generation → Clip Extraction → Clip Upload → Refinement           │
│                                                                                     │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                              SSH TUNNELS (on-demand)                                 │
│                                                                                     │
│   ┌─────────────┐    ┌─────────────┐    ┌──────────────┐    ┌─────────────────┐    │
│   │  Parakeet   │    │  MiniMax    │    │  Qwen3-Omni  │    │  Qwen3-VL-FP8  │    │
│   │  (ASR)      │    │  (Text LLM) │    │  (Text LLM)  │    │  (Vision LLM)  │    │
│   │  :6106→8006 │    │  :8007→7104 │    │  :7101→8002  │    │  :6010→8010    │    │
│   │  worker-7   │    │  worker-7   │    │  worker-9    │    │  worker-16     │    │
│   │             │    │             │    │              │    │                 │    │
│   │  /transcribe│    │  /v1/chat/  │    │  /v1/chat/   │    │  /v1/chat/     │    │
│   │             │    │  completions│    │  completions │    │  completions   │    │
│   └─────────────┘    └─────────────┘    └──────────────┘    └─────────────────┘    │
│                                                                                     │
│   All LLMs expose an OpenAI-compatible /v1/chat/completions endpoint                │
│   Qwen3-VL-FP8 is the only model that accepts video input (multimodal)              │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Frontend → Backend Communication

### Technology

| Aspect              | Detail                                                     |
|----------------------|------------------------------------------------------------|
| Framework            | React 19 + Material UI v7                                  |
| HTTP Client          | Axios with dynamic base URL                                |
| Real-time updates    | HTTP polling (2–3 second intervals), **no WebSockets/SSE** |
| State management     | React Context + component-local `useState`                 |
| Routing              | React Router v7                                            |

### How it works

```
┌─────────────┐         ┌──────────────────┐         ┌──────────────────┐
│   React UI  │──POST──▶│  /api/pipeline/  │──XADD──▶│  Redis Stream    │
│             │         │  {id}/start      │         │  pipeline:       │
│             │         │                  │         │  requests        │
│             │         └──────────────────┘         └──────────────────┘
│             │
│  setInterval│         ┌──────────────────┐         ┌──────────────────┐
│  (2 sec)    │──GET───▶│  /api/pipeline/  │──HGET──▶│  Redis Hash      │
│             │         │  {id}/status     │         │  pipeline:{id}:  │
│             │         │                  │         │  active          │
└─────────────┘         └──────────────────┘         └──────────────────┘
```

1. **User triggers an action** (e.g., "Generate Moments") in the UI.
2. Frontend calls a REST endpoint on the backend (e.g., `POST /api/pipeline/{id}/start`).
3. Backend enqueues a job in Redis and immediately returns `202 Accepted`.
4. Frontend begins **polling** the status endpoint every 2 seconds.
5. Backend reads real-time status from Redis and returns it.
6. Polling stops when status is `completed`, `failed`, or `cancelled`.

### Key API Endpoint Groups

| Group                 | Base Path                          | Purpose                                |
|-----------------------|------------------------------------|----------------------------------------|
| Videos                | `/api/videos`                      | List, stream, metadata, thumbnails     |
| Moments               | `/api/videos/{id}/moments`         | CRUD, generate, refine moments         |
| Transcripts           | `/api/videos/{id}/transcript`      | Audio extraction, transcription        |
| Clips                 | `/api/videos/{id}/extract-clips`   | Extract and check clip availability    |
| Pipeline              | `/api/pipeline/{id}`               | Start, status, cancel, history         |
| URL Generate          | `/api/generate_moments`            | One-shot: URL → full pipeline          |
| Admin / Model Config  | `/api/admin/models`                | View and update AI model configurations|
| Health                | `/health`                          | Checks Redis + PostgreSQL connectivity |

---

## 4. Backend → Redis Communication

Redis serves as the **coordination backbone** for the entire system. It is used for five distinct purposes:

### 4.1 Job Queue (Redis Streams)

```
API Server                           Redis                           Worker
    │                                  │                                │
    │──XADD pipeline:requests ──────▶  │                                │
    │   {request_id, video_id,         │                                │
    │    config, requested_at}         │                                │
    │                                  │  ◀── XREADGROUP (blocking) ────│
    │                                  │       group: pipeline_workers  │
    │                                  │       consumer: worker-{id}    │
    │                                  │                                │
    │                                  │  ◀── XACK (on completion) ─────│
    │                                  │                                │
```

- **Stream key:** `pipeline:requests`
- **Consumer group:** `pipeline_workers`
- Workers use `XREADGROUP` with blocking reads and `XAUTOCLAIM` for stale message recovery (idle > 60s).

### 4.2 Real-Time Status (Redis Hashes)

```
Key: pipeline:{video_id}:active

Fields:
  request_id          = "abc-123"
  status              = "processing"
  current_stage       = "TRANSCRIPTION"
  AUDIO_EXTRACTION_status      = "completed"
  AUDIO_EXTRACTION_started_at  = "2026-02-18T10:00:00Z"
  AUDIO_EXTRACTION_completed_at= "2026-02-18T10:00:45Z"
  TRANSCRIPTION_status         = "processing"
  TRANSCRIPTION_started_at     = "2026-02-18T10:00:46Z"
  ...
```

Every pipeline stage writes its own status fields. The frontend reads this hash on each poll.

### 4.3 Distributed Locking

| Key                              | TTL      | Purpose                                    |
|----------------------------------|----------|--------------------------------------------|
| `pipeline:{video_id}:lock`       | 30 min   | Prevents two workers processing same video  |
| `pipeline:{video_id}:cancel`     | 5 min    | Signals graceful cancellation to worker     |

### 4.4 Pipeline History (Hash + Sorted Set)

```
pipeline:run:{request_id}     → Hash with full pipeline run details (TTL: 24h)
pipeline:{video_id}:history   → Sorted Set of request_ids by timestamp
```

### 4.5 Model Configuration Registry

```
model:config:minimax         → Hash {ssh_host, ssh_user, local_port, remote_port, ...}
model:config:qwen3_vl_fp8   → Hash {ssh_host, ssh_user, local_port, remote_port, supports_video: true, ...}
model:config:parakeet        → Hash {ssh_host, ssh_user, local_port, remote_port, endpoint: /transcribe, ...}
model:config:_keys           → Set of all registered model keys
```

Seeded on startup from defaults in `app/utils/model_config.py`. Editable at runtime via the Admin API.

---

## 5. Backend → Database (PostgreSQL)

### Technology

| Aspect     | Detail                                          |
|------------|-------------------------------------------------|
| Database   | PostgreSQL (database: `vision_ai`)               |
| ORM        | SQLAlchemy 2.0 (async mode with `asyncpg`)       |
| Migrations | Alembic                                          |
| Pattern    | Repository pattern (`app/repositories/`)          |

### Entity Relationship Diagram

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────┐
│   prompts    │◀──┐   │ generation_configs│       │   videos     │
│              │   └───│ prompt_id (FK)    │       │              │
│  id          │       │ transcript_id(FK) │◀──┐   │  id          │
│  user_prompt │       │ model             │   │   │  identifier  │
│  system_prompt│      │ temperature       │   │   │  source_url  │
│  prompt_hash │       │ top_p, top_k      │   │   │  cloud_url   │
│  (SHA-256)   │       │ config_hash       │   │   │  title       │
└──────────────┘       │  (SHA-256)        │   │   │  duration    │
                       └────────┬──────────┘   │   │  resolution  │
                                │              │   └──────┬───────┘
                                │              │          │ 1
                                │              │          │
                     ┌──────────┘              │          ├─────────────┐
                     │ N:1                     │          │ 1:1         │ 1:N
                     ▼                         │          ▼             ▼
              ┌──────────────┐                 │  ┌──────────────┐  ┌──────────────┐
              │   moments    │                 │  │ transcripts  │  │  thumbnails  │
              │              │                 │  │              │  │  (video OR   │
              │  id          │                 │  │  id          │  │   clip)      │
              │  identifier  │                 └──│  video_id    │  │              │
              │  video_id(FK)│──────────────┐     │  full_text   │  │  video_id(FK)│
              │  title       │              │     │  word_ts     │  │  clip_id(FK) │
              │  start_time  │              │     │  segment_ts  │  │  cloud_url   │
              │  end_time    │              │     │  language     │  └──────────────┘
              │  is_refined  │              │     └──────────────┘
              │  parent_id   │──┐ self-ref  │
              │  gen_config_id│  │(refined   │
              └──────┬───────┘  │ from)     │
                     │ 1:1      └───────────│───┐
                     ▼                      │   │
              ┌──────────────┐              │   │
              │    clips     │              │   │
              │              │              │   │
              │  id          │              │   │
              │  moment_id   │──────────────┘   │
              │  video_id(FK)│                  │
              │  cloud_url   │                  │
              │  start_time  │                  │
              │  end_time    │                  │
              │  padding_l/r │                  │
              └──────────────┘                  │
                                                │
              ┌──────────────────┐              │
              │ pipeline_history │              │
              │                  │              │
              │  id              │              │
              │  identifier      │              │
              │  video_id (FK)   │──────────────┘
              │  gen_config_id   │
              │  status          │
              │  started_at      │
              │  completed_at    │
              │  duration_seconds│
              │  error_stage     │
              └──────────────────┘
```

### Key Design Decisions

- **Hash-based deduplication:** `prompts` and `generation_configs` use SHA-256 hashes to avoid storing duplicates.
- **Self-referencing moments:** A refined moment points to its `parent_id` (the original moment). `is_refined = true` marks refined versions.
- **Dual storage:** Real-time pipeline status lives in Redis (fast reads for polling). Completed runs are archived to `pipeline_history` in PostgreSQL.
- **JSONB columns:** Transcripts store `word_timestamps` and `segment_timestamps` as JSONB for flexible querying.

---

## 6. Backend → AI Model Services

### Connection Method: SSH Tunnels

The AI models run on remote GPU servers. The backend connects to them through **on-demand SSH port-forwarding tunnels**.

```
┌─────────────────────┐         SSH Tunnel          ┌────────────────────────────┐
│  Backend / Worker    │ ◀═══════════════════════▶   │  Remote GPU Server         │
│                      │                             │                            │
│  localhost:6010  ────┼──── ssh naresh@85.x.x.146 ──┼──▶ worker-16:8010          │
│  (Qwen3-VL-FP8)     │         -L 6010:worker-16:  │     (vLLM serving model)   │
│                      │            8010             │                            │
│  localhost:6106  ────┼──── ssh naresh@85.x.x.146 ──┼──▶ worker-7:8006           │
│  (Parakeet)          │         -L 6106:worker-7:   │     (ASR service)          │
│                      │            8006             │                            │
└─────────────────────┘                              └────────────────────────────┘
```

Tunnels are created **per-request** using a context manager (`ssh_tunnel(model_key)`), verified by port probe, and torn down after use.

### Model Inventory

| Model Key       | Type              | Video Input | Remote Host  | Endpoint              | Use Case                           |
|-----------------|-------------------|-------------|--------------|------------------------|-------------------------------------|
| `parakeet`      | ASR               | N/A         | worker-7     | `/transcribe`          | Speech-to-text transcription        |
| `minimax`       | Text LLM          | No          | worker-7     | `/v1/chat/completions` | Text-only moment generation         |
| `qwen3_omni`    | Text LLM          | No          | worker-9     | `/v1/chat/completions` | Text-only moment generation         |
| `qwen3_vl_fp8`  | Vision-Language LLM| **Yes**     | worker-16    | `/v1/chat/completions` | Moment generation + video refinement|

### Model Selection Logic

```
Pipeline Config
  ├── generation_model  (default: qwen3_vl_fp8)
  └── refinement_model  (default: qwen3_vl_fp8)

                         ┌─────────────────────────────────────┐
                         │ Does refinement_model support video? │
                         └──────────────┬──────────────────────┘
                                        │
                          Yes           │           No
                     ┌──────────────────┴──────────────────┐
                     ▼                                     ▼
              QWEN_STAGES                           MINIMAX_STAGES
              (all 8 stages)                        (skip clip stages)
              ┌───────────────┐                     ┌───────────────┐
              │ 1. Download   │                     │ 1. Download   │
              │ 2. Audio Ext  │                     │ 2. Audio Ext  │
              │ 3. Audio Up   │                     │ 3. Audio Up   │
              │ 4. Transcript │                     │ 4. Transcript │
              │ 5. Moments    │                     │ 5. Moments    │
              │ 6. Clip Ext   │                     │ 6. Refinement │
              │ 7. Clip Up    │                     └───────────────┘
              │ 8. Refinement │
              └───────────────┘
```

When the refinement model **can** watch video (like `qwen3_vl_fp8`), clips are extracted and uploaded so the model can see them. When it **cannot** (like `minimax`), clip stages are skipped entirely and refinement happens with text alone.

### API Format (OpenAI-Compatible)

All LLM services expose the same interface:

**Request:**
```json
{
  "model": "qwen3-vl-fp8",
  "messages": [
    {"role": "system", "content": "You are a video moment analyst..."},
    {"role": "user", "content": [
      {"type": "text", "text": "Analyze this transcript..."},
      {"type": "video_url", "video_url": {"url": "https://storage.googleapis.com/..."}}
    ]}
  ],
  "temperature": 0.3,
  "max_tokens": 4096
}
```

**Generation Response → parsed moments:**
```json
[
  {"start_time": 10.5, "end_time": 45.2, "title": "Introduction to the topic"},
  {"start_time": 52.0, "end_time": 98.7, "title": "Key demonstration"},
  ...
]
```

**Refinement Response → refined timestamps:**
```json
{"start_time": 12.3, "end_time": 43.8}
```

**Transcription Request/Response (Parakeet):**
```
POST /transcribe  (multipart: audio file)

Response:
{
  "transcription": "Full text of the audio...",
  "word_timestamps": [{"word": "Hello", "start": 0.0, "end": 0.5}, ...],
  "segment_timestamps": [{"text": "Hello world.", "start": 0.0, "end": 2.0}, ...],
  "processing_time": 15.5
}
```

---

## 7. The Pipeline — End-to-End Flow

This is the most important workflow in the system. Here is exactly what happens when a user clicks "Generate Moments":

```
 USER clicks "Generate Moments" on a video
   │
   ▼
 ① FRONTEND calls POST /api/pipeline/{video_id}/start
    Body: { generation_model: "qwen3_vl_fp8", refinement_model: "qwen3_vl_fp8", ... }
   │
   ▼
 ② BACKEND (API)
    ├── Validates request
    ├── Checks no active pipeline for this video (Redis lock)
    ├── Acquires lock: SET pipeline:{video_id}:lock
    ├── Creates status hash: HSET pipeline:{video_id}:active { status: "queued" }
    └── Enqueues job: XADD pipeline:requests { request_id, video_id, config }
    └── Returns 202 Accepted
   │
   ▼
 ③ FRONTEND starts polling GET /api/pipeline/{video_id}/status every 2 seconds
   │
   ▼
 ④ PIPELINE WORKER (separate process)
    ├── XREADGROUP picks up the message from the stream
    ├── Opens SSH tunnel to the generation model service
    │
    ├── STAGE 1: VIDEO_DOWNLOAD
    │   └── Downloads video from source URL → local temp file
    │
    ├── STAGE 2: AUDIO_EXTRACTION
    │   └── FFmpeg extracts audio from video → .wav file
    │
    ├── STAGE 3: AUDIO_UPLOAD
    │   └── Uploads .wav to Google Cloud Storage
    │
    ├── STAGE 4: TRANSCRIPTION
    │   ├── Opens SSH tunnel to Parakeet (port 6106)
    │   ├── Sends audio file to Parakeet /transcribe endpoint
    │   └── Receives transcript with word-level timestamps
    │   └── Saves to PostgreSQL (transcripts table)
    │
    ├── STAGE 5: MOMENT_GENERATION
    │   ├── Builds prompt with transcript text
    │   ├── Opens SSH tunnel to generation model
    │   ├── Calls /v1/chat/completions with the prompt
    │   ├── Parses response → list of {start_time, end_time, title}
    │   └── Saves moments to PostgreSQL
    │
    ├── STAGE 6: CLIP_EXTRACTION  (skipped if model has no video support)
    │   └── FFmpeg extracts a clip for each moment (parallel, up to 4 concurrent)
    │
    ├── STAGE 7: CLIP_UPLOAD  (skipped if model has no video support)
    │   └── Uploads each clip .mp4 to GCS
    │
    └── STAGE 8: MOMENT_REFINEMENT
        ├── For each moment:
        │   ├── Builds refinement prompt (includes clip URL if video-capable model)
        │   ├── Calls /v1/chat/completions
        │   ├── Parses refined {start_time, end_time}
        │   └── Creates new refined moment in DB with parent_id → original
        ├── Archives pipeline run to pipeline_history table
        ├── Releases lock
        └── XACK acknowledges the Redis Stream message
   │
   ▼
 ⑤ FRONTEND polling detects status = "completed"
    ├── Stops polling
    ├── Fetches updated moments from GET /api/videos/{id}/moments
    └── Displays the generated moments to the user
```

---

## 8. Concurrency & Scaling

### Concurrency Limits (Semaphore-based)

| Resource                 | Max Concurrent | Why                                  |
|--------------------------|----------------|--------------------------------------|
| Pipelines (per worker)   | 2              | Memory / CPU budget                   |
| Audio extraction         | 2              | FFmpeg is CPU-intensive               |
| Transcription            | 2              | Remote ASR service capacity           |
| Moment generation        | 2              | GPU memory on remote server           |
| Clip extraction          | 4              | FFmpeg I/O-bound, can parallelize more|
| Moment refinement        | 1              | GPU memory constraint                 |

### Scaling Strategy

```
                    ┌──────────────┐
                    │   Frontend   │  ← Static build, can be served by any CDN/nginx
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  API Server  │  ← Stateless; scale horizontally (N replicas)
                    │  (FastAPI)   │
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
       ┌────────────┐ ┌────────┐ ┌──────────┐
       │  Worker 1  │ │Worker 2│ │ Worker N │  ← Redis consumer group distributes work
       └────────────┘ └────────┘ └──────────┘
              │            │            │
              ▼            ▼            ▼
       ┌─────────────────────────────────────┐
       │              Redis                  │  ← Single instance (can be clustered)
       └─────────────────────────────────────┘
       ┌─────────────────────────────────────┐
       │           PostgreSQL                │  ← Single instance (can use replicas)
       └─────────────────────────────────────┘
```

Workers are independent processes. Adding more workers instantly increases pipeline throughput because the Redis consumer group (`pipeline_workers`) distributes messages automatically — each message goes to exactly one consumer.

---

## 9. Data Storage Strategy

| Data Type        | Hot (Real-time)              | Cold (Persistent)            | Files               |
|------------------|------------------------------|------------------------------|----------------------|
| Pipeline status  | Redis Hash                   | PostgreSQL `pipeline_history`| —                    |
| Model configs    | Redis Hash                   | Seeded from code defaults     | —                    |
| Video metadata   | —                            | PostgreSQL `videos`           | GCS (`.mp4`)         |
| Transcripts      | —                            | PostgreSQL `transcripts`      | —                    |
| Moments          | —                            | PostgreSQL `moments`          | —                    |
| Clips            | —                            | PostgreSQL `clips`            | GCS (`.mp4`)         |
| Thumbnails       | —                            | PostgreSQL `thumbnails`       | GCS (`.jpg`)         |
| Audio            | —                            | —                            | GCS (`.wav`/`.mp3`)  |

Video streaming uses **GCS signed URLs** (time-limited, refreshed proactively by the frontend 5 minutes before expiry).

---

## 10. Directory Structure

```
VideoMoments/
├── moments-frontend/                   # React application
│   ├── src/
│   │   ├── pages/                      # Route-level page components
│   │   ├── components/                 # Reusable UI components
│   │   │   ├── common/                 #   Shared components (StatusBadge, EmptyState, etc.)
│   │   │   ├── dashboard/              #   Dashboard widgets
│   │   │   ├── detail/                 #   Video detail page components
│   │   │   ├── library/                #   Video library components
│   │   │   ├── layout/                 #   AppLayout, Sidebar, TopBar
│   │   │   ├── pipelines/              #   Pipeline monitor components
│   │   │   ├── settings/               #   Model management, system health
│   │   │   └── URLGenerate/            #   URL generation workflow
│   │   ├── hooks/                      # Custom hooks (usePipelineStatus, etc.)
│   │   ├── services/api.js             # All API calls (single file)
│   │   ├── contexts/                   # React Context (Theme, Notifications)
│   │   └── theme/                      # MUI theme configuration
│   └── start_frontend.sh
│
├── moments-backend/                    # FastAPI application
│   ├── app/
│   │   ├── main.py                     # FastAPI app entry point
│   │   ├── api/endpoints/              # Route handlers
│   │   │   ├── videos.py
│   │   │   ├── pipeline.py
│   │   │   ├── moments.py
│   │   │   ├── transcripts.py
│   │   │   ├── clips.py
│   │   │   ├── generate_moments.py
│   │   │   ├── admin.py
│   │   │   └── delete.py
│   │   ├── core/
│   │   │   ├── config.py               # Pydantic Settings (all configuration)
│   │   │   └── redis.py                # Redis client (async + sync)
│   │   ├── database/
│   │   │   ├── session.py              # SQLAlchemy async session factory
│   │   │   └── models/                 # ORM models (8 tables)
│   │   ├── repositories/               # Data access layer
│   │   ├── services/
│   │   │   ├── ai/
│   │   │   │   ├── generation_service.py    # SSH tunnels + LLM calls
│   │   │   │   ├── refinement_service.py    # Moment refinement logic
│   │   │   │   └── prompt_tasks/            # Prompt builders + response parsers
│   │   │   │       ├── generation.py
│   │   │   │       └── refinement.py
│   │   │   ├── pipeline/
│   │   │   │   ├── orchestrator.py     # 8-stage pipeline execution
│   │   │   │   ├── status.py           # Redis status read/write
│   │   │   │   ├── lock.py             # Distributed locking
│   │   │   │   ├── concurrency.py      # Semaphore-based limits
│   │   │   │   └── redis_history.py    # Pipeline history in Redis
│   │   │   ├── config_registry.py      # Redis-backed model config store
│   │   │   ├── transcript_service.py   # Transcription orchestration
│   │   │   ├── audio_service.py        # FFmpeg audio extraction
│   │   │   ├── video_clipping_service.py # FFmpeg clip extraction
│   │   │   ├── gcs_downloader.py       # Download videos from URLs
│   │   │   └── job_tracker.py          # Legacy job tracking (Redis)
│   │   ├── utils/
│   │   │   ├── model_config.py         # Default model definitions
│   │   │   └── video.py                # Video utilities
│   │   └── workers/
│   │       └── pipeline_worker.py      # Redis Stream consumer
│   ├── alembic/                        # Database migrations
│   ├── run_worker.py                   # Worker entry point
│   ├── start_backend.sh                # Startup script (api / worker / all)
│   └── .env                            # GCS credentials, bucket config
│
└── database-migration/                 # Migration planning documents
    ├── DB_SCHEMA.md
    └── PHASE_*.md                      # 12 migration phase documents
```

---

## 11. How to Run Locally

```bash
# Terminal 1 — Start backend (API + Worker)
cd moments-backend
./start_backend.sh -p 7005          # Runs FastAPI on :7005 and pipeline worker

# Terminal 2 — Start frontend
cd moments-frontend
./start_frontend.sh                  # Runs React dev server on :3005
```

**Prerequisites:**
- PostgreSQL running locally (database: `vision_ai`)
- Redis running locally (default port 6379)
- SSH access to the remote GPU servers (for AI model services)
- GCS service account JSON file (for cloud storage)
- Python virtual environment with `requirements.txt` installed
- Node.js with `npm install` completed

---

## 12. Quick Reference: Request Lifecycle

| Step | Component | Action | Data Store |
|------|-----------|--------|------------|
| 1 | Frontend | User clicks "Generate" | — |
| 2 | Frontend | `POST /api/pipeline/{id}/start` | — |
| 3 | Backend API | Validate + acquire lock | Redis (lock) |
| 4 | Backend API | Enqueue job | Redis (stream) |
| 5 | Backend API | Return 202 | — |
| 6 | Frontend | Start polling `/status` | — |
| 7 | Worker | Pick up job from stream | Redis (stream) |
| 8 | Worker | Download video | Local filesystem |
| 9 | Worker | Extract audio (FFmpeg) | Local filesystem |
| 10 | Worker | Upload audio | GCS |
| 11 | Worker | Transcribe (Parakeet via SSH) | PostgreSQL |
| 12 | Worker | Generate moments (LLM via SSH) | PostgreSQL |
| 13 | Worker | Extract clips (FFmpeg) | Local filesystem |
| 14 | Worker | Upload clips | GCS |
| 15 | Worker | Refine moments (LLM via SSH) | PostgreSQL |
| 16 | Worker | Archive run + release lock | PostgreSQL + Redis |
| 17 | Frontend | Detect "completed" status | — |
| 18 | Frontend | Fetch and display moments | — |

---

## 13. Current State: Database Migration

The project is migrating from a local file-based storage system to a cloud + database architecture. This is happening in 12 phases:

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Database Foundation (schema, Alembic) | Complete |
| 2 | Videos → GCS + PostgreSQL | Complete |
| 3 | Video Streaming from GCS | Complete |
| 4 | Transcripts → PostgreSQL | Complete |
| 5 | Prompts & Generation Configs → PostgreSQL | Complete |
| 6 | Moments → PostgreSQL | In Progress |
| 7 | Clips → GCS + PostgreSQL | Planned |
| 8 | Thumbnails → GCS + PostgreSQL | Planned |
| 9 | Pipeline History → PostgreSQL | Planned |
| 10 | URL Registry Elimination | Planned |
| 11 | Temp File Management | Planned |
| 12 | Final Cleanup & Legacy Removal | Planned |

---

*For detailed API documentation, see `API_FLOW_DOCUMENTATION.md`.*
*For Redis/Worker details, see `WORKER_REDIS_ARCHITECTURE.md`.*
*For database schema details, see `database-migration/DB_SCHEMA.md`.*
