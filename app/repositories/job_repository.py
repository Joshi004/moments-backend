"""
Repository for managing job tracking with Redis.
Provides distributed locking and automatic TTL-based cleanup.
"""
import json
import time
from typing import Optional, Dict, Any
from enum import Enum
import logging

from app.core.redis import get_redis_client
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Job status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class JobType(Enum):
    """Job type enumeration."""
    AUDIO_EXTRACTION = "audio_extraction"
    TRANSCRIPTION = "transcription"
    MOMENT_GENERATION = "moment_generation"
    MOMENT_REFINEMENT = "moment_refinement"
    CLIP_EXTRACTION = "clip_extraction"


class JobRepository:
    """
    Redis-based repository for distributed job tracking.
    Thread-safe and works across multiple backend instances.
    """
    
    def __init__(self):
        """Initialize job repository with Redis client."""
        self._redis = get_redis_client()
        self._settings = get_settings()
    
    def _get_job_key(self, job_type: JobType, video_id: str, moment_id: Optional[str] = None) -> str:
        """
        Generate Redis key for a job.
        
        Format: lock:{job_type}:{video_id}[:{moment_id}]
        """
        if moment_id:
            return f"lock:{job_type.value}:{video_id}:{moment_id}"
        return f"lock:{job_type.value}:{video_id}"
    
    def create(
        self, 
        job_type: JobType, 
        video_id: str,
        moment_id: Optional[str] = None,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new job with distributed locking.
        Uses Redis SET NX (set if not exists) for atomic lock acquisition.
        
        Args:
            job_type: Type of the job
            video_id: Video ID the job is associated with
            moment_id: Moment ID (optional, for refinement jobs)
            **kwargs: Additional job-specific data
            
        Returns:
            Created job dictionary if successful, None if job already exists
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        
        # Check if job already exists
        if self._redis.exists(job_key):
            logger.warning(f"Job already exists: {job_key}")
            return None
        
        job = {
            "job_type": job_type.value,
            "video_id": video_id,
            "moment_id": moment_id,
            "status": JobStatus.PROCESSING.value,
            "started_at": time.time(),
            "completed_at": None,
            "container_id": self._settings.container_id,
            "last_poll": time.time(),
            "error": None,
            **kwargs
        }
        
        # Atomic set with NX (only if not exists) and TTL
        job_json = json.dumps(job)
        success = self._redis.set(
            job_key, 
            job_json, 
            nx=True,  # Only set if key doesn't exist
            ex=self._settings.job_lock_ttl  # TTL in seconds
        )
        
        if success:
            logger.info(
                f"Created job: {job_key} on container {self._settings.container_id}"
            )
            return job
        else:
            logger.warning(f"Failed to create job (race condition): {job_key}")
            return None
    
    def get(
        self, 
        job_type: JobType, 
        video_id: str,
        moment_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get job status from Redis.
        
        Args:
            job_type: Type of the job
            video_id: Video ID
            moment_id: Moment ID (optional)
            
        Returns:
            Job dictionary or None if not found
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        
        job_json = self._redis.get(job_key)
        if job_json is None:
            return None
        
        try:
            job = json.loads(job_json)
            
            # Check for timeout (if still processing after TTL expires soon)
            if job["status"] == JobStatus.PROCESSING.value:
                elapsed = time.time() - job["started_at"]
                if elapsed > self._settings.job_lock_ttl - 60:  # Within 1 min of timeout
                    job["status"] = JobStatus.TIMEOUT.value
                    self.update_status(
                        job_type, video_id, JobStatus.TIMEOUT, 
                        moment_id=moment_id,
                        error="Job timed out after 15 minutes"
                    )
            
            return job
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode job JSON for {job_key}: {e}")
            return None
    
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
        Update job status in Redis.
        
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
        
        job_json = self._redis.get(job_key)
        if job_json is None:
            logger.warning(f"Cannot update non-existent job: {job_key}")
            return False
        
        try:
            job = json.loads(job_json)
            
            # Update status
            job["status"] = status.value
            
            # Set completion time for terminal states
            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMEOUT):
                job["completed_at"] = time.time()
            
            # Update error if provided
            if error:
                job["error"] = error
            
            # Update additional fields
            for key, value in kwargs.items():
                job[key] = value
            
            # Save back to Redis
            job_json = json.dumps(job)
            
            # Update TTL based on status
            if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMEOUT):
                # Short TTL for completed jobs so clients can still see result
                self._redis.set(job_key, job_json, ex=self._settings.job_result_ttl)
                logger.info(f"Updated job to {status.value} with {self._settings.job_result_ttl}s TTL: {job_key}")
            else:
                # Keep original TTL for processing jobs
                self._redis.set(job_key, job_json, ex=self._settings.job_lock_ttl)
                logger.info(f"Updated job to {status.value}: {job_key}")
            
            return True
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode job JSON for update {job_key}: {e}")
            return False
    
    def update_last_poll(
        self,
        job_type: JobType,
        video_id: str,
        moment_id: Optional[str] = None
    ) -> bool:
        """
        Update the last_poll timestamp to track client activity.
        
        Args:
            job_type: Type of the job
            video_id: Video ID
            moment_id: Moment ID (optional)
            
        Returns:
            True if updated, False if job not found
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        
        job_json = self._redis.get(job_key)
        if job_json is None:
            return False
        
        try:
            job = json.loads(job_json)
            job["last_poll"] = time.time()
            
            # Preserve existing TTL
            ttl = self._redis.ttl(job_key)
            if ttl > 0:
                self._redis.set(job_key, json.dumps(job), ex=ttl)
                return True
            else:
                # TTL expired or not set, use default
                self._redis.set(job_key, json.dumps(job), ex=self._settings.job_lock_ttl)
                return True
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to update last_poll for {job_key}: {e}")
            return False
    
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
        Delete a job from Redis.
        
        Args:
            job_type: Type of the job
            video_id: Video ID
            moment_id: Moment ID (optional)
            
        Returns:
            True if deleted, False if job not found
        """
        job_key = self._get_job_key(job_type, video_id, moment_id)
        result = self._redis.delete(job_key)
        
        if result > 0:
            logger.info(f"Deleted job: {job_key}")
            return True
        else:
            logger.warning(f"Job not found for deletion: {job_key}")
            return False
    
    def get_all_by_video(self, video_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all jobs for a video.
        
        Args:
            video_id: Video ID
            
        Returns:
            Dictionary of {job_key: job_data}
        """
        pattern = f"lock:*:{video_id}*"
        keys = self._redis.keys(pattern)
        
        result = {}
        for key in keys:
            job_json = self._redis.get(key)
            if job_json:
                try:
                    result[key] = json.loads(job_json)
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode job JSON for key {key}")
        
        return result
    
    def clear_completed(self, older_than_seconds: int = 3600) -> int:
        """
        Clear completed jobs older than specified seconds.
        Note: With Redis TTL, this is mostly unnecessary as jobs auto-expire.
        
        Args:
            older_than_seconds: Clear jobs completed longer than this many seconds ago
            
        Returns:
            Number of jobs cleared
        """
        # This is less critical with Redis since TTL handles cleanup
        # But can be used for manual cleanup if needed
        current_time = time.time()
        cleared_count = 0
        
        # Get all job keys
        keys = self._redis.keys("lock:*")
        
        for key in keys:
            job_json = self._redis.get(key)
            if job_json:
                try:
                    job = json.loads(job_json)
                    if job["status"] in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
                        completed_at = job.get("completed_at")
                        if completed_at and (current_time - completed_at) > older_than_seconds:
                            self._redis.delete(key)
                            cleared_count += 1
                except json.JSONDecodeError:
                    continue
        
        if cleared_count > 0:
            logger.info(f"Cleared {cleared_count} completed jobs older than {older_than_seconds}s")
        
        return cleared_count
