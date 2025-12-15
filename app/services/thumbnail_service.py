import cv2
import os
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def get_thumbnails_directory() -> Path:
    """Get the path to the thumbnails directory."""
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent
    thumbnails_dir = backend_dir / "static" / "thumbnails"
    thumbnails_dir = thumbnails_dir.resolve()
    
    # Create directory if it doesn't exist
    thumbnails_dir.mkdir(parents=True, exist_ok=True)
    
    return thumbnails_dir


def get_thumbnail_path(video_filename: str) -> Path:
    """Get the path for a thumbnail file based on video filename."""
    thumbnails_dir = get_thumbnails_directory()
    # Replace video extension with .jpg
    thumbnail_filename = Path(video_filename).stem + ".jpg"
    return thumbnails_dir / thumbnail_filename


def extract_frame_from_video(video_path: Path, output_path: Path, frame_time_seconds: Optional[float] = None) -> bool:
    """
    Extract a frame from a video and save it as a thumbnail.
    
    Args:
        video_path: Path to the video file
        output_path: Path where thumbnail should be saved
        frame_time_seconds: Specific time in seconds to extract frame. If None, uses default strategy.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Open video file
        cap = cv2.VideoCapture(str(video_path))
        
        if not cap.isOpened():
            logger.error(f"Could not open video file: {video_path}")
            return False
        
        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0
        
        # Determine frame time
        if frame_time_seconds is None:
            # Default strategy: 10% of duration or 1 second, whichever is smaller
            if duration > 0:
                frame_time = min(duration * 0.1, 1.0)
            else:
                frame_time = 1.0
        else:
            frame_time = frame_time_seconds
        
        # Calculate frame number
        frame_number = int(frame_time * fps) if fps > 0 else 0
        
        # Seek to the desired frame
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        
        # Read the frame
        ret, frame = cap.read()
        
        if not ret or frame is None:
            logger.error(f"Could not read frame from video: {video_path}")
            cap.release()
            return False
        
        # Resize to standard thumbnail size (16:9 aspect ratio)
        target_width = 640
        target_height = 360
        frame_resized = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        
        # Save as JPEG
        success = cv2.imwrite(str(output_path), frame_resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
        
        cap.release()
        
        if not success:
            logger.error(f"Could not save thumbnail to: {output_path}")
            return False
        
        logger.info(f"Successfully generated thumbnail: {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error generating thumbnail for {video_path}: {str(e)}")
        return False


def generate_thumbnail(video_path: Path, frame_time_seconds: Optional[float] = None) -> Optional[Path]:
    """
    Generate a thumbnail for a video file.
    
    Args:
        video_path: Path to the video file
        frame_time_seconds: Specific time in seconds to extract frame. If None, uses default strategy.
    
    Returns:
        Path to the generated thumbnail if successful, None otherwise
    """
    if not video_path.exists():
        logger.error(f"Video file does not exist: {video_path}")
        return None
    
    thumbnail_path = get_thumbnail_path(video_path.name)
    
    # Check if thumbnail already exists
    if thumbnail_path.exists():
        logger.info(f"Thumbnail already exists: {thumbnail_path}")
        return thumbnail_path
    
    # Generate thumbnail
    success = extract_frame_from_video(video_path, thumbnail_path, frame_time_seconds)
    
    if success:
        return thumbnail_path
    else:
        return None


def generate_thumbnails_for_all_videos() -> dict:
    """
    Generate thumbnails for all videos in the videos directory.
    
    Returns:
        Dictionary with 'success' count and 'failed' list
    """
    from app.utils.video import get_video_files
    
    video_files = get_video_files()
    results = {
        'success': 0,
        'failed': []
    }
    
    for video_file in video_files:
        thumbnail_path = generate_thumbnail(video_file)
        if thumbnail_path:
            results['success'] += 1
        else:
            results['failed'].append(video_file.name)
    
    return results


def get_thumbnail_url(video_filename: str) -> Optional[str]:
    """
    Get the URL path for a thumbnail if it exists.
    
    Args:
        video_filename: Name of the video file
    
    Returns:
        URL path to thumbnail or None if it doesn't exist
    """
    thumbnail_path = get_thumbnail_path(video_filename)
    if thumbnail_path.exists():
        # Return relative URL path
        return f"/static/thumbnails/{thumbnail_path.name}"
    return None



