# Video Moments Backend

FastAPI backend server for the Video Moments service.

## Setup

1. Create and activate virtual environment (if not already done):
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Ensure videos are in `static/videos/` directory

## Running the Server

### Option 1: Using the startup script (Recommended)
```bash
./start_backend.sh
```

### Option 2: Manual start
```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8005 --reload
```

The server will start on http://localhost:8005

API documentation available at http://localhost:8005/docs

## API Endpoints

- `GET /api/videos` - List all videos
- `GET /api/videos/{video_id}` - Get video metadata
- `GET /api/videos/{video_id}/stream` - Stream video file (supports range requests)
- `GET /api/videos/{video_id}/thumbnail` - Placeholder for thumbnail (returns 404 for now)

## Video Storage

Videos should be placed in `static/videos/` directory. Supported formats: .mp4, .webm, .mov, .avi, .mkv, .ogg


