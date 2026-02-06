"""
Repository for managing moment data persistence.
"""
import hashlib
import logging
from pathlib import Path
from typing import Optional, List, Dict
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class MomentsRepository(BaseRepository):
    """Repository for moment file operations."""
    
    def __init__(self, base_path: Path):
        """
        Initialize moments repository.
        
        Args:
            base_path: Path to moments directory
        """
        super().__init__(base_path)
    
    def _get_moments_filename(self, video_filename: str) -> str:
        """Convert video filename to moments JSON filename."""
        return f"{Path(video_filename).stem}.json"
    
    def generate_moment_id(self, start_time: float, end_time: float) -> str:
        """
        Generate a unique ID for a moment based on its timestamps.
        
        Args:
            start_time: Start time in seconds
            end_time: End time in seconds
            
        Returns:
            Hash-based unique identifier
        """
        id_string = f"{start_time:.2f}_{end_time:.2f}"
        return hashlib.sha256(id_string.encode()).hexdigest()[:16]
    
    def get_by_video(self, video_filename: str) -> List[Dict]:
        """
        Load all moments for a video.
        Auto-assigns IDs to moments that don't have them.
        
        Args:
            video_filename: Name of the video file
            
        Returns:
            List of moment dictionaries, or empty list if file doesn't exist
        """
        moments_filename = self._get_moments_filename(video_filename)
        moments = self.read_json(moments_filename)
        
        if moments is None:
            return []
        
        if not isinstance(moments, list):
            logger.warning(f"Moments file for {video_filename} does not contain a list")
            return []
        
        return moments
    
    def get_by_id(self, video_filename: str, moment_id: str) -> Optional[Dict]:
        """
        Get a specific moment by its ID.
        
        Args:
            video_filename: Name of the video file
            moment_id: ID of the moment to find
            
        Returns:
            Moment dictionary or None if not found
        """
        moments = self.get_by_video(video_filename)
        for moment in moments:
            if moment.get('id') == moment_id:
                return moment
        return None
    
    def save(self, video_filename: str, moments: List[Dict]) -> bool:
        """
        Save moments for a video.
        
        Args:
            video_filename: Name of the video file
            moments: List of moment dictionaries
            
        Returns:
            True if successful, False otherwise
        """
        moments_filename = self._get_moments_filename(video_filename)
        return self.write_json(moments_filename, moments)
    
    def add(self, video_filename: str, moment: Dict) -> Optional[Dict]:
        """
        Add a moment to a video's moment list.
        
        Args:
            video_filename: Name of the video file
            moment: Moment dictionary
            
        Returns:
            The added moment with ID, or None if save failed
        """
        # Load existing moments
        moments = self.get_by_video(video_filename)
        
        # Generate ID if not present
        if 'id' not in moment or not moment['id']:
            moment['id'] = self.generate_moment_id(
                moment['start_time'], 
                moment['end_time']
            )
        
        # Ensure required fields exist
        if 'is_refined' not in moment:
            moment['is_refined'] = False
        if 'parent_id' not in moment:
            moment['parent_id'] = None
        if 'model_name' not in moment:
            moment['model_name'] = None
        if 'prompt' not in moment:
            moment['prompt'] = None
        if 'generation_config' not in moment:
            moment['generation_config'] = None
        
        # Add the moment
        moments.append(moment)
        
        # Save moments
        if self.save(video_filename, moments):
            return moment
        return None
    
    def update(self, video_filename: str, moment_id: str, updated_moment: Dict) -> bool:
        """
        Update an existing moment.
        
        Args:
            video_filename: Name of the video file
            moment_id: ID of the moment to update
            updated_moment: Updated moment data
            
        Returns:
            True if successful, False if moment not found or save failed
        """
        moments = self.get_by_video(video_filename)
        
        # Find and update the moment
        found = False
        for i, moment in enumerate(moments):
            if moment.get('id') == moment_id:
                moments[i] = updated_moment
                found = True
                break
        
        if not found:
            return False
        
        return self.save(video_filename, moments)
    
    def delete(self, video_filename: str, moment_id: str) -> bool:
        """
        Delete a moment.
        
        Args:
            video_filename: Name of the video file
            moment_id: ID of the moment to delete
            
        Returns:
            True if successful, False if moment not found or save failed
        """
        moments = self.get_by_video(video_filename)
        
        # Filter out the moment
        initial_count = len(moments)
        moments = [m for m in moments if m.get('id') != moment_id]
        
        if len(moments) == initial_count:
            return False  # Moment not found
        
        return self.save(video_filename, moments)
    
    def exists(self, video_filename: str) -> bool:
        """
        Check if moments file exists for a video.
        
        Args:
            video_filename: Name of the video file
            
        Returns:
            True if moments file exists, False otherwise
        """
        moments_filename = self._get_moments_filename(video_filename)
        return self.file_exists(moments_filename)

