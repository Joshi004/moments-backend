import subprocess
import time
import json
import requests
import psutil
import socket
import warnings
from pathlib import Path
from typing import Optional, Dict, List
from contextlib import asynccontextmanager
import logging
from app.utils.logging_config import (
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    get_request_id
)
from app.utils.model_config import get_model_config, get_transcription_service_url
from app.database.session import get_session_factory
from app.repositories import transcript_db_repository, video_db_repository

logger = logging.getLogger(__name__)


def get_transcript_directory() -> Path:
    """
    Get the path to the transcripts directory.
    
    DEPRECATED: Transcripts are now stored in the database. This function is kept for backward
    compatibility but will be removed in a future version.
    """
    warnings.warn(
        "get_transcript_directory() is deprecated. Transcripts are now stored in the database.",
        DeprecationWarning,
        stacklevel=2
    )
    current_file = Path(__file__).resolve()
    backend_dir = current_file.parent.parent.parent
    transcript_dir = backend_dir / "static" / "transcripts"
    transcript_dir = transcript_dir.resolve()
    
    # Create directory if it doesn't exist
    transcript_dir.mkdir(parents=True, exist_ok=True)
    
    return transcript_dir


def get_transcript_path(audio_filename: str) -> Path:
    """
    Get the path for a transcript file based on audio filename.
    
    DEPRECATED: Transcripts are now stored in the database. This function is kept for backward
    compatibility but will be removed in a future version.
    """
    warnings.warn(
        "get_transcript_path() is deprecated. Transcripts are now stored in the database.",
        DeprecationWarning,
        stacklevel=2
    )
    transcript_dir = get_transcript_directory()
    # Replace audio extension with .json
    transcript_filename = Path(audio_filename).stem + ".json"
    return transcript_dir / transcript_filename


async def check_transcript_exists(audio_filename: str) -> bool:
    """
    Check if transcript exists in the database for a given audio filename.
    
    Args:
        audio_filename: Name of the audio file (e.g., "motivation.wav")
    
    Returns:
        True if transcript exists in database, False otherwise
    """
    if not audio_filename:
        return False
    
    # Extract identifier from audio filename
    identifier = Path(audio_filename).stem
    
    # Query database
    session_factory = get_session_factory()
    async with session_factory() as session:
        exists = await transcript_db_repository.exists_by_identifier(session, identifier)
        return exists


async def load_transcript(audio_filename: str) -> Optional[dict]:
    """
    Load transcript data from the database.
    
    Args:
        audio_filename: Name of the audio file (e.g., "motivation.wav")
    
    Returns:
        Dictionary containing transcript data or None if not found
    """
    if not audio_filename:
        return None
    
    try:
        # Extract identifier from audio filename
        identifier = Path(audio_filename).stem
        
        # Query database
        session_factory = get_session_factory()
        async with session_factory() as session:
            transcript = await transcript_db_repository.get_by_video_identifier(session, identifier)
            
            if transcript is None:
                return None
            
            # Map database columns back to expected JSON format
            # Note: DB uses "full_text", JSON expects "transcription"
            transcript_data = {
                "transcription": transcript.full_text,
                "word_timestamps": transcript.word_timestamps,
                "segment_timestamps": transcript.segment_timestamps,
                "processing_time": transcript.processing_time_seconds,
            }
            
            logger.info(f"Successfully loaded transcript from database for {identifier}")
            return transcript_data
        
    except Exception as e:
        logger.error(f"Error loading transcript for {audio_filename}: {str(e)}")
        return None


async def save_transcript(audio_filename: str, transcription_data: dict) -> bool:
    """
    Save transcription data to the database (and also to JSON file as backup).
    
    This function performs a dual-write: saves to both database and JSON file
    for safety during the transition period.
    
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
        message="Saving transcript to database (with JSON backup)",
        context={
            "audio_filename": audio_filename,
            "has_segments": "segment_timestamps" in transcription_data if transcription_data else False,
            "has_words": "word_timestamps" in transcription_data if transcription_data else False,
            "request_id": get_request_id()
        }
    )
    
    try:
        # Extract identifier from audio filename
        identifier = Path(audio_filename).stem
        
        # Extract data from transcription response
        # Note: JSON uses "transcription" key, DB expects "full_text"
        full_text = transcription_data.get("transcription", "")
        word_timestamps = transcription_data.get("word_timestamps", [])
        segment_timestamps = transcription_data.get("segment_timestamps", [])
        processing_time = transcription_data.get("processing_time")
        
        # Compute counts
        number_of_words = len(word_timestamps) if word_timestamps else 0
        number_of_segments = len(segment_timestamps) if segment_timestamps else 0
        
        # Save to database
        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                # Look up video by identifier
                video = await video_db_repository.get_by_identifier(session, identifier)
                if not video:
                    logger.error(f"Video '{identifier}' not found in database")
                    return False
                
                # Check if transcript already exists
                exists = await transcript_db_repository.exists_for_video(session, video.id)
                if exists:
                    logger.warning(f"Transcript already exists for video '{identifier}' - skipping database insert")
                else:
                    # Insert transcript
                    transcript = await transcript_db_repository.create(
                        session=session,
                        video_id=video.id,
                        full_text=full_text,
                        word_timestamps=word_timestamps,
                        segment_timestamps=segment_timestamps,
                        language="en",
                        number_of_words=number_of_words,
                        number_of_segments=number_of_segments,
                        transcription_service="parakeet",
                        processing_time_seconds=processing_time,
                    )
                    await session.commit()
                    logger.info(f"Saved transcript to database (id={transcript.id})")
                
            except Exception as e:
                await session.rollback()
                logger.error(f"Database error saving transcript: {str(e)}")
                # Continue to save JSON file as backup even if DB fails
        
        # Also save to JSON file (dual-write for safety)
        transcript_path = get_transcript_path(audio_filename)
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(transcript_path, 'w', encoding='utf-8') as f:
            json.dump(transcription_data, f, indent=2, ensure_ascii=False)
        
        file_size = transcript_path.stat().st_size
        duration = time.time() - start_time
        
        log_operation_complete(
            logger="app.services.transcript_service",
            function="save_transcript",
            operation=operation,
            message="Successfully saved transcript to database and JSON file",
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


@asynccontextmanager
async def ssh_tunnel(service_key: str = "parakeet"):
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
        tunnel_process = await create_ssh_tunnel(service_key)
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
            await close_ssh_tunnel(tunnel_process, service_key)


async def create_ssh_tunnel(service_key: str = "parakeet") -> Optional[subprocess.Popen]:
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
        config = await get_model_config(service_key)
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
        killed = await close_ssh_tunnel(None, service_key)  # Pass None to kill by port/config
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


async def close_ssh_tunnel(tunnel_process: Optional[subprocess.Popen] = None, service_key: str = "parakeet") -> bool:
    """
    Close SSH tunnel by killing the SSH process.
    
    Args:
        tunnel_process: Optional subprocess.Popen object. If None, finds process by port.
        service_key: Service identifier ("parakeet")
    
    Returns:
        True if successful, False otherwise
    """
    try:
        config = await get_model_config(service_key)
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


# Job management functions now handled by JobRepository


async def call_transcription_service_async(audio_url: str) -> Optional[dict]:
    """
    Call the remote transcription service via tunnel asynchronously using httpx.
    
    This is the async version of call_transcription_service() for use in async contexts.
    The SSH tunnel must already be established before calling this function.
    
    Args:
        audio_url: URL to the audio file (e.g., GCS signed URL)
    
    Returns:
        Dictionary with transcription response or None if failed
    """
    import httpx
    
    operation = "transcription_api_call_async"
    start_time = time.time()
    
    # Get service URL from config
    service_url = await get_transcription_service_url()
    
    log_operation_start(
        logger="app.services.transcript_service",
        function="call_transcription_service_async",
        operation=operation,
        message="Calling transcription service (async)",
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
            function="call_transcription_service_async",
            operation=operation,
            event="external_call_start",
            message="Sending request to transcription service",
            context={"payload": payload}
        )
        
        # Use httpx AsyncClient for async HTTP requests
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                service_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
        
        duration = time.time() - start_time
        
        log_event(
            level="DEBUG",
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
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
            function="call_transcription_service_async",
            operation=operation,
            message="Transcription service call completed (async)",
            context={
                "audio_url": audio_url,
                "processing_time_seconds": processing_time,
                "has_segments": "segment_timestamps" in result if result else False,
                "has_words": "word_timestamps" in result if result else False,
                "duration_seconds": duration
            }
        )
        
        return result
        
    except httpx.HTTPStatusError as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="HTTP error calling transcription service (async)",
            context={
                "audio_url": audio_url,
                "status_code": e.response.status_code if e.response else None,
                "response_preview": e.response.text[:500] if e.response and hasattr(e.response, 'text') else None,
                "duration_seconds": duration
            }
        )
        return None
    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="Timeout calling transcription service (async)",
            context={
                "audio_url": audio_url,
                "timeout_seconds": 300,
                "duration_seconds": duration
            }
        )
        return None
    except httpx.ConnectError as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="Connection error calling transcription service (async)",
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
            function="call_transcription_service_async",
            operation=operation,
            error=e,
            message="Unexpected error in transcription service call (async)",
            context={
                "audio_url": audio_url,
                "duration_seconds": duration
            }
        )
        return None


async def process_transcription(
    video_id: str,
    audio_signed_url: str
) -> dict:
    """
    Process transcription as an async coroutine using GCS signed URL.
    
    This is the recommended async version that integrates with the pipeline orchestrator.
    Unlike the deprecated thread-based version, this function:
    - Returns result directly (no Redis polling needed)
    - Raises exceptions on errors (native exception handling)
    - Can be used with asyncio.wait_for() for timeout handling
    
    Args:
        video_id: ID of the video (filename stem)
        audio_signed_url: GCS signed URL for the audio file
    
    Returns:
        Dictionary with transcription result
    
    Raises:
        Exception: If transcription fails with an error that should stop processing
    """
    operation = "transcription_processing"
    start_time = time.time()
    
    try:
        # Extract audio filename from video_id for saving transcript
        audio_filename = f"{video_id}.wav"
        
        log_operation_start(
            logger="app.services.transcript_service",
            function="process_transcription",
            operation=operation,
            message="Starting transcription processing (async)",
            context={
                "video_id": video_id,
                "audio_url_type": "gcs_signed_url",
                "request_id": get_request_id()
            }
        )
        
        # Create SSH tunnel
        async with ssh_tunnel("parakeet"):
            # Call transcription service asynchronously
            transcription_result = await call_transcription_service_async(audio_signed_url)
            
            if transcription_result is None:
                raise Exception("Transcription service returned no result")
            
            # Save transcript to database (and file as backup)
            success = await save_transcript(audio_filename, transcription_result)
            
            if not success:
                raise Exception("Failed to save transcript")
            
            duration = time.time() - start_time
            log_operation_complete(
                logger="app.services.transcript_service",
                function="process_transcription",
                operation=operation,
                message="Transcription processing completed successfully (async)",
                context={
                    "video_id": video_id,
                    "duration_seconds": duration
                }
            )
            
            return transcription_result
    
    except Exception as e:
        duration = time.time() - start_time
        log_operation_error(
            logger="app.services.transcript_service",
            function="process_transcription",
            operation=operation,
            error=e,
            message="Error in transcription processing (async)",
            context={
                "video_id": video_id,
                "duration_seconds": duration
            }
        )
        raise
