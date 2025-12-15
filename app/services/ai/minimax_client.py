"""
MiniMax AI model client.
"""
import logging
from typing import Dict, List, Optional
from app.core.config import Settings
from app.services.ai.base_client import BaseAIClient
from app.services.ai.tunnel_manager import TunnelManager

logger = logging.getLogger(__name__)


class MinimaxClient(BaseAIClient):
    """Client for MiniMax AI model."""
    
    def __init__(self, tunnel_manager: TunnelManager, settings: Settings):
        """
        Initialize MiniMax client.
        
        Args:
            tunnel_manager: Tunnel manager instance
            settings: Application settings
        """
        super().__init__(tunnel_manager, settings, "minimax")
    
    def call(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> Optional[str]:
        """
        Call MiniMax model.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Temperature parameter (default: 0.7)
            max_tokens: Maximum tokens to generate (default: from settings)
            
        Returns:
            Generated content string or None if failed
        """
        if max_tokens is None:
            max_tokens = self.settings.max_tokens
        
        # Build MiniMax-specific payload
        # Note: MiniMax doesn't require model_id in the request
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        self._log_request(payload)
        
        try:
            response = self._make_request(payload, timeout=600)
            content = self._extract_content(response)
            
            if content:
                logger.info(f"MiniMax response length: {len(content)} characters")
            else:
                logger.warning("MiniMax returned empty content")
            
            return content
            
        except Exception as e:
            logger.error(f"MiniMax call failed: {str(e)}")
            raise

