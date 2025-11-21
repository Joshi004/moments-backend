from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from app.models import Video
from app.utils.video_utils import get_video_files
from app.utils.thumbnail_service import generate_thumbnail, get_thumbnail_path, get_thumbnail_url
from pathlib import Path

router = APIRouter()


@router.get("/videos", response_model=list[Video])
async def list_videos():
    """List all available videos."""
    try:
        from app.utils.video_utils import get_videos_directory, get_video_files
        videos_dir = get_videos_directory()
        
        # Verify directory before proceeding
        if not videos_dir.exists():
            raise HTTPException(
                status_code=500, 
                detail=f"Videos directory does not exist: {videos_dir}"
            )
        
        video_files = get_video_files()
        videos = []
        
        for video_file in video_files:
            video_id = video_file.stem  # filename without extension
            thumbnail_url = get_thumbnail_url(video_file.name)
            videos.append(Video(
                id=video_id,
                filename=video_file.name,
                title=video_file.stem.replace("-", " ").replace("_", " ").title(),
                thumbnail_url=thumbnail_url
            ))
        
        return videos
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        error_msg = f"Error listing videos: {str(e)}\nDirectory: {get_videos_directory() if 'get_videos_directory' in dir() else 'unknown'}\nTraceback:\n{error_details}"
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/videos/{video_id}")
async def get_video(video_id: str):
    """Get metadata for a specific video."""
    video_files = get_video_files()
    
    # Find video by matching stem (filename without extension)
    for video_file in video_files:
        if video_file.stem == video_id:
            thumbnail_url = get_thumbnail_url(video_file.name)
            return Video(
                id=video_id,
                filename=video_file.name,
                title=video_file.stem.replace("-", " ").replace("_", " ").title(),
                thumbnail_url=thumbnail_url
            )
    
    raise HTTPException(status_code=404, detail="Video not found")


@router.get("/videos/{video_id}/stream")
async def stream_video(video_id: str, request: Request):
    """Stream video file with range request support."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    file_path = video_file
    file_size = file_path.stat().st_size
    
    # Handle range requests for video seeking
    range_header = request.headers.get("range")
    
    if range_header:
        # Parse range header
        range_match = range_header.replace("bytes=", "").split("-")
        start = int(range_match[0]) if range_match[0] else 0
        end = int(range_match[1]) if range_match[1] and range_match[1] else file_size - 1
        
        if start >= file_size or end >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        
        chunk_size = end - start + 1
        
        async def generate():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining:
                    chunk = f.read(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": "video/mp4",
        }
        
        return StreamingResponse(
            generate(),
            status_code=206,
            headers=headers,
            media_type="video/mp4"
        )
    else:
        # Return full file
        return FileResponse(
            file_path,
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            }
        )


@router.get("/videos/{video_id}/thumbnail")
async def get_thumbnail(video_id: str):
    """Get video thumbnail. Generates thumbnail if it doesn't exist."""
    video_files = get_video_files()
    
    # Find video by matching stem
    video_file = None
    for vf in video_files:
        if vf.stem == video_id:
            video_file = vf
            break
    
    if not video_file or not video_file.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Get or generate thumbnail
    thumbnail_path = get_thumbnail_path(video_file.name)
    
    # Generate thumbnail if it doesn't exist
    if not thumbnail_path.exists():
        generated_path = generate_thumbnail(video_file)
        if not generated_path:
            raise HTTPException(status_code=500, detail="Failed to generate thumbnail")
        thumbnail_path = generated_path
    
    if not thumbnail_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    
    return FileResponse(
        thumbnail_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000",  # Cache for 1 year
        }
    )

