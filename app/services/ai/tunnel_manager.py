"""
Centralized SSH tunnel management for AI services.
Consolidates tunnel creation, monitoring, and cleanup logic.
"""
import subprocess
import threading
import time
import socket
import logging
import psutil
from typing import Optional, Dict
from contextlib import contextmanager
from app.core.config import Settings

logger = logging.getLogger(__name__)


class TunnelManager:
    """
    Manages SSH tunnels for AI model services.
    Thread-safe singleton for tunnel lifecycle management.
    """
    
    def __init__(self, settings: Settings):
        """
        Initialize tunnel manager.
        
        Args:
            settings: Application settings
        """
        self.settings = settings
        self._tunnels: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
    
    def _check_port_accessible(self, port: int, timeout: float = 2.0) -> bool:
        """Check if a port is accessible."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug(f"Port check error: {str(e)}")
            return False
    
    def _find_tunnel_process(self, service_key: str) -> Optional[psutil.Process]:
        """Find running SSH tunnel process for a service."""
        try:
            config = self.settings.get_model_config(service_key)
            ssh_host = config['ssh_host']
            ssh_remote_host = config['ssh_remote_host']
            ssh_remote_port = config['ssh_remote_port']
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and 'ssh' in cmdline:
                        cmd_str = ' '.join(cmdline)
                        if f':{ssh_remote_host}:{ssh_remote_port}' in cmd_str and ssh_host in cmd_str:
                            return proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return None
        except Exception as e:
            logger.debug(f"Error finding tunnel process: {str(e)}")
            return None
    
    def check_tunnel_exists(self, service_key: str) -> bool:
        """
        Check if tunnel exists and is working.
        
        Args:
            service_key: Service identifier (minimax, qwen, etc.)
            
        Returns:
            True if tunnel exists and port is accessible
        """
        try:
            config = self.settings.get_model_config(service_key)
            local_port = config['ssh_local_port']
            
            # Check if port is accessible
            if self._check_port_accessible(local_port):
                # Try to find the tunnel process
                proc = self._find_tunnel_process(service_key)
                if proc:
                    logger.info(f"Found existing SSH tunnel for {service_key} (PID: {proc.pid})")
                else:
                    logger.info(f"Port {local_port} is accessible (tunnel may be manually created)")
                return True
            
            return False
        except Exception as e:
            logger.debug(f"Error checking tunnel existence: {str(e)}")
            return False
    
    def create_tunnel(self, service_key: str) -> bool:
        """
        Create SSH tunnel for a service.
        
        Args:
            service_key: Service identifier (minimax, qwen, parakeet, etc.)
            
        Returns:
            True if tunnel created or already exists, False on failure
        """
        with self._lock:
            # Check if we already have this tunnel
            if service_key in self._tunnels:
                logger.info(f"Tunnel for {service_key} already managed")
                return True
            
            # Check if tunnel already exists (created externally)
            if self.check_tunnel_exists(service_key):
                logger.info(f"Reusing existing tunnel for {service_key}")
                # Create dummy process to track it
                self._tunnels[service_key] = subprocess.Popen(['echo'], stdout=subprocess.PIPE)
                return True
            
            # Create new tunnel
            try:
                config = self.settings.get_model_config(service_key)
                ssh_host = config['ssh_host']
                ssh_remote_host = config['ssh_remote_host']
                local_port = config['ssh_local_port']
                remote_port = config['ssh_remote_port']
                
                # Check if port is already in use
                if self._check_port_accessible(local_port):
                    logger.info(f"Port {local_port} already accessible, verifying...")
                    time.sleep(0.5)
                    if self._check_port_accessible(local_port):
                        logger.info(f"Port {local_port} verified working, reusing")
                        self._tunnels[service_key] = subprocess.Popen(['echo'], stdout=subprocess.PIPE)
                        return True
                
                # Build SSH command
                cmd = [
                    'ssh',
                    '-fN',  # Background, no command execution
                    '-o', 'ExitOnForwardFailure=yes',
                    '-o', 'StrictHostKeyChecking=no',
                    '-o', 'ConnectTimeout=10',
                    '-L', f'{local_port}:{ssh_remote_host}:{remote_port}',
                    ssh_host
                ]
                
                logger.info(f"Creating SSH tunnel for {service_key}: localhost:{local_port} -> {ssh_remote_host}:{remote_port}")
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                # Wait for process to complete (with -fN, it forks and exits)
                stdout, stderr = process.communicate(timeout=5)
                
                exit_code = process.returncode
                error_msg = stderr.decode().strip() if stderr else ''
                
                # Check for "Address already in use" - means tunnel exists
                if 'Address already in use' in error_msg or 'bind' in error_msg.lower():
                    logger.info(f"Port {local_port} already in use, checking existing tunnel...")
                    time.sleep(1.0)
                    if self._check_port_accessible(local_port):
                        logger.info("Existing tunnel verified, reusing")
                        self._tunnels[service_key] = subprocess.Popen(['echo'], stdout=subprocess.PIPE)
                        return True
                    else:
                        logger.warning("Port in use but not accessible, will attempt anyway")
                        self._tunnels[service_key] = subprocess.Popen(['echo'], stdout=subprocess.PIPE)
                        return True
                
                if exit_code != 0:
                    logger.error(f"SSH tunnel failed (exit code {exit_code}): {error_msg}")
                    return False
                
                logger.info(f"SSH tunnel command executed (exit code: {exit_code})")
                
                # Wait for tunnel to establish
                time.sleep(2.0)
                
                # Verify tunnel is working
                if self._check_port_accessible(local_port, timeout=3.0):
                    logger.info(f"SSH tunnel verified: port {local_port} is accessible")
                    self._tunnels[service_key] = process
                    return True
                else:
                    logger.error(f"SSH tunnel port {local_port} is not accessible")
                    return False
                    
            except Exception as e:
                logger.error(f"Error creating SSH tunnel for {service_key}: {str(e)}")
                return False
    
    def close_tunnel(self, service_key: str) -> bool:
        """
        Close SSH tunnel for a service.
        
        Args:
            service_key: Service identifier
            
        Returns:
            True if successful
        """
        with self._lock:
            if service_key in self._tunnels:
                process = self._tunnels[service_key]
                try:
                    if process.poll() is None:  # Process still running
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                    logger.info(f"Closed tunnel for {service_key}")
                except Exception as e:
                    logger.error(f"Error closing tunnel process: {str(e)}")
                
                del self._tunnels[service_key]
            
            # Also try to find and kill the actual SSH process
            try:
                proc = self._find_tunnel_process(service_key)
                if proc:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except psutil.TimeoutExpired:
                        proc.kill()
                    logger.info(f"Killed SSH process for {service_key} (PID: {proc.pid})")
            except Exception as e:
                logger.debug(f"Error killing SSH process: {str(e)}")
            
            return True
    
    def close_all_tunnels(self) -> None:
        """Close all managed tunnels."""
        with self._lock:
            for service_key in list(self._tunnels.keys()):
                self.close_tunnel(service_key)
    
    def ensure_tunnel(self, service_key: str) -> bool:
        """
        Ensure tunnel exists and is working, create if needed.
        
        Args:
            service_key: Service identifier
            
        Returns:
            True if tunnel is available
        """
        # Quick check if already managed
        with self._lock:
            if service_key in self._tunnels:
                config = self.settings.get_model_config(service_key)
                if self._check_port_accessible(config['ssh_local_port']):
                    return True
                else:
                    # Tunnel not working, remove and recreate
                    logger.warning(f"Tunnel for {service_key} not working, recreating...")
                    del self._tunnels[service_key]
        
        # Create or verify tunnel
        return self.create_tunnel(service_key)
    
    @contextmanager
    def tunnel_context(self, service_key: str):
        """
        Context manager for tunnel lifecycle.
        
        Usage:
            with tunnel_manager.tunnel_context("minimax"):
                # Make API calls
                pass
        
        Args:
            service_key: Service identifier
        """
        try:
            if not self.ensure_tunnel(service_key):
                raise Exception(f"Failed to create tunnel for {service_key}")
            
            logger.info(f"Tunnel context established for {service_key}")
            yield
            
        except Exception as e:
            logger.error(f"Tunnel context error for {service_key}: {str(e)}")
            raise
        finally:
            # Note: We don't auto-close tunnels as they may be reused
            # Explicit close_tunnel() should be called if needed
            pass
    
    def check_tunnel_health(self, service_key: str) -> bool:
        """
        Check if tunnel is healthy.
        
        Args:
            service_key: Service identifier
            
        Returns:
            True if tunnel is healthy and accessible
        """
        try:
            config = self.settings.get_model_config(service_key)
            local_port = config['ssh_local_port']
            
            # Check port accessibility
            if not self._check_port_accessible(local_port):
                return False
            
            # Check if process exists
            proc = self._find_tunnel_process(service_key)
            if proc:
                logger.debug(f"Tunnel for {service_key} is healthy (PID: {proc.pid})")
                return True
            else:
                # Port accessible but no process found - external tunnel
                logger.debug(f"Tunnel for {service_key} accessible (external)")
                return True
                
        except Exception as e:
            logger.debug(f"Tunnel health check failed: {str(e)}")
            return False

