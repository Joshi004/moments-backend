from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import videos

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


@app.get("/")
async def root():
    return {"message": "Video Moments API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "healthy"}

