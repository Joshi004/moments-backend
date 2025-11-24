import subprocess
import threading
import time
import json
import requests
import psutil
from pathlib import Path
from typing import Optional, Dict, List
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# In-memory job tracking dictionary for transcriptions
# Structure: {video_id: {"status": "processing"|"completed"|"failed", "started_at": timestamp, "audio_filename": str}}
_transcription_jobs: Dict[str, Dict] = {}
_transcription_lock = threading.Lock()

# SSH tunnel configuration
SSH_HOST = "naresh@85.234.64.44"
SSH_REMOTE_HOST = "worker-9"
SSH_LOCAL_PORT = 8006
SSH_REMOTE_PORT = 8006
TRANSCRIPTION_SERVICE_URL = f"http://localhost:{SSH_LOCAL_PORT}/transcribe"
AUDIO_BASE_URL = "http://localhost:8080/audios"


def get_transcript_directory() -> Path:
    """Get the path to the transcripts directory."""
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent
    transcript_dir = backend_dir / "static" / "transcripts"
    transcript_dir = transcript_dir.resolve()
    
    # Create directory if it doesn't exist
    transcript_dir.mkdir(parents=True, exist_ok=True)
    
    return transcript_dir


def get_transcript_path(audio_filename: str) -> Path:
    """Get the path for a transcript file based on audio filename."""
    transcript_dir = get_transcript_directory()
    # Replace audio extension with .json
    transcript_filename = Path(audio_filename).stem + ".json"
    return transcript_dir / transcript_filename


def check_transcript_exists(audio_filename: str) -> bool:
    """Check if transcript file exists for a given audio filename."""
    if not audio_filename:
        return False
    transcript_path = get_transcript_path(audio_filename)
    return transcript_path.exists() and transcript_path.is_file()


def load_transcript(audio_filename: str) -> Optional[dict]:
    """
    Load transcript data from JSON file.
    
    Args:
        audio_filename: Name of the audio file (e.g., "motivation.wav")
    
    Returns:
        Dictionary containing transcript data or None if file doesn't exist
    """
    if not audio_filename:
        return None
    
    try:
        transcript_path = get_transcript_path(audio_filename)
        
        if not transcript_path.exists() or not transcript_path.is_file():
            return None
        
        with open(transcript_path, 'r', encoding='utf-8') as f:
            transcript_data = json.load(f)
        
        logger.info(f"Successfully loaded transcript from {transcript_path}")
        return transcript_data
        
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing transcript JSON for {audio_filename}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error loading transcript for {audio_filename}: {str(e)}")
        return None


def save_transcript(audio_filename: str, transcription_data: dict) -> bool:
    """
    Save transcription data to a JSON file.
    
    Args:
        audio_filename: Name of the audio file
        transcription_data: Dictionary containing transcription response
    
    Returns:
        True if successful, False otherwise
    """
    try:
        transcript_path = get_transcript_path(audio_filename)
        
        # Ensure directory exists
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save as JSON
        with open(transcript_path, 'w', encoding='utf-8') as f:
            json.dump(transcription_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Successfully saved transcript to {transcript_path}")
        return True
    except Exception as e:
        logger.error(f"Error saving transcript for {audio_filename}: {str(e)}")
        return False


@contextmanager
def ssh_tunnel():
    """
    Context manager for SSH tunnel lifecycle.
    Creates tunnel on entry and closes it on exit.
    """
    tunnel_process = None
    try:
        # Create SSH tunnel
        tunnel_process = create_ssh_tunnel()
        if tunnel_process is None:
            raise Exception("Failed to create SSH tunnel")
        
        # Wait a moment for tunnel to establish
        time.sleep(1)
        
        yield tunnel_process
        
    finally:
        # Always close tunnel
        if tunnel_process is not None:
            close_ssh_tunnel(tunnel_process)


def create_ssh_tunnel() -> Optional[subprocess.Popen]:
    """
    Create SSH tunnel to remote transcription service.
    
    Returns:
        subprocess.Popen object if successful, None otherwise
    """
    try:
        cmd = [
            'ssh',
            '-fN',  # Background, no command execution
            '-o', 'ExitOnForwardFailure=yes',
            '-L', f'{SSH_LOCAL_PORT}:{SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}',
            SSH_HOST
        ]
        
        logger.info(f"Creating SSH tunnel: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait a moment to check if tunnel was created successfully
        time.sleep(0.5)
        
        # Check if process is still running (tunnel created successfully)
        if process.poll() is None:
            logger.info(f"SSH tunnel created successfully (PID: {process.pid})")
            return process
        else:
            # Process exited, check for errors
            stdout, stderr = process.communicate()
            logger.error(f"SSH tunnel failed: {stderr.decode() if stderr else 'Unknown error'}")
            return None
            
    except Exception as e:
        logger.error(f"Error creating SSH tunnel: {str(e)}")
        return None


def close_ssh_tunnel(tunnel_process: Optional[subprocess.Popen] = None) -> bool:
    """
    Close SSH tunnel by killing the SSH process.
    
    Args:
        tunnel_process: Optional subprocess.Popen object. If None, finds process by port.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if tunnel_process is not None:
            # Kill the specific process
            try:
                tunnel_process.terminate()
                tunnel_process.wait(timeout=5)
                logger.info(f"SSH tunnel closed (PID: {tunnel_process.pid})")
                return True
            except subprocess.TimeoutExpired:
                tunnel_process.kill()
                logger.info(f"SSH tunnel force-killed (PID: {tunnel_process.pid})")
                return True
        else:
            # Find and kill SSH processes using the tunnel port
            killed = False
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and 'ssh' in cmdline:
                        # Check if this is our tunnel command
                        cmd_str = ' '.join(cmdline)
                        if f':{SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}' in cmd_str and SSH_HOST in cmd_str:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except psutil.TimeoutExpired:
                                proc.kill()
                            logger.info(f"SSH tunnel closed (PID: {proc.info['pid']})")
                            killed = True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            return killed
            
    except Exception as e:
        logger.error(f"Error closing SSH tunnel: {str(e)}")
        return False


def call_transcription_service(audio_url: str) -> Optional[dict]:
    """
    Call the remote transcription service via tunnel.
    
    Args:
        audio_url: URL to the audio file (e.g., "http://localhost:8080/audios/motivation.wav")
    
    Returns:
        Dictionary with transcription response or None if failed
    """
    try:
        payload = {"audio_url": audio_url}
        
        logger.info(f"Calling transcription service with audio_url: {audio_url}")
        
        response = requests.post(
            TRANSCRIPTION_SERVICE_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=300  # 5 minute timeout for long audio files
        )
        
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"Transcription completed in {result.get('processing_time', 0):.2f} seconds")
        
        return result
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling transcription service: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in transcription service call: {str(e)}")
        return None


def start_transcription_job(video_id: str, audio_filename: str) -> bool:
    """
    Register a new transcription job.
    
    Args:
        video_id: ID of the video (filename stem)
        audio_filename: Name of the audio file
    
    Returns:
        True if job was registered, False if already processing
    """
    with _transcription_lock:
        if video_id in _transcription_jobs:
            return False
        
        _transcription_jobs[video_id] = {
            "status": "processing",
            "started_at": time.time(),
            "audio_filename": audio_filename
        }
        return True


def complete_transcription_job(video_id: str, success: bool = True) -> None:
    """
    Mark a transcription job as complete.
    
    Args:
        video_id: ID of the video
        success: True if processing succeeded, False otherwise
    """
    with _transcription_lock:
        if video_id in _transcription_jobs:
            _transcription_jobs[video_id]["status"] = "completed" if success else "failed"


def is_transcribing(video_id: str) -> bool:
    """
    Check if a video is currently being transcribed.
    
    Args:
        video_id: ID of the video
    
    Returns:
        True if transcribing, False otherwise
    """
    with _transcription_lock:
        if video_id not in _transcription_jobs:
            return False
        status = _transcription_jobs[video_id].get("status", "")
        return status == "processing"


def get_transcription_jobs() -> Dict[str, List[Dict]]:
    """
    Get all active transcription jobs.
    
    Returns:
        Dictionary with 'active_jobs' count and 'jobs' list
    """
    with _transcription_lock:
        # Clean up completed/failed jobs older than 30 seconds
        current_time = time.time()
        jobs_to_remove = []
        
        for video_id, job_info in _transcription_jobs.items():
            if job_info["status"] != "processing":
                # Remove completed/failed jobs after 30 seconds
                if current_time - job_info["started_at"] > 30:
                    jobs_to_remove.append(video_id)
        
        for video_id in jobs_to_remove:
            del _transcription_jobs[video_id]
        
        # Get active transcription jobs
        active_jobs = [
            {
                "video_id": video_id,
                "status": job_info["status"],
                "audio_filename": job_info.get("audio_filename", "")
            }
            for video_id, job_info in _transcription_jobs.items()
            if job_info["status"] == "processing"
        ]
        
        return {
            "active_jobs": len(active_jobs),
            "jobs": active_jobs
        }


def process_transcription_async(video_id: str, audio_filename: str) -> None:
    """
    Process transcription asynchronously in a background thread.
    
    Args:
        video_id: ID of the video (filename stem)
        audio_filename: Name of the audio file (e.g., "motivation.wav")
    """
    def transcribe():
        tunnel_process = None
        try:
            # Construct audio URL
            audio_url = f"{AUDIO_BASE_URL}/{audio_filename}"
            
            # Create SSH tunnel
            tunnel_process = create_ssh_tunnel()
            if tunnel_process is None:
                raise Exception("Failed to create SSH tunnel")
            
            # Wait for tunnel to establish
            time.sleep(2)
            
            # Call transcription service
            transcription_result = call_transcription_service(audio_url)
            
            if transcription_result is None:
                raise Exception("Transcription service returned no result")
            
            # Save transcript to file
            success = save_transcript(audio_filename, transcription_result)
            
            if not success:
                raise Exception("Failed to save transcript file")
            
            # Mark job as complete
            complete_transcription_job(video_id, success=True)
            
            logger.info(f"Transcription processing completed for {video_id}: success")
            
        except Exception as e:
            logger.error(f"Error in async transcription processing for {video_id}: {str(e)}")
            complete_transcription_job(video_id, success=False)
        finally:
            # Always close tunnel
            if tunnel_process is not None:
                close_ssh_tunnel(tunnel_process)
    
    # Start processing in background thread
    thread = threading.Thread(target=transcribe, daemon=True)
    thread.start()

