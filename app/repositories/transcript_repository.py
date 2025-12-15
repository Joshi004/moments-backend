"""
Repository for managing transcript data persistence.
"""
import logging
from pathlib import Path
from typing import Optional, Dict
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class TranscriptRepository(BaseRepository):
    """Repository for transcript file operations."""
    
    def __init__(self, base_path: Path):
        """
        Initialize transcript repository.
        
        Args:
            base_path: Path to transcripts directory
        """
        super().__init__(base_path)
    
    def _get_transcript_filename(self, audio_filename: str) -> str:
        """Convert audio filename to transcript JSON filename."""
        return f"{Path(audio_filename).stem}.json"
    
    def get(self, audio_filename: str) -> Optional[Dict]:
        """
        Load transcript data for an audio file.
        
        Args:
            audio_filename: Name of the audio file (e.g., "motivation.wav")
            
        Returns:
            Dictionary containing transcript data or None if file doesn't exist
        """
        if not audio_filename:
            return None
        
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.read_json(transcript_filename)
    
    def save(self, audio_filename: str, transcript_data: Dict) -> bool:
        """
        Save transcription data to a JSON file.
        
        Args:
            audio_filename: Name of the audio file
            transcript_data: Dictionary containing transcription response
            
        Returns:
            True if successful, False otherwise
        """
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.write_json(transcript_filename, transcript_data)
    
    def exists(self, audio_filename: str) -> bool:
        """
        Check if transcript file exists for a given audio filename.
        
        Args:
            audio_filename: Name of the audio file
            
        Returns:
            True if transcript file exists, False otherwise
        """
        if not audio_filename:
            return False
        
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.file_exists(transcript_filename)
    
    def delete(self, audio_filename: str) -> bool:
        """
        Delete transcript file for an audio file.
        
        Args:
            audio_filename: Name of the audio file
            
        Returns:
            True if successful, False otherwise
        """
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.delete_file(transcript_filename)

