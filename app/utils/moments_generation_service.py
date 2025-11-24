import subprocess
import threading
import time
import json
import requests
import psutil
import re
from typing import Optional, Dict, List
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

# In-memory job tracking dictionary for moment generation
# Structure: {video_id: {"status": "processing"|"completed"|"failed", "started_at": timestamp}}
_generation_jobs: Dict[str, Dict] = {}
_generation_lock = threading.Lock()

# SSH tunnel configuration for AI model
# Note: Using port 8007 to avoid conflicts with Cursor IDE which uses ports 7004, 7104, 8005, etc.
# The model service runs on worker-9, not on the login node
SSH_HOST = "naresh@85.234.64.44"
SSH_REMOTE_HOST = "worker-9"  # The compute node where the model service is running
SSH_LOCAL_PORT = 8007
SSH_REMOTE_PORT = 7104  # Remote server port (where your model is actually running)
AI_MODEL_URL = f"http://localhost:{SSH_LOCAL_PORT}/v1/chat/completions"


@contextmanager
def ssh_tunnel():
    """
    Context manager for SSH tunnel lifecycle.
    Creates tunnel on entry and closes it on exit.
    """
    tunnel_process = None
    try:
        # Create SSH tunnel
        logger.info("Creating SSH tunnel...")
        tunnel_process = create_ssh_tunnel()
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
            close_ssh_tunnel(tunnel_process)


def check_existing_tunnel() -> bool:
    """
    Check if there's already an active SSH tunnel on the configured port.
    
    Returns:
        True if tunnel exists and port is accessible, False otherwise
    """
    import socket
    try:
        # Check if port is accessible
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', SSH_LOCAL_PORT))
        sock.close()
        
        if result == 0:
            # Port is accessible, check if it's our SSH tunnel
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and 'ssh' in cmdline:
                        cmd_str = ' '.join(cmdline)
                        if f':{SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}' in cmd_str and SSH_HOST in cmd_str:
                            logger.info(f"Found existing SSH tunnel (PID: {proc.info['pid']})")
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            # Port is accessible but not our tunnel - might be another service
            logger.warning(f"Port {SSH_LOCAL_PORT} is in use but not by our SSH tunnel")
            return False
        return False
    except Exception as e:
        logger.debug(f"Error checking existing tunnel: {str(e)}")
        return False


def create_ssh_tunnel() -> Optional[subprocess.Popen]:
    """
    Create SSH tunnel to AI model service.
    
    Returns:
        subprocess.Popen object if successful, None otherwise
    """
    try:
        # First, check if there's already an active tunnel we can reuse
        if check_existing_tunnel():
            logger.info("Reusing existing SSH tunnel")
            # Return a dummy process - the tunnel is already running
            return subprocess.Popen(['echo'], stdout=subprocess.PIPE)
        
        # No existing tunnel, check if port is in use by something else
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', SSH_LOCAL_PORT))
        sock.close()
        
        if result == 0:
            # Port is in use - try to kill any SSH tunnels on this port
            logger.warning(f"Port {SSH_LOCAL_PORT} is in use, attempting to close existing tunnels...")
            close_ssh_tunnel(None)
            time.sleep(1.0)
            
            # Check again
            sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock2.settimeout(1)
            result2 = sock2.connect_ex(('localhost', SSH_LOCAL_PORT))
            sock2.close()
            
            if result2 == 0:
                logger.error(f"Port {SSH_LOCAL_PORT} is still in use after cleanup. Another service may be using it.")
                return None
        
        cmd = [
            'ssh',
            '-fN',  # Background, no command execution
            '-o', 'ExitOnForwardFailure=yes',
            '-o', 'StrictHostKeyChecking=no',  # Skip host key checking
            '-o', 'ConnectTimeout=10',  # Connection timeout
            '-L', f'{SSH_LOCAL_PORT}:{SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}',
            SSH_HOST
        ]
        
        logger.info(f"Creating SSH tunnel: {' '.join(cmd)}")
        logger.info(f"Tunnel config: localhost:{SSH_LOCAL_PORT} -> {SSH_REMOTE_HOST}:{SSH_REMOTE_PORT} via {SSH_HOST}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for process to complete (with -fN, it forks and parent exits immediately)
        stdout, stderr = process.communicate(timeout=5)
        
        exit_code = process.returncode
        error_msg = stderr.decode().strip() if stderr else ''
        
        # Check for "Address already in use" - this means tunnel already exists, which is OK
        if 'Address already in use' in error_msg or 'bind' in error_msg.lower():
            logger.info("Port already in use - checking if existing tunnel is working...")
            # Verify the existing tunnel works
            time.sleep(1.0)
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', SSH_LOCAL_PORT))
            sock.close()
            
            if result == 0:
                logger.info("Existing tunnel is working, reusing it")
                return process  # Return process even though it "failed" - tunnel exists
            else:
                logger.warning("Port in use but tunnel not accessible, attempting cleanup...")
                close_ssh_tunnel(None)
                time.sleep(1.0)
                # Try to verify again after cleanup
                sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock2.settimeout(2)
                result2 = sock2.connect_ex(('localhost', SSH_LOCAL_PORT))
                sock2.close()
                if result2 == 0:
                    logger.info("Tunnel accessible after cleanup")
                    return process
                else:
                    logger.error("Tunnel not accessible even after cleanup")
                    return None
        
        # With -fN, SSH forks into background and parent exits immediately
        # Exit code 0 usually means success, non-zero means failure
        if exit_code != 0:
            # Non-zero exit code indicates failure
            logger.error(f"SSH tunnel failed with exit code {exit_code}: {error_msg}")
            return None
        
        logger.info(f"SSH tunnel command executed (exit code: {exit_code})")
        
        # Wait a moment for tunnel to establish
        time.sleep(2.0)
        
        # Check if SSH tunnel process is actually running
        tunnel_running = False
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline', [])
                if cmdline and 'ssh' in cmdline:
                    cmd_str = ' '.join(cmdline)
                    if f':{SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}' in cmd_str and SSH_HOST in cmd_str:
                        logger.info(f"Found SSH tunnel process (PID: {proc.info['pid']})")
                        tunnel_running = True
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        if not tunnel_running:
            logger.warning("SSH tunnel process not found, but command succeeded. Tunnel may have failed silently.")
        
        # Verify the tunnel is actually working by checking if port is listening
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex(('localhost', SSH_LOCAL_PORT))
            sock.close()
            
            if result == 0:
                logger.info(f"SSH tunnel verified: port {SSH_LOCAL_PORT} is listening and accessible")
                # Return the process object (even though it exited, we have its PID for cleanup)
                # The actual tunnel runs in a background SSH process
                return process
            else:
                logger.error(f"SSH tunnel port {SSH_LOCAL_PORT} is not accessible (connection test failed with code {result})")
                if not tunnel_running:
                    logger.error("SSH tunnel process is not running. Check SSH configuration and remote service status.")
                else:
                    logger.error("SSH tunnel process is running but port is not accessible. Check if remote service is running.")
                return None
        except Exception as e:
            logger.error(f"Could not verify tunnel port: {str(e)}")
            return None
            
    except Exception as e:
        logger.error(f"Error creating SSH tunnel: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
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


def test_ssh_connection() -> bool:
    """
    Test basic SSH connectivity to the remote host.
    
    Returns:
        True if SSH connection works, False otherwise
    """
    try:
        logger.info(f"Testing SSH connection to {SSH_HOST}...")
        cmd = [
            'ssh',
            '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=no',
            SSH_HOST,
            'echo "SSH connection test successful"'
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10
        )
        
        if result.returncode == 0:
            logger.info("SSH connection test successful")
            return True
        else:
            logger.error(f"SSH connection test failed: {result.stderr.decode()}")
            return False
    except Exception as e:
        logger.error(f"SSH connection test error: {str(e)}")
        return False


def check_remote_service() -> bool:
    """
    Check if the AI model service is running on the remote server.
    
    Returns:
        True if service is accessible, False otherwise
    """
    try:
        logger.info(f"Checking if AI model service is running on {SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}...")
        # Try to curl the service via SSH
        cmd = [
            'ssh',
            '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=no',
            SSH_HOST,
            f'curl -s -o /dev/null -w "%{{http_code}}" --connect-timeout 5 http://{SSH_REMOTE_HOST}:{SSH_REMOTE_PORT}/v1/chat/completions || echo "FAILED"'
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr= subprocess.PIPE,
            timeout=10
        )
        
        output = result.stdout.decode().strip()
        logger.info(f"Remote service check result: {output}")
        
        # If we get any HTTP status code (even 400/500), service is running
        if output.isdigit() or 'FAILED' not in output:
            logger.info(f"AI model service appears to be running (response: {output})")
            return True
        else:
            logger.error(f"AI model service check failed: {output}")
            return False
    except Exception as e:
        logger.error(f"Remote service check error: {str(e)}")
        return False


def verify_tunnel_active(max_retries: int = 3, retry_delay: float = 1.0) -> bool:
    """
    Verify that the SSH tunnel is active and the AI model endpoint is accessible.
    
    Args:
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
    
    Returns:
        True if tunnel is active and endpoint is accessible, False otherwise
    """
    # First, verify port is listening (basic connectivity check)
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', SSH_LOCAL_PORT))
        sock.close()
        
        if result != 0:
            logger.error(f"Port {SSH_LOCAL_PORT} is not accessible")
            return False
        
        logger.info(f"Port {SSH_LOCAL_PORT} is accessible")
    except Exception as e:
        logger.error(f"Port connectivity check failed: {str(e)}")
        return False
    
    # Now try to make a request to the endpoint
    for attempt in range(max_retries):
        try:
            # Try a simple health check - make a minimal request to see if endpoint responds
            # We'll use a simple test message to verify connectivity
            test_payload = {
                "messages": [{
                    "role": "user",
                    "content": "test"
                }]
            }
            
            logger.info(f"Verifying tunnel connectivity to AI model (attempt {attempt + 1}/{max_retries})")
            
            response = requests.post(
                AI_MODEL_URL,
                json=test_payload,
                headers={"Content-Type": "application/json"},
                timeout=15  # Longer timeout for verification
            )
            
            # If we get any response (even an error), the tunnel is working
            # We just need to check if the endpoint is reachable
            logger.info(f"Tunnel verification successful (status: {response.status_code})")
            return True
            
        except requests.exceptions.ConnectionError as e:
            error_str = str(e)
            logger.warning(f"Tunnel verification attempt {attempt + 1} failed: Connection error - {error_str}")
            
            # Connection reset might mean the service is rejecting the request but tunnel works
            # Let's check if it's a reset vs complete connection failure
            if 'Connection reset' in error_str or 'Connection aborted' in error_str:
                logger.info("Connection reset detected - this might indicate tunnel is working but service rejected request")
                # If port is accessible, consider tunnel working even if request fails
                # The actual API call might work with proper payload
                if attempt == max_retries - 1:
                    logger.warning("Connection reset on all attempts, but port is accessible - assuming tunnel works")
                    return True  # Port is accessible, tunnel likely works
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logger.error("Tunnel verification failed after all retries")
                return False
        except requests.exceptions.Timeout:
            logger.warning(f"Tunnel verification attempt {attempt + 1} failed: Timeout")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logger.error("Tunnel verification failed: Timeout after all retries")
                return False
        except Exception as e:
            # Other errors might indicate the endpoint is reachable but returned an error
            # This is still a sign that the tunnel is working
            logger.info(f"Tunnel appears active (endpoint responded with error: {str(e)})")
            return True
    
    return False


def call_ai_model(messages: List[Dict]) -> Optional[Dict]:
    """
    Call the AI model via tunnel.
    
    Args:
        messages: List of message dictionaries with 'role' and 'content'
    
    Returns:
        Dictionary with AI model response or None if failed
    """
    try:
        payload = {
            "messages": messages
        }
        
        logger.info(f"Calling AI model at {AI_MODEL_URL} with {len(messages)} messages")
        logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(
            AI_MODEL_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=300,  # 5 minute timeout
            allow_redirects=True
        )
        
        logger.info(f"AI model response status: {response.status_code}")
        response.raise_for_status()
        
        # Log response text for debugging (first 1000 chars)
        response_text = response.text[:1000] if hasattr(response, 'text') else 'N/A'
        logger.debug(f"AI model response text preview: {response_text}")
        
        try:
            result = response.json()
            logger.info("AI model call completed successfully")
            logger.debug(f"AI model response keys: {list(result.keys()) if isinstance(result, dict) else 'Not a dict'}")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI model response as JSON: {str(e)}")
            logger.error(f"Response text: {response.text[:2000]}")
            raise
        
    except requests.exceptions.ConnectionError as e:
        error_str = str(e)
        logger.error(f"Connection error calling AI model: {error_str}")
        
        # Check if it's a connection reset - might indicate service issue
        if 'Connection reset' in error_str or 'Connection aborted' in error_str:
            logger.error("Connection reset by peer - this usually means:")
            logger.error("  1. The tunnel is working but the remote service is not responding")
            logger.error(f"  2. The service on {SSH_REMOTE_HOST}:{SSH_REMOTE_PORT} might not be running")
            logger.error("  3. The service might be rejecting the connection")
        
        return None
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout calling AI model: {str(e)}")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error calling AI model: {str(e)}")
        logger.error(f"Response status: {e.response.status_code if e.response else 'Unknown'}")
        logger.error(f"Response text: {e.response.text[:500] if e.response else 'No response'}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error calling AI model: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in AI model call: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def extract_segment_data(transcript: Dict) -> List[Dict]:
    """
    Extract segment timestamps from transcript, returning only start time and text.
    
    Args:
        transcript: Dictionary containing transcript data with 'segment_timestamps'
    
    Returns:
        List of dictionaries with 'start' (float) and 'text' (string)
    """
    if not transcript or 'segment_timestamps' not in transcript:
        logger.warning("Transcript does not contain segment_timestamps")
        return []
    
    segments = transcript['segment_timestamps']
    if not isinstance(segments, list):
        logger.warning("segment_timestamps is not a list")
        return []
    
    extracted = []
    for segment in segments:
        if isinstance(segment, dict) and 'start' in segment and 'text' in segment:
            extracted.append({
                'start': float(segment['start']),
                'text': str(segment['text'])
            })
    
    logger.info(f"Extracted {len(extracted)} segments from transcript")
    return extracted


def build_prompt(user_prompt: str, segments: List[Dict]) -> str:
    """
    Build the complete prompt for the AI model.
    
    Args:
        user_prompt: User-provided prompt (fully editable, includes response format requirements)
        segments: List of segment dictionaries with 'start' and 'text'
    
    Returns:
        Complete prompt string with segments inserted
    """
    # Format segments for inclusion in prompt
    segments_text = "\n".join([
        f"[{seg['start']:.2f}s] {seg['text']}"
        for seg in segments
    ])
    
    # Insert segments into the user prompt where {segments} placeholder is, or append if no placeholder
    if '{segments}' in user_prompt:
        complete_prompt = user_prompt.replace('{segments}', segments_text)
    else:
        # If no placeholder, append segments after the prompt
        complete_prompt = f"""{user_prompt}

Transcript segments with timestamps:
{segments_text}"""
    
    return complete_prompt


def parse_moments_response(response: Dict) -> List[Dict]:
    """
    Parse the AI model response to extract moments.
    
    Args:
        response: Dictionary containing AI model response
    
    Returns:
        List of moment dictionaries with start_time, end_time, and title
    """
    try:
        # Log the full response structure for debugging
        logger.debug(f"Full AI response structure: {json.dumps(response, indent=2)[:1000]}")
        
        # Extract content from response
        if 'choices' not in response or len(response['choices']) == 0:
            logger.error(f"No choices in response. Response keys: {list(response.keys())}")
            raise ValueError("No choices in response")
        
        content = response['choices'][0].get('message', {}).get('content', '')
        if not content:
            logger.error(f"No content in response. Choices structure: {response['choices'][0]}")
            raise ValueError("No content in response")
        
        logger.info(f"Extracted content from response (length: {len(content)} chars)")
        logger.debug(f"Content preview: {content[:500]}")
        
        # Try to extract JSON from content (handle markdown code blocks)
        json_str = content.strip()
        
        if not json_str:
            logger.error("Content is empty after stripping")
            raise ValueError("Empty content in response")
        
        # Remove markdown code blocks if present
        if json_str.startswith('```'):
            # Extract content between ```json and ```
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                logger.info("Extracted JSON from markdown code block")
        
        if not json_str:
            logger.error("JSON string is empty after processing")
            raise ValueError("Empty JSON string in response")
        
        logger.debug(f"Attempting to parse JSON: {json_str[:500]}")
        
        # Parse JSON
        moments = json.loads(json_str)
        
        # Validate it's a list
        if not isinstance(moments, list):
            raise ValueError("Response is not a list")
        
        # Validate each moment has required fields
        validated_moments = []
        for i, moment in enumerate(moments):
            if not isinstance(moment, dict):
                logger.warning(f"Moment {i} is not a dictionary, skipping")
                continue
            
            if 'start_time' not in moment or 'end_time' not in moment or 'title' not in moment:
                logger.warning(f"Moment {i} missing required fields, skipping")
                continue
            
            try:
                validated_moments.append({
                    'start_time': float(moment['start_time']),
                    'end_time': float(moment['end_time']),
                    'title': str(moment['title']).strip()
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"Moment {i} has invalid types: {e}, skipping")
                continue
        
        logger.info(f"Parsed {len(validated_moments)} valid moments from response")
        return validated_moments
        
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON from response: {str(e)}")
        logger.error(f"JSON string that failed to parse: {json_str[:1000] if 'json_str' in locals() else 'N/A'}")
        raise ValueError(f"Invalid JSON in response: {str(e)}")
    except Exception as e:
        logger.error(f"Error parsing moments response: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise ValueError(f"Error parsing response: {str(e)}")


def start_generation_job(video_id: str) -> bool:
    """
    Register a new moment generation job.
    
    Args:
        video_id: ID of the video (filename stem)
    
    Returns:
        True if job was registered, False if already processing
    """
    with _generation_lock:
        # Check if job exists and is currently processing
        if video_id in _generation_jobs:
            job_status = _generation_jobs[video_id].get("status", "")
            if job_status == "processing":
                return False
            # If job is completed or failed, we can start a new one
            # Remove the old job entry
            del _generation_jobs[video_id]
        
        _generation_jobs[video_id] = {
            "status": "processing",
            "started_at": time.time()
        }
        return True


def complete_generation_job(video_id: str, success: bool = True) -> None:
    """
    Mark a generation job as complete.
    
    Args:
        video_id: ID of the video
        success: True if processing succeeded, False otherwise
    """
    with _generation_lock:
        if video_id in _generation_jobs:
            _generation_jobs[video_id]["status"] = "completed" if success else "failed"


def is_generating(video_id: str) -> bool:
    """
    Check if a video is currently generating moments.
    
    Args:
        video_id: ID of the video
    
    Returns:
        True if generating, False otherwise
    """
    with _generation_lock:
        if video_id not in _generation_jobs:
            return False
        status = _generation_jobs[video_id].get("status", "")
        return status == "processing"


def get_generation_jobs() -> Dict[str, List[Dict]]:
    """
    Get all active generation jobs.
    
    Returns:
        Dictionary with 'active_jobs' count and 'jobs' list
    """
    with _generation_lock:
        # Clean up completed/failed jobs older than 30 seconds
        current_time = time.time()
        jobs_to_remove = []
        
        for video_id, job_info in _generation_jobs.items():
            if job_info["status"] != "processing":
                # Remove completed/failed jobs after 30 seconds
                if current_time - job_info["started_at"] > 30:
                    jobs_to_remove.append(video_id)
        
        for video_id in jobs_to_remove:
            del _generation_jobs[video_id]
        
        # Get active generation jobs
        active_jobs = [
            {
                "video_id": video_id,
                "status": job_info["status"]
            }
            for video_id, job_info in _generation_jobs.items()
            if job_info["status"] == "processing"
        ]
        
        return {
            "active_jobs": len(active_jobs),
            "jobs": active_jobs
        }

