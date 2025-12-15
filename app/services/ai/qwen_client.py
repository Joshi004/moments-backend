"""
Qwen AI model client.
"""
import logging
from typing import Dict, List, Optional
from app.core.config import Settings
from app.services.ai.base_client import BaseAIClient
from app.services.ai.tunnel_manager import TunnelManager

logger = logging.getLogger(__name__)


class QwenClient(BaseAIClient):
    """Client for Qwen AI model."""
    
    def __init__(self, tunnel_manager: TunnelManager, settings: Settings):
        """
        Initialize Qwen client.
        
        Args:
            tunnel_manager: Tunnel manager instance
            settings: Application settings
        """
        super().__init__(tunnel_manager, settings, "qwen")
    
    def call(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        model_id: Optional[str] = None,
        max_tokens: Optional[int] = None
    ) -> Optional[str]:
        """
        Call Qwen model.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Temperature parameter (default: 0.7)
            model_id: Model ID (default: from settings)
            max_tokens: Maximum tokens to generate (default: from settings)
            
        Returns:
            Generated content string or None if failed
        """
        if model_id is None:
            model_id = self.config.get('model_id', 'qwen3-vl-235b-thinking')
        
        if max_tokens is None:
            max_tokens = self.settings.max_tokens
        
        # Build Qwen-specific payload
        # Qwen REQUIRES model_id in the request
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        self._log_request(payload)
        
        try:
            response = self._make_request(payload, timeout=600)
            content = self._extract_content(response)
            
            if content:
                logger.info(f"Qwen response length: {len(content)} characters")
            else:
                logger.warning("Qwen returned empty content")
            
            return content
            
        except Exception as e:
            logger.error(f"Qwen call failed: {str(e)}")
            raise

