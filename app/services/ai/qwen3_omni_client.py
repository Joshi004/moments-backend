"""
Qwen3-Omni AI model client (text-only).
"""
import logging
from typing import Dict, List, Optional
from app.core.config import Settings
from app.services.ai.base_client import BaseAIClient
from app.services.ai.tunnel_manager import TunnelManager

logger = logging.getLogger(__name__)


class Qwen3OmniClient(BaseAIClient):
    """Client for Qwen3-Omni AI model (text-only)."""
    
    def __init__(self, tunnel_manager: TunnelManager, settings: Settings):
        """
        Initialize Qwen3-Omni client.
        
        Args:
            tunnel_manager: Tunnel manager instance
            settings: Application settings
        """
        super().__init__(tunnel_manager, settings, "qwen3_omni")
    
    def call(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None
    ) -> Optional[str]:
        """
        Call Qwen3-Omni model.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Temperature parameter (default: 0.7)
            max_tokens: Maximum tokens to generate (default: from settings)
            top_p: Top-p sampling parameter (default: from config)
            top_k: Top-k sampling parameter (default: from config)
            
        Returns:
            Generated content string or None if failed
        """
        if max_tokens is None:
            max_tokens = self.settings.max_tokens
        
        if top_p is None:
            top_p = self.config.get('top_p', 0.95)
        
        if top_k is None:
            top_k = self.config.get('top_k', 20)
        
        # Build Qwen3-Omni-specific payload
        # Note: Qwen3-Omni doesn't require model_id
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "top_k": top_k
        }
        
        self._log_request(payload)
        
        try:
            response = self._make_request(payload, timeout=600)
            content = self._extract_content(response)
            
            if content:
                logger.info(f"Qwen3-Omni response length: {len(content)} characters")
            else:
                logger.warning("Qwen3-Omni returned empty content")
            
            return content
            
        except Exception as e:
            logger.error(f"Qwen3-Omni call failed: {str(e)}")
            raise

