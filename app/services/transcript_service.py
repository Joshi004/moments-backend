import subprocess
import threading
import time
import json
import requests
import psutil
import socket
from pathlib import Path
from typing import Optional, Dict, List
from contextlib import contextmanager
import logging
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)
from app.utils.model_config import get_model_config, get_transcription_service_url
from app.repositories.job_repository import JobRepository, JobType, JobStatus

logger = logging.getLogger(__name__)

# Job repository for distributed job tracking (kept for backward compatibility with API endpoints)
job_repo = JobRepository()


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
    operation = "save_transcript"
    start_time = time.time()
    
    log_operation_start(
        logger="app.services.transcript_service",
        function="save_transcript",
        operation=operation,
        message="Saving transcript to file",
        context={
            "audio_filename": audio_filename,
            "has_segments": "segment_timestamps" in transcription_data if transcription_data else False,
            "has_words": "word_timestamps" in transcription_data if transcription_data else False,
            "request_id": get_request_id()
        }
    )
    
    try:
        transcript_path = get_transcript_path(audio_filename)
        
        # Ensure directory exists
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save as JSON
        with open(transcript_path, 'w', encoding='utf-8') as f:
            json.dump(transcription_data, f, indent=2, ensure_ascii=False)
        
        file_size = transcript_path.stat().st_size
        duration = time.time() - start_time
        
        log_operation_complete(
            logger="app.services.transcript_service",
            function="save_transcript",
            operation=operation,
            message="Successfully saved transcript",
            context={
                "audio_filename": audio_filename,
                "transcript_path": str(transcript_path),
                "file_size_bytes": file_size,
                "duration_seconds": duration
            }
        )
        return True
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="save_transcript",
            operation=operation,
            error=e,
            message="Error saving transcript",
            context={
                "audio_filename": audio_filename,
                "duration_seconds": duration
            }
        )
        return False


@contextmanager
def ssh_tunnel(service_key: str = "parakeet"):
    """
    Context manager for SSH tunnel lifecycle.
    Creates tunnel on entry and closes it on exit.
    
    Args:
        service_key: Service identifier ("parakeet")
    """
    tunnel_process = None
    try:
        # Create SSH tunnel
        logger.info(f"Creating SSH tunnel for service: {service_key}...")
        tunnel_process = create_ssh_tunnel(service_key)
        if tunnel_process is None:
            raise Exception("Failed to create SSH tunnel - process exited immediately")
        
        # Tunnel is already verified in create_ssh_tunnel by checking port accessibility
        logger.info("SSH tunnel established successfully")
        yield tunnel_process
        
    except Exception as e:
        logger.error(f"SSH tunnel error: {str(e)}")
        raise
    finally:
        # Always close tunnel
        if tunnel_process is not None:
            logger.info("Closing SSH tunnel...")
            close_ssh_tunnel(tunnel_process, service_key)


def create_ssh_tunnel(service_key: str = "parakeet") -> Optional[subprocess.Popen]:
    """
    Create FRESH SSH tunnel to remote transcription service.
    Always kills existing tunnels first to ensure clean state and correct config.
    
    Args:
        service_key: Service identifier ("parakeet")
    
    Returns:
        subprocess.Popen object if successful, None otherwise
    """
    operation = "ssh_tunnel_creation"
    start_time = time.time()
    
    try:
        config = get_model_config(service_key)
        ssh_host = config['ssh_host']
        ssh_remote_host = config['ssh_remote_host']
        ssh_local_port = config['ssh_local_port']
        ssh_remote_port = config['ssh_remote_port']
        
        log_operation_start(
            logger="app.services.transcript_service",
            function="create_ssh_tunnel",
            operation=operation,
            message="Creating FRESH SSH tunnel (killing any existing tunnel first)",
            context={
                "ssh_host": ssh_host,
                "local_port": ssh_local_port,
                "remote_host": ssh_remote_host,
                "remote_port": ssh_remote_port,
                "service_key": service_key,
                "request_id": get_request_id()
            }
        )
        
        # ALWAYS kill existing tunnel first to ensure fresh connection with correct config
        logger.info(f"Killing any existing tunnel on port {ssh_local_port}...")
        killed = close_ssh_tunnel(None, service_key)  # Pass None to kill by port/config
        if killed:
            logger.info(f"Killed existing tunnel - will create fresh tunnel")
            # Wait a moment for port to be released
            time.sleep(0.5)
        else:
            logger.info(f"No existing tunnel found - will create fresh tunnel")
        
        # Create fresh tunnel
        cmd = [
            'ssh',
            '-fN',  # Background, no command execution
            '-o', 'ExitOnForwardFailure=yes',
            '-o', 'StrictHostKeyChecking=no',  # Skip host key checking
            '-o', 'ConnectTimeout=10',  # Connection timeout
            '-L', f'{ssh_local_port}:{ssh_remote_host}:{ssh_remote_port}',
            ssh_host
        ]
        
        log_event(
            level="DEBUG",
            logger="app.services.transcript_service",
            function="create_ssh_tunnel",
            operation=operation,
            event="external_call_start",
            message="Executing SSH tunnel command",
            context={"command": " ".join(cmd)}
        )
        
        logger.info(f"Creating FRESH SSH tunnel: {' '.join(cmd)}")
        logger.info(f"Tunnel config: localhost:{ssh_local_port} -> {ssh_remote_host}:{ssh_remote_port} via {ssh_host}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for process to complete (with -fN, it forks and parent exits immediately)
        stdout, stderr = process.communicate(timeout=5)
        
        exit_code = process.returncode
        error_msg = stderr.decode().strip() if stderr else ''
        
        # With -fN, SSH forks into background and parent exits immediately
        # Exit code 0 usually means success, non-zero means failure
        if exit_code != 0:
            # Non-zero exit code indicates failure
            duration = time.time() - start_time
            log_event(
                level="ERROR",
                logger="app.services.transcript_service",
                function="create_ssh_tunnel",
                operation=operation,
                event="ssh_tunnel_error",
                message="SSH tunnel creation failed",
                context={
                    "error": error_msg[:500],
                    "exit_code": exit_code,
                    "duration_seconds": duration
                }
            )
            logger.error(f"SSH tunnel failed with exit code {exit_code}: {error_msg}")
            return None
        
        logger.info(f"SSH tunnel command executed successfully (exit code: {exit_code})")
        
        # Wait a moment for tunnel to establish
        time.sleep(2.0)
        
        # Find the actual SSH tunnel process that was created
        tunnel_pid = None
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline and 'ssh' in cmdline:
                    cmd_str = ' '.join(cmdline)
                    if f':{ssh_remote_host}:{ssh_remote_port}' in cmd_str and ssh_host in cmd_str:
                        tunnel_pid = proc.info['pid']
                        logger.info(f"Found SSH tunnel process (PID: {tunnel_pid})")
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        if not tunnel_pid:
            logger.warning("SSH tunnel process not found after creation. Tunnel may have failed silently.")
        
        # Verify the tunnel is actually working by checking if port is listening
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('localhost', ssh_local_port))
            sock.close()
            
            duration = time.time() - start_time
            
            if result == 0:
                log_event(
                    level="INFO",
                    logger="app.services.transcript_service",
                    function="create_ssh_tunnel",
                    operation=operation,
                    event="ssh_tunnel_complete",
                    message="✅ Fresh SSH tunnel created and verified successfully",
                    context={
                        "tunnel_pid": tunnel_pid,
                        "duration_seconds": duration
                    }
                )
                logger.info(f"✅ Fresh SSH tunnel verified: port {ssh_local_port} is listening and accessible")
                # Return the process object (will be used for tracking, actual cleanup uses PID)
                return process
            else:
                log_event(
                    level="ERROR",
                    logger="app.services.transcript_service",
                    function="create_ssh_tunnel",
                    operation=operation,
                    event="ssh_tunnel_error",
                    message="SSH tunnel port not accessible after creation",
                    context={
                        "duration_seconds": duration
                    }
                )
                logger.error(f"❌ SSH tunnel port {ssh_local_port} is not accessible (connection test failed with code {result})")
                if not tunnel_pid:
                    logger.error("SSH tunnel process is not running. Check SSH configuration and remote service status.")
                else:
                    logger.error("SSH tunnel process is running but port is not accessible. Check if remote service is running.")
                return None
        except Exception as e:
            duration = time.time() - start_time
            log_operation_error(
                logger="app.services.transcript_service",
                function="create_ssh_tunnel",
                operation=operation,
                error=e,
                message="Could not verify tunnel port",
                context={"duration_seconds": duration}
            )
            logger.error(f"Could not verify tunnel port: {str(e)}")
            return None
            
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="create_ssh_tunnel",
            operation=operation,
            error=e,
            message="Error creating SSH tunnel",
            context={"duration_seconds": duration}
        )
        logger.error(f"Error creating SSH tunnel: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def close_ssh_tunnel(tunnel_process: Optional[subprocess.Popen] = None, service_key: str = "parakeet") -> bool:
    """
    Close SSH tunnel by killing the SSH process.
    
    Args:
        tunnel_process: Optional subprocess.Popen object. If None, finds process by port.
        service_key: Service identifier ("parakeet")
    
    Returns:
        True if successful, False otherwise
    """
    try:
        config = get_model_config(service_key)
        ssh_host = config['ssh_host']
        ssh_remote_host = config['ssh_remote_host']
        ssh_remote_port = config['ssh_remote_port']
        
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
                        if f':{ssh_remote_host}:{ssh_remote_port}' in cmd_str and ssh_host in cmd_str:
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
    operation = "transcription_api_call"
    start_time = time.time()
    
    # Get service URL from config
    service_url = get_transcription_service_url()
    
    log_operation_start(
        logger="app.services.transcript_service",
        function="call_transcription_service",
        operation=operation,
        message="Calling transcription service",
        context={
            "audio_url": audio_url,
            "service_url": service_url,
            "request_id": get_request_id()
        }
    )
    
    try:
        payload = {"audio_url": audio_url}
        
        log_event(
            level="DEBUG",
            logger="app.services.transcript_service",
            function="call_transcription_service",
            operation=operation,
            event="external_call_start",
            message="Sending request to transcription service",
            context={"payload": payload}
        )
        
        response = requests.post(
            service_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=300  # 5 minute timeout for long audio files
        )
        
        duration = time.time() - start_time
        
        log_event(
            level="DEBUG",
            logger="app.services.transcript_service",
            function="call_transcription_service",
            operation=operation,
            event="external_call_complete",
            message="Received response from transcription service",
            context={
                "status_code": response.status_code,
                "response_size_bytes": len(response.content) if response.content else 0,
                "duration_seconds": duration
            }
        )
        
        response.raise_for_status()
        
        result = response.json()
        processing_time = result.get('processing_time', 0)
        
        log_operation_complete(
            logger="app.services.transcript_service",
            function="call_transcription_service",
            operation=operation,
            message="Transcription service call completed",
            context={
                "audio_url": audio_url,
                "processing_time_seconds": processing_time,
                "has_segments": "segment_timestamps" in result if result else False,
                "has_words": "word_timestamps" in result if result else False,
                "duration_seconds": duration
            }
        )
        
        return result
        
    except requests.exceptions.RequestException as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service",
            operation=operation,
            error=e,
            message="Error calling transcription service",
            context={
                "audio_url": audio_url,
                "duration_seconds": duration
            }
        )
        return None
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service",
            operation=operation,
            error=e,
            message="Unexpected error in transcription service call",
            context={
                "audio_url": audio_url,
                "duration_seconds": duration
            }
        )
        return None


# Job management functions now handled by JobRepository


def process_transcription_async(video_id: str, audio_signed_url: str) -> None:
    """
    Process transcription asynchronously in a background thread using GCS signed URL.
    
    Args:
        video_id: ID of the video (filename stem)
        audio_signed_url: GCS signed URL for the audio file
    """
    operation = "transcription_processing_async"
    
    # Extract audio filename from video_id for saving transcript
    audio_filename = f"{video_id}.wav"
    
    log_event(
        level="INFO",
        logger="app.services.transcript_service",
        function="process_transcription_async",
        operation=operation,
        event="operation_start",
        message="Starting async transcription processing thread with GCS audio",
        context={
            "video_id": video_id,
            "audio_url_type": "gcs_signed_url",
            "request_id": get_request_id()
        }
    )
    
    def transcribe():
        tunnel_process = None
        try:
            # Use the GCS signed URL directly
            audio_url = audio_signed_url
            
            log_event(
                level="DEBUG",
                logger="app.services.transcript_service",
                function="process_transcription_async",
                operation=operation,
                event="operation_start",
                message="Starting transcription in background thread",
                context={
                    "video_id": video_id,
                    "audio_url": audio_url
                }
            )
            
            # Create SSH tunnel (tunnel is already verified in create_ssh_tunnel)
            tunnel_process = create_ssh_tunnel("parakeet")
            if tunnel_process is None:
                raise Exception("Failed to create SSH tunnel")
            
            # Call transcription service
            transcription_result = call_transcription_service(audio_url)
            
            if transcription_result is None:
                raise Exception("Transcription service returned no result")
            
            # Save transcript to file
            success = save_transcript(audio_filename, transcription_result)
            
            if not success:
                raise Exception("Failed to save transcript file")
            
            # Mark pipeline stage as complete (import here to avoid circular dependency)
            from app.services.pipeline.status import mark_stage_completed
            from app.models.pipeline_schemas import PipelineStage
            mark_stage_completed(video_id, PipelineStage.TRANSCRIPTION)
            
            # Also update job repo for backward compatibility with API endpoints
            job_repo.update_status(
                JobType.TRANSCRIPTION,
                video_id,
                JobStatus.COMPLETED
            )
            
            log_event(
                level="INFO",
                logger="app.services.transcript_service",
                function="process_transcription_async",
                operation=operation,
                event="operation_complete",
                message="Transcription processing completed successfully",
                context={"video_id": video_id}
            )
            
        except Exception as e:
            log_operation_error(
                logger="app.services.transcript_service",
                function="process_transcription_async",
                operation=operation,
                error=e,
                message="Error in async transcription processing",
                context={"video_id": video_id}
            )
            # Mark pipeline stage as failed (import here to avoid circular dependency)
            from app.services.pipeline.status import mark_stage_failed
            from app.models.pipeline_schemas import PipelineStage
            mark_stage_failed(video_id, PipelineStage.TRANSCRIPTION, str(e))
            
            # Also update job repo for backward compatibility with API endpoints
            job_repo.update_status(
                JobType.TRANSCRIPTION,
                video_id,
                JobStatus.FAILED,
                error=str(e)
            )
        finally:
            # Always close tunnel
            if tunnel_process is not None:
                log_event(
                    level="DEBUG",
                    logger="app.services.transcript_service",
                    function="process_transcription_async",
                    operation=operation,
                    event="ssh_tunnel_start",
                    message="Closing SSH tunnel",
                    context={"video_id": video_id}
                )
                close_ssh_tunnel(tunnel_process, "parakeet")
    
    # Start processing in background thread
    thread = threading.Thread(target=transcribe, daemon=True)
    thread.start()
    
    log_event(
        level="DEBUG",
        logger="app.services.transcript_service",
        function="process_transcription_async",
        operation=operation,
        event="operation_complete",
        message="Background thread started",
        context={"video_id": video_id, "thread_name": thread.name}
    )

