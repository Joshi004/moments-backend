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
from app.utils.model_config import get_model_config, get_model_url
from app.utils.refine_moment_service import strip_think_tags

logger = logging.getLogger(__name__)

# In-memory job tracking dictionary for moment generation
# Structure: {video_id: {"status": "processing"|"completed"|"failed", "started_at": timestamp}}
_generation_jobs: Dict[str, Dict] = {}
_generation_lock = threading.Lock()

# Hardcoded max_tokens for all models
MAX_TOKENS = 2000


@contextmanager
def ssh_tunnel(model_key: str = "minimax"):
    """
    Context manager for SSH tunnel lifecycle.
    Creates tunnel on entry and closes it on exit.
    
    Args:
        model_key: Model identifier ("minimax" or "qwen")
    """
    tunnel_process = None
    try:
        # Create SSH tunnel
        logger.info(f"Creating SSH tunnel for model: {model_key}...")
        tunnel_process = create_ssh_tunnel(model_key)
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
            close_ssh_tunnel(tunnel_process, model_key)


def check_existing_tunnel(model_key: str = "minimax") -> bool:
    """
    Check if there's already an active SSH tunnel on the configured port.
    Less restrictive: if port is accessible, assume tunnel exists and allow reuse.
    
    Args:
        model_key: Model identifier ("minimax" or "qwen")
    
    Returns:
        True if tunnel exists and port is accessible, False otherwise
    """
    import socket
    try:
        config = get_model_config(model_key)
        ssh_host = config['ssh_host']
        ssh_remote_host = config['ssh_remote_host']
        ssh_local_port = config['ssh_local_port']
        ssh_remote_port = config['ssh_remote_port']
        
        # Check if port is accessible
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', ssh_local_port))
        sock.close()
        
        if result == 0:
            # Port is accessible - check if we can find our SSH tunnel process
            found_matching_tunnel = False
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and 'ssh' in cmdline:
                        cmd_str = ' '.join(cmdline)
                        # More flexible matching: check for port forwarding patterns
                        port_pattern = f'{ssh_local_port}:{ssh_remote_host}:{ssh_remote_port}'
                        remote_pattern = f':{ssh_remote_host}:{ssh_remote_port}'
                        
                        if (port_pattern in cmd_str or remote_pattern in cmd_str) and ssh_host in cmd_str:
                            logger.info(f"Found existing SSH tunnel (PID: {proc.info['pid']})")
                            found_matching_tunnel = True
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # If port is accessible, assume it's a working tunnel (less restrictive)
            # This allows reuse of tunnels created manually or by other processes
            if found_matching_tunnel:
                logger.info(f"Port {ssh_local_port} is accessible and matches our tunnel configuration")
            else:
                logger.info(f"Port {ssh_local_port} is accessible - assuming existing tunnel (may be created manually)")
            return True  # Port is accessible, allow reuse
        
        return False
    except Exception as e:
        logger.debug(f"Error checking existing tunnel: {str(e)}")
        return False


def create_ssh_tunnel(model_key: str = "minimax") -> Optional[subprocess.Popen]:
    """
    Create SSH tunnel to AI model service.
    
    Args:
        model_key: Model identifier ("minimax" or "qwen")
    
    Returns:
        subprocess.Popen object if successful, None otherwise
    """
    try:
        config = get_model_config(model_key)
        ssh_host = config['ssh_host']
        ssh_remote_host = config['ssh_remote_host']
        ssh_local_port = config['ssh_local_port']
        ssh_remote_port = config['ssh_remote_port']
        
        # First, check if there's already an active tunnel we can reuse
        if check_existing_tunnel(model_key):
            logger.info("Reusing existing SSH tunnel")
            # Return a dummy process - the tunnel is already running
            return subprocess.Popen(['echo'], stdout=subprocess.PIPE)
        
        # No existing tunnel found by check_existing_tunnel, check if port is in use by something else
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('localhost', ssh_local_port))
        sock.close()
        
        if result == 0:
            # Port is accessible - less restrictive: verify it works and reuse it
            logger.info(f"Port {ssh_local_port} is accessible. Verifying it's working and reusing existing connection...")
            
            # Try to verify the port is actually forwarding correctly
            # If port is accessible, assume it's a working tunnel and reuse it
            try:
                # Quick connectivity test
                test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_sock.settimeout(2)
                test_result = test_sock.connect_ex(('localhost', ssh_local_port))
                test_sock.close()
                
                if test_result == 0:
                    logger.info(f"Port {ssh_local_port} is accessible and appears to be working. Reusing existing tunnel.")
                    # Return a dummy process - the tunnel is already running
                    return subprocess.Popen(['echo'], stdout=subprocess.PIPE)
            except Exception as e:
                logger.debug(f"Port connectivity test failed: {str(e)}")
            
            # If we get here, port is accessible but we couldn't verify it
            # Still be lenient and try to reuse it
            logger.info(f"Port {ssh_local_port} is accessible. Attempting to reuse (less restrictive mode).")
            return subprocess.Popen(['echo'], stdout=subprocess.PIPE)
        
        cmd = [
            'ssh',
            '-fN',  # Background, no command execution
            '-o', 'ExitOnForwardFailure=yes',
            '-o', 'StrictHostKeyChecking=no',  # Skip host key checking
            '-o', 'ConnectTimeout=10',  # Connection timeout
            '-L', f'{ssh_local_port}:{ssh_remote_host}:{ssh_remote_port}',
            ssh_host
        ]
        
        logger.info(f"Creating SSH tunnel: {' '.join(cmd)}")
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
        
        # Check for "Address already in use" - this means tunnel already exists, which is OK
        if 'Address already in use' in error_msg or 'bind' in error_msg.lower():
            logger.info("Port already in use - checking if existing tunnel is working...")
            # Verify the existing tunnel works
            time.sleep(1.0)
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('localhost', ssh_local_port))
            sock.close()
            
            if result == 0:
                logger.info("Existing tunnel is working, reusing it")
                # Return a dummy process - the tunnel is already running
                return subprocess.Popen(['echo'], stdout=subprocess.PIPE)
            else:
                # Port was reported as in use but not accessible - this is unusual
                # Still be lenient and log a warning but don't fail
                logger.warning("Port reported as in use but not immediately accessible. Will attempt to use anyway.")
                # Return dummy process - let the actual API call determine if it works
                return subprocess.Popen(['echo'], stdout=subprocess.PIPE)
        
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
                    if f':{ssh_remote_host}:{ssh_remote_port}' in cmd_str and ssh_host in cmd_str:
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
            result = sock.connect_ex(('localhost', ssh_local_port))
            sock.close()
            
            if result == 0:
                logger.info(f"SSH tunnel verified: port {ssh_local_port} is listening and accessible")
                # Return the process object (even though it exited, we have its PID for cleanup)
                # The actual tunnel runs in a background SSH process
                return process
            else:
                logger.error(f"SSH tunnel port {ssh_local_port} is not accessible (connection test failed with code {result})")
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


def close_ssh_tunnel(tunnel_process: Optional[subprocess.Popen] = None, model_key: str = "minimax") -> bool:
    """
    Close SSH tunnel by killing the SSH process.
    
    Args:
        tunnel_process: Optional subprocess.Popen object. If None, finds process by port.
        model_key: Model identifier ("minimax" or "qwen")
    
    Returns:
        True if successful, False otherwise
    """
    try:
        config = get_model_config(model_key)
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


def test_ssh_connection(model_key: str = "minimax") -> bool:
    """
    Test basic SSH connectivity to the remote host.
    
    Args:
        model_key: Model identifier ("minimax" or "qwen")
    
    Returns:
        True if SSH connection works, False otherwise
    """
    try:
        config = get_model_config(model_key)
        ssh_host = config['ssh_host']
        
        logger.info(f"Testing SSH connection to {ssh_host}...")
        cmd = [
            'ssh',
            '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=no',
            ssh_host,
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


def check_remote_service(model_key: str = "minimax") -> bool:
    """
    Check if the AI model service is running on the remote server.
    
    Args:
        model_key: Model identifier ("minimax" or "qwen")
    
    Returns:
        True if service is accessible, False otherwise
    """
    try:
        config = get_model_config(model_key)
        ssh_host = config['ssh_host']
        ssh_remote_host = config['ssh_remote_host']
        ssh_remote_port = config['ssh_remote_port']
        
        logger.info(f"Checking if AI model service is running on {ssh_remote_host}:{ssh_remote_port}...")
        # Try to curl the service via SSH
        cmd = [
            'ssh',
            '-o', 'ConnectTimeout=5',
            '-o', 'StrictHostKeyChecking=no',
            ssh_host,
            f'curl -s -o /dev/null -w "%{{http_code}}" --connect-timeout 5 http://{ssh_remote_host}:{ssh_remote_port}/v1/chat/completions || echo "FAILED"'
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


def verify_tunnel_active(model_key: str = "minimax", max_retries: int = 3, retry_delay: float = 1.0) -> bool:
    """
    Verify that the SSH tunnel is active and the AI model endpoint is accessible.
    
    Args:
        model_key: Model identifier ("minimax" or "qwen")
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
    
    Returns:
        True if tunnel is active and endpoint is accessible, False otherwise
    """
    config = get_model_config(model_key)
    ssh_local_port = config['ssh_local_port']
    model_url = get_model_url(model_key)
    
    # First, verify port is listening (basic connectivity check)
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', ssh_local_port))
        sock.close()
        
        if result != 0:
            logger.error(f"Port {ssh_local_port} is not accessible")
            return False
        
        logger.info(f"Port {ssh_local_port} is accessible")
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
                model_url,
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


def call_ai_model(messages: List[Dict], model_key: str = "minimax", model_id: Optional[str] = None, temperature: float = 0.7) -> Optional[Dict]:
    """
    Call the AI model via tunnel.
    
    Args:
        messages: List of message dictionaries with 'role' and 'content'
        model_key: Model identifier ("minimax", "qwen", or "qwen3_omni")
        model_id: Optional model ID to use in the request (if None, uses config default)
        temperature: Temperature parameter for the model (default: 0.7)
    
    Returns:
        Dictionary with AI model response or None if failed
    """
    try:
        model_url = get_model_url(model_key)
        config = get_model_config(model_key)
        
        # Use provided model_id or get from config
        if model_id is None:
            model_id = config.get('model_id')
        
        payload = {
            "messages": messages,
            "max_tokens": MAX_TOKENS,
            "temperature": temperature
        }
        
        # Only add model_id if it's specified (Qwen needs it, MiniMax might not)
        if model_id:
            payload["model"] = model_id
        
        # Add top_p and top_k if they're specified in the model config
        if 'top_p' in config:
            payload["top_p"] = config['top_p']
        if 'top_k' in config:
            payload["top_k"] = config['top_k']
        
        logger.info(f"Calling AI model at {model_url} with {len(messages)} messages, model={model_id}, temperature={temperature}")
        logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(
            model_url,
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
        config = get_model_config(model_key)
        ssh_remote_host = config['ssh_remote_host']
        ssh_remote_port = config['ssh_remote_port']
        
        if 'Connection reset' in error_str or 'Connection aborted' in error_str:
            logger.error("Connection reset by peer - this usually means:")
            logger.error("  1. The tunnel is working but the remote service is not responding")
            logger.error(f"  2. The service on {ssh_remote_host}:{ssh_remote_port} might not be running")
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


def extract_model_name(response: Dict) -> str:
    """
    Extract model name from AI API response.
    
    Args:
        response: Dictionary containing AI model response
    
    Returns:
        Model name string, or "Unknown Model" if not available
    """
    if not isinstance(response, dict):
        return "Unknown Model"
    
    model_name = response.get('model', 'Unknown Model')
    if not model_name or model_name == '':
        return "Unknown Model"
    
    return str(model_name)


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


def build_prompt(
    user_prompt: str,
    segments: List[Dict],
    video_duration: float,
    min_moment_length: float,
    max_moment_length: float,
    min_moments: int,
    max_moments: int
) -> str:
    """
    Build the complete prompt for the AI model.
    
    Args:
        user_prompt: User-provided prompt (editable, visible in UI)
        segments: List of segment dictionaries with 'start' (float) and 'text' (string)
        video_duration: Total duration of the video in seconds
        min_moment_length: Minimum moment length in seconds
        max_moment_length: Maximum moment length in seconds
        min_moments: Minimum number of moments to generate
        max_moments: Maximum number of moments to generate
    
    Returns:
        Complete prompt string with all sections assembled
    """
    # Format segments as [timestamp] text (only start timestamp and text)
    segments_text = "\n".join([
        f"[{seg['start']:.2f}] {seg['text']}"
        for seg in segments
    ])
    
    # Input format explanation (backend-only, not editable)
    input_format_explanation = """INPUT FORMAT:
The transcript is provided as a series of segments. Each segment has:
- A timestamp (in seconds) indicating when that segment starts in the video
- The text content spoken during that segment

Format: [timestamp_in_seconds] text_content

Example:
[0.24] You know, rather than be scared by a jobless future
[2.56] I started to rethink it and I said
[5.12] I could really be excited by a jobless future"""
    
    # Response format specification (backend-only, not editable)
    response_format_specification = """OUTPUT FORMAT:
You must respond with a valid JSON array. Each object in the array represents one moment and must have exactly these fields:
- start_time: (float) The start time in seconds (must match or be close to a segment timestamp)
- end_time: (float) The end time in seconds (must be greater than start_time)
- title: (string) A clear, descriptive title for this moment (5-15 words)

Example response:
[
  {
    "start_time": 0.24,
    "end_time": 15.5,
    "title": "Introduction to jobless future concept"
  },
  {
    "start_time": 45.2,
    "end_time": 78.8,
    "title": "Discussion about human potential and creativity"
  }
]"""
    
    # Constraints section (backend-only, dynamically generated)
    constraints = f"""CONSTRAINTS:
- Video duration: {video_duration:.2f} seconds
- Moment length: Between {min_moment_length:.2f} and {max_moment_length:.2f} seconds
- Number of moments: Between {min_moments} and {max_moments}
- All moments must be non-overlapping
- All start_time values must be >= 0
- All end_time values must be <= {video_duration:.2f}
- Each moment's end_time must be > start_time"""
    
    # Assemble complete prompt
    complete_prompt = f"""{user_prompt}

{input_format_explanation}

Transcript segments:
{segments_text}

{response_format_specification}

{constraints}"""
    
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
        
        # Strip think tags before processing
        logger.info("parse_moments_response: About to call strip_think_tags")
        content = strip_think_tags(content)
        logger.info(f"parse_moments_response: After strip_think_tags, content length: {len(content)} chars")
        logger.debug(f"parse_moments_response: Content after strip_think_tags (first 300 chars): {content[:300]}")
        
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


def get_generation_status(video_id: str) -> Optional[Dict]:
    """
    Get generation status for a specific video.
    
    Args:
        video_id: ID of the video
    
    Returns:
        Dictionary with 'status' and 'started_at', or None if no job exists
    """
    with _generation_lock:
        if video_id not in _generation_jobs:
            return None
        
        job_info = _generation_jobs[video_id]
        return {
            "status": job_info.get("status", "unknown"),
            "started_at": job_info.get("started_at", 0)
        }


def process_moments_generation_async(
    video_id: str,
    video_filename: str,
    user_prompt: str,
    min_moment_length: float,
    max_moment_length: float,
    min_moments: int,
    max_moments: int,
    model: str = "minimax",
    temperature: float = 0.7
) -> None:
    """
    Process moment generation asynchronously in a background thread.
    
    Args:
        video_id: ID of the video (filename stem)
        video_filename: Name of the video file (e.g., "motivation.mp4")
        user_prompt: User-provided prompt (editable, visible in UI)
        min_moment_length: Minimum moment length in seconds
        max_moment_length: Maximum moment length in seconds
        min_moments: Minimum number of moments to generate
        max_moments: Maximum number of moments to generate
        model: Model identifier ("minimax", "qwen", or "qwen3_omni"), default: "minimax"
        temperature: Temperature parameter for the model, default: 0.7
    """
    def generate():
        tunnel_process = None
        try:
            # Import here to avoid circular imports
            from app.utils.transcript_service import load_transcript
            from app.utils.moments_service import save_moments, load_moments
            from app.utils.video_utils import get_video_by_filename
            import cv2
            
            logger.info(f"Starting moment generation for {video_id}")
            
            # Load transcript
            audio_filename = video_filename.rsplit('.', 1)[0] + ".wav"
            transcript_data = load_transcript(audio_filename)
            
            if transcript_data is None:
                raise Exception(f"Transcript not found for {audio_filename}")
            
            # Extract segments (only start timestamp and text)
            segments = extract_segment_data(transcript_data)
            
            if not segments:
                raise Exception("No segments found in transcript")
            
            # Get video duration
            video_file = get_video_by_filename(video_filename)
            if not video_file:
                raise Exception(f"Video file not found: {video_filename}")
            
            cap = cv2.VideoCapture(str(video_file))
            if not cap.isOpened():
                raise Exception(f"Could not open video file: {video_filename}")
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            video_duration = frame_count / fps if fps > 0 else 0.0
            cap.release()
            
            if video_duration <= 0:
                raise Exception(f"Could not determine video duration for {video_filename}")
            
            logger.info(f"Video duration: {video_duration:.2f} seconds, Segments: {len(segments)}")
            
            # Build complete prompt
            complete_prompt = build_prompt(
                user_prompt=user_prompt,
                segments=segments,
                video_duration=video_duration,
                min_moment_length=min_moment_length,
                max_moment_length=max_moment_length,
                min_moments=min_moments,
                max_moments=max_moments
            )
            
            logger.debug(f"Complete prompt length: {len(complete_prompt)} characters")
            
            # Get model configuration
            model_config = get_model_config(model)
            model_id = model_config.get('model_id')
            
            # Create SSH tunnel and call AI model
            with ssh_tunnel(model):
                # Prepare messages for AI model
                messages = [{
                    "role": "user",
                    "content": complete_prompt
                }]
                
                # Call AI model
                logger.info(f"Calling AI model ({model}) for moment generation...")
                ai_response = call_ai_model(messages, model_key=model, model_id=model_id, temperature=temperature)
                
                if ai_response is None:
                    raise Exception("AI model call failed or returned no response")
                
                # Extract model name from response
                model_name = extract_model_name(ai_response)
                logger.info(f"Using AI model: {model_name}")
                
                # Parse response to extract moments
                logger.info("Parsing AI model response...")
                moments = parse_moments_response(ai_response)
                
                if not moments:
                    raise Exception("No moments found in AI model response")
                
                logger.info(f"Parsed {len(moments)} moments from AI response")
                
                # Create generation_config dictionary with all parameters
                generation_config = {
                    "model": model,
                    "temperature": temperature,
                    "user_prompt": user_prompt,
                    "complete_prompt": complete_prompt,
                    "min_moment_length": min_moment_length,
                    "max_moment_length": max_moment_length,
                    "min_moments": min_moments,
                    "max_moments": max_moments,
                    "operation_type": "generation"
                }
                
                # Add model_name, prompt, and generation_config to each moment
                for moment in moments:
                    moment['model_name'] = model_name
                    moment['prompt'] = complete_prompt
                    moment['generation_config'] = generation_config
                
                # Validate moments against constraints
                validated_moments = []
                for i, moment in enumerate(moments):
                    # Check moment duration
                    duration = moment['end_time'] - moment['start_time']
                    if duration < min_moment_length or duration > max_moment_length:
                        logger.warning(f"Moment {i} duration {duration:.2f}s outside range [{min_moment_length:.2f}, {max_moment_length:.2f}], skipping")
                        continue
                    
                    # Check bounds
                    if moment['start_time'] < 0 or moment['end_time'] > video_duration:
                        logger.warning(f"Moment {i} outside video bounds, skipping")
                        continue
                    
                    # Check start < end
                    if moment['end_time'] <= moment['start_time']:
                        logger.warning(f"Moment {i} has invalid time range, skipping")
                        continue
                    
                    validated_moments.append(moment)
                
                # Check number of moments constraint
                if len(validated_moments) < min_moments:
                    logger.warning(f"Only {len(validated_moments)} valid moments found, but minimum is {min_moments}")
                elif len(validated_moments) > max_moments:
                    logger.warning(f"{len(validated_moments)} valid moments found, but maximum is {max_moments}. Truncating to {max_moments}")
                    validated_moments = validated_moments[:max_moments]
                
                # Check for overlaps
                validated_moments.sort(key=lambda x: x['start_time'])
                non_overlapping = []
                for moment in validated_moments:
                    overlaps = False
                    for existing in non_overlapping:
                        if (moment['start_time'] < existing['end_time'] and 
                            moment['end_time'] > existing['start_time']):
                            overlaps = True
                            logger.warning(f"Moment '{moment['title']}' overlaps with '{existing['title']}', skipping")
                            break
                    if not overlaps:
                        non_overlapping.append(moment)
                
                validated_moments = non_overlapping
                
                if not validated_moments:
                    raise Exception("No valid moments after validation")
                
                logger.info(f"Saving {len(validated_moments)} validated moments")
                
                # Save moments (replaces existing)
                success = save_moments(video_filename, validated_moments)
                
                if not success:
                    raise Exception("Failed to save moments to file")
                
                # Mark job as complete
                complete_generation_job(video_id, success=True)
                
                logger.info(f"Moment generation completed successfully for {video_id}: {len(validated_moments)} moments saved")
                
        except Exception as e:
            logger.error(f"Error in async moment generation for {video_id}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            complete_generation_job(video_id, success=False)
        finally:
            # Tunnel is closed by context manager
            pass
    
    # Start processing in background thread
    thread = threading.Thread(target=generate, daemon=True)
    thread.start()
