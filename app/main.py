from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.routes import videos
from pathlib import Path

app = FastAPI(title="Video Moments API", version="1.0.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3005"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(videos.router, prefix="/api", tags=["videos"])

# Mount static files for thumbnails
thumbnails_dir = Path(__file__).parent.parent / "static" / "thumbnails"
thumbnails_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/thumbnails", StaticFiles(directory=str(thumbnails_dir)), name="thumbnails")

# Mount static files for audio
audio_dir = Path(__file__).parent.parent / "static" / "audio"
audio_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/audio", StaticFiles(directory=str(audio_dir)), name="audio")


@app.get("/")
async def root():
    return {"message": "Video Moments API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


