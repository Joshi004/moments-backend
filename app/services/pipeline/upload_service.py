"""
SCP-based file uploader for audio files and video clips.
Uploads files to remote server via SCP.
"""
import asyncio
import logging
from pathlib import Path
from typing import List, Dict
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class SCPUploader:
    """SCP-based file uploader for audio and clips."""
    
    def __init__(self):
        """Initialize uploader with settings from config."""
        settings = get_settings()
        self.remote_host = settings.scp_remote_host
        self.audio_remote_path = settings.scp_audio_remote_path
        self.clips_remote_path = settings.scp_clips_remote_path
        self.timeout = settings.scp_connect_timeout
    
    async def upload_audio(self, local_path: Path) -> str:
        """
        Upload audio file to remote server for Parakeet access.
        
        Args:
            local_path: Path to local audio file (e.g., static/audios/motivation.wav)
        
        Returns:
            Remote path where file was uploaded
        
        Raises:
            Exception: If SCP upload fails
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Audio file not found: {local_path}")
        
        remote_name = local_path.name
        remote_dest = f"{self.remote_host}:{self.audio_remote_path}{remote_name}"
        
        cmd = [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", f"ConnectTimeout={self.timeout}",
            str(local_path),
            remote_dest
        ]
        
        logger.info(f"Uploading audio to remote: {remote_dest}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            logger.error(f"SCP audio upload failed: {error_msg}")
            raise Exception(f"SCP audio upload failed: {error_msg}")
        
        remote_path = f"{self.audio_remote_path}{remote_name}"
        logger.info(f"Successfully uploaded audio to: {remote_path}")
        return remote_path
    
    async def upload_clip(self, local_path: Path) -> str:
        """
        Upload video clip to remote server.
        
        Args:
            local_path: Path to local clip file
        
        Returns:
            Remote path where file was uploaded
        
        Raises:
            Exception: If SCP upload fails
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Clip file not found: {local_path}")
        
        remote_name = local_path.name
        remote_dest = f"{self.remote_host}:{self.clips_remote_path}{remote_name}"
        
        cmd = [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            "-o", f"ConnectTimeout={self.timeout}",
            str(local_path),
            remote_dest
        ]
        
        logger.info(f"Uploading clip to remote: {remote_dest}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            logger.error(f"SCP clip upload failed: {error_msg}")
            raise Exception(f"SCP clip upload failed: {error_msg}")
        
        remote_path = f"{self.clips_remote_path}{remote_name}"
        logger.info(f"Successfully uploaded clip to: {remote_path}")
        return remote_path
    
    async def upload_all_clips(self, video_id: str, moments: List[Dict]) -> List[Dict]:
        """
        Upload all clips for moments, return updated moments with remote paths.
        
        Args:
            video_id: Video identifier
            moments: List of moment dictionaries
        
        Returns:
            Updated moments list with remote_clip_path field
        """
        from app.services.video_clipping_service import get_clip_path
        
        for moment in moments:
            clip_path = get_clip_path(moment['id'], f"{video_id}.mp4")
            if clip_path.exists():
                try:
                    remote_path = await self.upload_clip(clip_path)
                    moment['remote_clip_path'] = remote_path
                    logger.info(f"Uploaded clip for moment {moment['id']}")
                except Exception as e:
                    logger.error(f"Failed to upload clip for moment {moment['id']}: {e}")
                    # Continue with other clips even if one fails
            else:
                logger.warning(f"Clip file not found for moment {moment['id']}: {clip_path}")
        
        return moments



