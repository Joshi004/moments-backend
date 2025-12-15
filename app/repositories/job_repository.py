"""
Repository for managing job tracking (in-memory).
Replaces scattered job dictionaries across services.
"""
import threading
import time
from typing import Optional, Dict, Any
from enum import Enum


class JobStatus(Enum):
    """Job status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(Enum):
    """Job type enumeration."""
    AUDIO_EXTRACTION = "audio_extraction"
    TRANSCRIPTION = "transcription"
    MOMENT_GENERATION = "moment_generation"
    MOMENT_REFINEMENT = "moment_refinement"
    CLIP_EXTRACTION = "clip_extraction"


class JobRepository:
    """
    In-memory repository for job tracking.
    Thread-safe for concurrent access.
    """
    
    def __init__(self):
        """Initialize job repository with thread-safe storage."""
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
    
    def _get_job_key(self, job_type: JobType, video_id: str, moment_id: Optional[str] = None) -> str:
        """Generate a unique key for a job."""
        if moment_id:
            return f"{job_type.value}:{video_id}:{moment_id}"
        return f"{job_type.value}:{video_id}"
    
    def create(
        self, 
        job_type: JobType, 
        video_id: str,
        moment_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Create a new job.
        
        Args:
            job_type: Type of the job
            video_id: Video ID the job is associated with
            moment_id: Moment ID (optional, for refinement jobs)
            **kwargs: Additional job-specific data
            
        Returns:
            Created job dictionary
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        
        job = {
            "job_type": job_type.value,
            "video_id": video_id,
            "moment_id": moment_id,
            "status": JobStatus.PROCESSING.value,
            "started_at": time.time(),
            "completed_at": None,
            "error": None,
            **kwargs
        }
        
        with self._lock:
            self._jobs[job_key] = job
        
        return job
    
    def get(
        self, 
        job_type: JobType, 
        video_id: str,
        moment_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get job status.
        
        Args:
            job_type: Type of the job
            video_id: Video ID
            moment_id: Moment ID (optional)
            
        Returns:
            Job dictionary or None if not found
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        
        with self._lock:
            return self._jobs.get(job_key)
    
    def update_status(
        self,
        job_type: JobType,
        video_id: str,
        status: JobStatus,
        moment_id: Optional[str] = None,
        error: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        Update job status.
        
        Args:
            job_type: Type of the job
            video_id: Video ID
            status: New status
            moment_id: Moment ID (optional)
            error: Error message if failed
            **kwargs: Additional fields to update
            
        Returns:
            True if updated, False if job not found
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        
        with self._lock:
            if job_key not in self._jobs:
                return False
            
            self._jobs[job_key]["status"] = status.value
            
            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                self._jobs[job_key]["completed_at"] = time.time()
            
            if error:
                self._jobs[job_key]["error"] = error
            
            # Update additional fields
            for key, value in kwargs.items():
                self._jobs[job_key][key] = value
            
            return True
    
    def is_processing(
        self,
        job_type: JobType,
        video_id: str,
        moment_id: Optional[str] = None
    ) -> bool:
        """
        Check if a job is currently processing.
        
        Args:
            job_type: Type of the job
            video_id: Video ID
            moment_id: Moment ID (optional)
            
        Returns:
            True if job is processing, False otherwise
        """
        job = self.get(job_type, video_id, moment_id)
        return job is not None and job["status"] == JobStatus.PROCESSING.value
    
    def delete(
        self,
        job_type: JobType,
        video_id: str,
        moment_id: Optional[str] = None
    ) -> bool:
        """
        Delete a job.
        
        Args:
            job_type: Type of the job
            video_id: Video ID
            moment_id: Moment ID (optional)
            
        Returns:
            True if deleted, False if job not found
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        
        with self._lock:
            if job_key in self._jobs:
                del self._jobs[job_key]
                return True
            return False
    
    def get_all_by_video(self, video_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all jobs for a video.
        
        Args:
            video_id: Video ID
            
        Returns:
            Dictionary of {job_key: job_data}
        """
        with self._lock:
            return {
                key: job.copy()
                for key, job in self._jobs.items()
                if job["video_id"] == video_id
            }
    
    def clear_completed(self, older_than_seconds: int = 3600) -> int:
        """
        Clear completed jobs older than specified seconds.
        
        Args:
            older_than_seconds: Clear jobs completed longer than this many seconds ago
            
        Returns:
            Number of jobs cleared
        """
        current_time = time.time()
        cleared_count = 0
        
        with self._lock:
            keys_to_delete = []
            
            for key, job in self._jobs.items():
                if job["status"] in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
                    completed_at = job.get("completed_at")
                    if completed_at and (current_time - completed_at) > older_than_seconds:
                        keys_to_delete.append(key)
            
            for key in keys_to_delete:
                del self._jobs[key]
                cleared_count += 1
        
        return cleared_count

