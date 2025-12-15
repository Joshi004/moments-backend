import os
from pathlib import Path


def get_videos_directory() -> Path:
    """Get the path to the videos directory."""
    # Use __file__ to find the backend directory - this is the most reliable method
    # __file__ is app/utils/video_utils.py when imported
    current_file = Path(__file__).resolve()
    
    # Go up 3 levels: app/utils/video_utils.py -> app/utils -> app -> moments-backend
    backend_dir = current_file.parent.parent.parent
    videos_dir = backend_dir / "static" / "videos"
    
    # Resolve to absolute path and verify it's the correct one
    videos_dir = videos_dir.resolve()
    
    # Ensure we're not accidentally pointing to the root videos directory
    # The correct path should contain 'moments-backend/static/videos'
    videos_str = str(videos_dir)
    if 'moments-backend/static/videos' not in videos_str:
        # Fallback: use absolute path
        videos_dir = Path("/Users/nareshjoshi/Documents/TetherWorkspace/VideoMoments/moments-backend/static/videos").resolve()
    
    # Final verification
    if not videos_dir.exists():
        raise FileNotFoundError(f"Videos directory not found at: {videos_dir}")
    
    return videos_dir


def get_video_files():
    """Get list of video files from the videos directory."""
    videos_dir = get_videos_directory()
    
    # Verify we have the correct directory
    expected_path_ending = 'moments-backend/static/videos'
    actual_path = str(videos_dir)
    if expected_path_ending not in actual_path:
        raise ValueError(f"Videos directory path seems incorrect. Expected path containing '{expected_path_ending}', got '{actual_path}'")
    
    if not videos_dir.exists():
        raise FileNotFoundError(f"Videos directory does not exist: {videos_dir}")
    
    if not videos_dir.is_dir():
        raise NotADirectoryError(f"Videos path is not a directory: {videos_dir}")
    
    video_extensions = {'.mp4', '.webm', '.mov', '.avi', '.mkv', '.ogg'}
    video_files = []
    
    try:
        # Use Path.iterdir() - wrap in try-except to catch specific errors
        dir_iterator = videos_dir.iterdir()
        for file_path in dir_iterator:
            try:
                if file_path.is_file() and file_path.suffix.lower() in video_extensions:
                    video_files.append(file_path)
            except (PermissionError, OSError) as e:
                # Skip individual files we can't access
                continue
    except PermissionError as e:
        raise PermissionError(f"Cannot access videos directory: {videos_dir}. Error: {e}. Path: {videos_dir.resolve()}")
    except OSError as e:
        raise OSError(f"OS error accessing videos directory: {videos_dir}. Error: {e}. Path: {videos_dir.resolve()}")
    except Exception as e:
        raise Exception(f"Error reading videos directory {videos_dir}: {type(e).__name__}: {e}. Path: {videos_dir.resolve()}")
    
    return sorted(video_files)


def get_video_by_filename(filename: str):
    """Get a video file by its filename."""
    videos_dir = get_videos_directory()
    video_path = videos_dir / filename
    
    if video_path.exists() and video_path.is_file():
        return video_path
    return None

