"""
Base repository class with common file operations.
"""
import json
from pathlib import Path
from typing import Optional, Any, Dict
import logging

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base class for file-based repositories."""
    
    def __init__(self, base_path: Path):
        """
        Initialize repository.
        
        Args:
            base_path: Base directory for this repository's files
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
    
    def get_file_path(self, filename: str) -> Path:
        """Get the full path for a file in this repository."""
        return self.base_path / filename
    
    def file_exists(self, filename: str) -> bool:
        """Check if a file exists in this repository."""
        file_path = self.get_file_path(filename)
        return file_path.exists() and file_path.is_file()
    
    def read_json(self, filename: str) -> Optional[Any]:
        """
        Read JSON data from a file.
        
        Args:
            filename: Name of the file to read
            
        Returns:
            Parsed JSON data or None if file doesn't exist or parsing fails
        """
        if not filename:
            return None
        
        file_path = self.get_file_path(filename)
        
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON file {file_path}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {str(e)}")
            return None
    
    def write_json(self, filename: str, data: Any) -> bool:
        """
        Write JSON data to a file.
        
        Args:
            filename: Name of the file to write
            data: Data to serialize to JSON
            
        Returns:
            True if successful, False otherwise
        """
        file_path = self.get_file_path(filename)
        
        try:
            # Ensure directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write data to file
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            return True
        except Exception as e:
            logger.error(f"Error writing file {file_path}: {str(e)}")
            return False
    
    def delete_file(self, filename: str) -> bool:
        """
        Delete a file from this repository.
        
        Args:
            filename: Name of the file to delete
            
        Returns:
            True if successful, False otherwise
        """
        file_path = self.get_file_path(filename)
        
        try:
            if file_path.exists():
                file_path.unlink()
                return True
            return False
        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {str(e)}")
            return False

