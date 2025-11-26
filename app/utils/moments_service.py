import json
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import logging
import hashlib

logger = logging.getLogger(__name__)


def get_moments_directory() -> Path:
    """Get the path to the moments directory."""
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent
    moments_dir = backend_dir / "static" / "moments"
    moments_dir = moments_dir.resolve()
    
    # Create directory if it doesn't exist
    moments_dir.mkdir(parents=True, exist_ok=True)
    
    return moments_dir


def get_moments_file_path(video_filename: str) -> Path:
    """Get the path for a moments JSON file based on video filename."""
    moments_dir = get_moments_directory()
    # Replace video extension with .json
    moments_filename = Path(video_filename).stem + ".json"
    return moments_dir / moments_filename


def generate_moment_id(start_time: float, end_time: float) -> str:
    """
    Generate a unique ID for a moment based on its timestamps.
    
    Args:
        start_time: Start time in seconds
        end_time: End time in seconds
        
    Returns:
        Hash-based unique identifier
    """
    # Create a deterministic ID from timestamps
    id_string = f"{start_time:.2f}_{end_time:.2f}"
    # Use first 16 characters of SHA256 hash for shorter IDs
    return hashlib.sha256(id_string.encode()).hexdigest()[:16]


def load_moments(video_filename: str) -> List[Dict]:
    """
    Load moments from JSON file for a video.
    Auto-assigns IDs to moments that don't have them.
    
    Args:
        video_filename: Name of the video file
        
    Returns:
        List of moment dictionaries, or empty list if file doesn't exist
    """
    moments_file = get_moments_file_path(video_filename)
    
    if not moments_file.exists():
        return []
    
    try:
        with open(moments_file, 'r', encoding='utf-8') as f:
            moments = json.load(f)
            # Validate it's a list
            if not isinstance(moments, list):
                logger.warning(f"Moments file {moments_file} does not contain a list, returning empty list")
                return []
            
            # Auto-assign IDs to moments that don't have them
            modified = False
            for moment in moments:
                if 'id' not in moment or not moment['id']:
                    moment['id'] = generate_moment_id(moment['start_time'], moment['end_time'])
                    modified = True
                # Ensure is_refined and parent_id fields exist
                if 'is_refined' not in moment:
                    moment['is_refined'] = False
                    modified = True
                if 'parent_id' not in moment:
                    moment['parent_id'] = None
                    modified = True
                # Ensure model_name and prompt fields exist (backward compatibility)
                if 'model_name' not in moment:
                    moment['model_name'] = None
                    modified = True
                if 'prompt' not in moment:
                    moment['prompt'] = None
                    modified = True
            
            # Save if we modified any moments
            if modified:
                save_moments(video_filename, moments)
            
            return moments
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing moments JSON file {moments_file}: {str(e)}")
        return []
    except Exception as e:
        logger.error(f"Error loading moments from {moments_file}: {str(e)}")
        return []


def save_moments(video_filename: str, moments: List[Dict]) -> bool:
    """
    Save moments to JSON file for a video.
    
    Args:
        video_filename: Name of the video file
        moments: List of moment dictionaries
        
    Returns:
        True if successful, False otherwise
    """
    moments_file = get_moments_file_path(video_filename)
    
    try:
        # Ensure directory exists
        moments_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Write moments to file
        with open(moments_file, 'w', encoding='utf-8') as f:
            json.dump(moments, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Successfully saved moments to {moments_file}")
        return True
    except Exception as e:
        logger.error(f"Error saving moments to {moments_file}: {str(e)}")
        return False


def validate_moment(moment: Dict, existing_moments: List[Dict], video_duration: float) -> Tuple[bool, Optional[str]]:
    """
    Validate a moment against rules.
    
    Args:
        moment: Dictionary with start_time, end_time, and title
        existing_moments: List of existing moments
        video_duration: Total duration of the video in seconds
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields
    if 'start_time' not in moment or 'end_time' not in moment or 'title' not in moment:
        return False, "Missing required fields: start_time, end_time, and title are required"
    
    start_time = moment['start_time']
    end_time = moment['end_time']
    title = moment['title']
    is_refined = moment.get('is_refined', False)
    
    # Validate types
    try:
        start_time = float(start_time)
        end_time = float(end_time)
    except (ValueError, TypeError):
        return False, "start_time and end_time must be numbers"
    
    # Validate title
    if not isinstance(title, str) or not title.strip():
        return False, "title must be a non-empty string"
    
    # Validate time bounds
    if start_time < 0:
        return False, "start_time must be >= 0"
    
    if end_time > video_duration:
        return False, f"end_time must be <= video duration ({video_duration} seconds)"
    
    if end_time <= start_time:
        return False, "end_time must be greater than start_time"
    
    # Validate duration (≤ 2 minutes = 120 seconds) - only for non-refined moments
    if not is_refined:
        duration = end_time - start_time
        if duration > 120:
            return False, f"Moment duration ({duration} seconds) exceeds maximum of 120 seconds (2 minutes)"
    
    # Check for overlaps with existing moments - skip for refined moments
    if not is_refined:
        for existing in existing_moments:
            existing_start = existing.get('start_time', 0)
            existing_end = existing.get('end_time', 0)
            
            # Check if new moment overlaps with existing moment
            # Overlap occurs if: new_start < existing_end AND new_end > existing_start
            if start_time < existing_end and end_time > existing_start:
                return False, f"Moment overlaps with existing moment '{existing.get('title', 'Untitled')}' ({existing_start}s - {existing_end}s)"
    
    return True, None


def get_moment_by_id(video_filename: str, moment_id: str) -> Optional[Dict]:
    """
    Get a moment by its ID.
    
    Args:
        video_filename: Name of the video file
        moment_id: ID of the moment to find
        
    Returns:
        Moment dictionary or None if not found
    """
    moments = load_moments(video_filename)
    for moment in moments:
        if moment.get('id') == moment_id:
            return moment
    return None


def add_moment(video_filename: str, moment: Dict, video_duration: float) -> Tuple[bool, Optional[str], Optional[Dict]]:
    """
    Add a moment to a video after validation.
    
    Args:
        video_filename: Name of the video file
        moment: Dictionary with start_time, end_time, and title
        video_duration: Total duration of the video in seconds
        
    Returns:
        Tuple of (success, error_message, created_moment)
    """
    # Load existing moments
    existing_moments = load_moments(video_filename)
    
    # Validate the new moment
    is_valid, error_message = validate_moment(moment, existing_moments, video_duration)
    
    if not is_valid:
        return False, error_message, None
    
    # Generate ID if not present
    if 'id' not in moment or not moment['id']:
        moment['id'] = generate_moment_id(moment['start_time'], moment['end_time'])
    
    # Ensure is_refined and parent_id fields exist
    if 'is_refined' not in moment:
        moment['is_refined'] = False
    if 'parent_id' not in moment:
        moment['parent_id'] = None
    # Ensure model_name and prompt fields exist (backward compatibility)
    if 'model_name' not in moment:
        moment['model_name'] = None
    if 'prompt' not in moment:
        moment['prompt'] = None
    
    # Add the moment to the list
    existing_moments.append(moment)
    
    # Save moments
    success = save_moments(video_filename, existing_moments)
    
    if success:
        return True, None, moment
    else:
        return False, "Failed to save moment", None

