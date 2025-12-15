"""
Base AI client with common functionality for all model clients.
"""
import time
import logging
import requests
from typing import Dict, List, Optional, Any
from app.core.config import Settings
from app.core.exceptions import AIModelException, SSHTunnelException
from app.services.ai.tunnel_manager import TunnelManager

logger = logging.getLogger(__name__)


class BaseAIClient:
    """Base class with common functionality for all AI clients."""
    
    def __init__(self, tunnel_manager: TunnelManager, settings: Settings, service_key: str):
        """
        Initialize base AI client.
        
        Args:
            tunnel_manager: Tunnel manager instance
            settings: Application settings
            service_key: Service key for model configuration (minimax, qwen, etc.)
        """
        self.tunnel_manager = tunnel_manager
        self.settings = settings
        self.service_key = service_key
        self.config = settings.get_model_config(service_key)
        self.model_url = settings.get_model_url(service_key)
    
    def _ensure_tunnel(self) -> None:
        """Ensure SSH tunnel is active."""
        if not self.tunnel_manager.ensure_tunnel(self.service_key):
            raise SSHTunnelException(
                service=self.service_key,
                error="Failed to establish SSH tunnel"
            )
    
    def _make_request(
        self,
        payload: Dict[str, Any],
        timeout: int = 600,
        max_retries: int = 1
    ) -> Dict[str, Any]:
        """
        Make HTTP request to AI model with retry logic.
        
        Args:
            payload: Request payload
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
            
        Returns:
            Response dictionary
            
        Raises:
            AIModelException: If request fails after all retries
        """
        self._ensure_tunnel()
        
        headers = {"Content-Type": "application/json"}
        
        for attempt in range(max_retries + 1):
            try:
                start_time = time.time()
                
                logger.info(f"Calling {self.service_key} model (attempt {attempt + 1}/{max_retries + 1})")
                
                response = requests.post(
                    self.model_url,
                    json=payload,
                    headers=headers,
                    timeout=timeout
                )
                
                duration = time.time() - start_time
                
                # Log response
                logger.info(
                    f"{self.service_key} API response: "
                    f"status={response.status_code}, duration={duration:.2f}s"
                )
                
                # Check for HTTP errors
                if response.status_code != 200:
                    error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.error(f"{self.service_key} API error: {error_msg}")
                    
                    if attempt < max_retries:
                        logger.info(f"Retrying in 2 seconds...")
                        time.sleep(2)
                        continue
                    
                    raise AIModelException(
                        model=self.service_key,
                        error=error_msg
                    )
                
                # Parse JSON response
                try:
                    response_data = response.json()
                    return response_data
                except ValueError as e:
                    error_msg = f"Invalid JSON response: {str(e)}"
                    logger.error(f"{self.service_key} response parse error: {error_msg}")
                    raise AIModelException(
                        model=self.service_key,
                        error=error_msg
                    )
                    
            except requests.exceptions.Timeout:
                error_msg = f"Request timeout after {timeout}s"
                logger.error(f"{self.service_key} timeout: {error_msg}")
                
                if attempt < max_retries:
                    logger.info(f"Retrying in 2 seconds...")
                    time.sleep(2)
                    continue
                
                raise AIModelException(
                    model=self.service_key,
                    error=error_msg
                )
                
            except requests.exceptions.ConnectionError as e:
                error_msg = f"Connection error: {str(e)}"
                logger.error(f"{self.service_key} connection error: {error_msg}")
                
                if attempt < max_retries:
                    logger.info(f"Retrying in 2 seconds...")
                    time.sleep(2)
                    continue
                
                raise AIModelException(
                    model=self.service_key,
                    error=error_msg
                )
                
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logger.error(f"{self.service_key} unexpected error: {error_msg}")
                raise AIModelException(
                    model=self.service_key,
                    error=error_msg
                )
        
        # Should never reach here
        raise AIModelException(
            model=self.service_key,
            error="Failed after all retries"
        )
    
    def _log_request(self, payload: Dict[str, Any], purpose: str = "call") -> None:
        """Log AI request for debugging."""
        message_count = len(payload.get('messages', []))
        logger.debug(
            f"{self.service_key} {purpose}: "
            f"{message_count} messages, "
            f"temperature={payload.get('temperature', 'N/A')}"
        )
    
    def _extract_content(self, response: Dict[str, Any]) -> Optional[str]:
        """
        Extract content from AI response.
        Common extraction logic for most models.
        
        Args:
            response: API response dictionary
            
        Returns:
            Extracted content string or None
        """
        try:
            choices = response.get('choices', [])
            if not choices:
                logger.warning("No choices in response")
                return None
            
            message = choices[0].get('message', {})
            content = message.get('content', '')
            
            return content
        except Exception as e:
            logger.error(f"Error extracting content from response: {str(e)}")
            return None

