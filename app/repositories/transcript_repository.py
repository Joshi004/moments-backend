"""
Repository for managing transcript data persistence.

DEPRECATED: This repository is deprecated. Use transcript_db_repository for database-backed operations.
This class is kept for backward compatibility but will be removed in a future version.
"""
import warnings
import logging
from pathlib import Path
from typing import Optional, Dict
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class TranscriptRepository(BaseRepository):
    """
    Repository for transcript file operations.
    
    DEPRECATED: Use transcript_db_repository module for database-backed operations.
    """
    
    def __init__(self, base_path: Path):
        """
        Initialize transcript repository.
        
        DEPRECATED: Use transcript_db_repository instead.
        
        Args:
            base_path: Path to transcripts directory
        """
        warnings.warn(
            "TranscriptRepository is deprecated. Use transcript_db_repository for database operations.",
            DeprecationWarning,
            stacklevel=2
        )
        super().__init__(base_path)
    
    def _get_transcript_filename(self, audio_filename: str) -> str:
        """Convert audio filename to transcript JSON filename."""
        return f"{Path(audio_filename).stem}.json"
    
    def get(self, audio_filename: str) -> Optional[Dict]:
        """
        Load transcript data for an audio file.
        
        DEPRECATED: Use async load_transcript() from transcript_service instead.
        
        Args:
            audio_filename: Name of the audio file (e.g., "motivation.wav")
            
        Returns:
            Dictionary containing transcript data or None if file doesn't exist
        """
        warnings.warn(
            "TranscriptRepository.get() is deprecated. Use async load_transcript() from transcript_service.",
            DeprecationWarning,
            stacklevel=2
        )
        if not audio_filename:
            return None
        
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.read_json(transcript_filename)
    
    def save(self, audio_filename: str, transcript_data: Dict) -> bool:
        """
        Save transcription data to a JSON file.
        
        DEPRECATED: Use async save_transcript() from transcript_service instead.
        
        Args:
            audio_filename: Name of the audio file
            transcript_data: Dictionary containing transcription response
            
        Returns:
            True if successful, False otherwise
        """
        warnings.warn(
            "TranscriptRepository.save() is deprecated. Use async save_transcript() from transcript_service.",
            DeprecationWarning,
            stacklevel=2
        )
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.write_json(transcript_filename, transcript_data)
    
    def exists(self, audio_filename: str) -> bool:
        """
        Check if transcript file exists for a given audio filename.
        
        DEPRECATED: Use async check_transcript_exists() from transcript_service instead.
        
        Args:
            audio_filename: Name of the audio file
            
        Returns:
            True if transcript file exists, False otherwise
        """
        warnings.warn(
            "TranscriptRepository.exists() is deprecated. Use async check_transcript_exists() from transcript_service.",
            DeprecationWarning,
            stacklevel=2
        )
        if not audio_filename:
            return False
        
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.file_exists(transcript_filename)
    
    def delete(self, audio_filename: str) -> bool:
        """
        Delete transcript file for an audio file.
        
        DEPRECATED: This method is deprecated and will be removed.
        
        Args:
            audio_filename: Name of the audio file
            
        Returns:
            True if successful, False otherwise
        """
        warnings.warn(
            "TranscriptRepository.delete() is deprecated.",
            DeprecationWarning,
            stacklevel=2
        )
        transcript_filename = self._get_transcript_filename(audio_filename)
        return self.delete_file(transcript_filename)

