# Video Moments Backend

FastAPI backend server for the Video Moments service — fully cloud-first architecture.

## Architecture

The backend runs on a single code path backed by:

| Store | Purpose |
|---|---|
| **PostgreSQL** | All metadata: videos, transcripts, moments, clips, thumbnails, pipeline history |
| **Google Cloud Storage (GCS)** | All media files: videos, audio, clips, thumbnails |
| **Redis** | Pipeline real-time state: job status, locks, stream messages, model configs |
| **Temp directory** | Short-lived processing files, auto-cleaned by scheduler |

The legacy `static/` file system (JSON files, local video/audio storage) has been fully removed.

## Setup

1. Create and activate virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Install FFmpeg (required for audio extraction):
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt-get install ffmpeg`

4. Configure environment variables (copy `.env.example` to `.env` and fill in values):

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `GCS_BUCKET_NAME` | Google Cloud Storage bucket name |
| `GCS_CREDENTIALS_PATH` | Path to GCS service account JSON |
| `REDIS_URL` | Redis connection URL |
| `BACKEND_PORT` | Port for the FastAPI server (default: 7005) |

## Running the Server

### Option 1: Using the startup script (recommended)
```bash
./start_backend.sh
```

### Option 2: Manual start
```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 7005 --reload
```

The server starts on http://localhost:7005

API documentation: http://localhost:7005/docs

## Data Flow

1. **Video ingestion** -- URL submitted → video downloaded from source → uploaded to GCS → database record created with `cloud_url`
2. **Audio extraction** -- Video downloaded from GCS to temp → audio extracted → audio uploaded to GCS → temp cleaned up
3. **Transcription** -- Audio uploaded to GCS → transcription service called → transcript saved to database
4. **Moment generation** -- Transcript fetched from DB → AI model generates moments → moments saved to database
5. **Clip extraction** -- Video downloaded from GCS to temp → FFmpeg extracts clips → clips uploaded to GCS → clip records in database → temp cleaned up
6. **Deletion** -- GCS files deleted → temp cleaned → Redis state cleared → database record deleted (CASCADE removes all related records)

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/videos` | List all videos from database |
| `POST` | `/api/videos` | Submit new video URL for processing |
| `DELETE` | `/api/videos/{id}` | Delete video and all associated data |
| `GET` | `/api/videos/{id}/moments` | Get all moments for a video |
| `POST` | `/api/videos/{id}/generate-moments` | Generate moments using AI |
| `POST` | `/api/videos/{id}/moments/{mid}/refine` | Refine a specific moment using AI |
| `GET` | `/api/videos/{id}/transcript` | Get transcript for a video |
| `GET` | `/api/videos/{id}/clip-extraction-status` | Get clip extraction status |
| `GET` | `/api/clips/{moment_id}/stream` | Stream a clip (redirects to GCS signed URL) |
| `POST` | `/api/pipeline/start` | Start full processing pipeline |
| `GET` | `/api/pipeline/status/{id}` | Get pipeline status |
| `GET` | `/health` | Health check (database + Redis) |

## Directory Structure

```
moments-backend/
├── app/
│   ├── api/             # FastAPI routers and endpoint handlers
│   ├── core/            # Config, logging, Redis client
│   ├── database/        # SQLAlchemy models, session, Alembic migrations
│   ├── repositories/    # Database CRUD operations
│   ├── services/        # Business logic, pipeline orchestration
│   │   ├── ai/          # Moment generation and refinement
│   │   └── pipeline/    # Pipeline stages, status, locking
│   ├── utils/           # Utility functions (timestamps, model config)
│   └── workers/         # Background pipeline worker
├── alembic/             # Database migrations
├── temp/                # Managed temp directory (auto-cleaned, gitignored)
└── requirements.txt
```
