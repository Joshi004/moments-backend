"""
Qwen3-VL-FP8 AI model client (supports video/multimodal).
"""
import logging
from typing import Dict, List, Optional
from app.core.config import Settings
from app.services.ai.base_client import BaseAIClient
from app.services.ai.tunnel_manager import TunnelManager

logger = logging.getLogger(__name__)


class Qwen3VLClient(BaseAIClient):
    """Client for Qwen3-VL-FP8 AI model (supports video input)."""
    
    def __init__(self, tunnel_manager: TunnelManager, settings: Settings):
        """
        Initialize Qwen3-VL-FP8 client.
        
        Args:
            tunnel_manager: Tunnel manager instance
            settings: Application settings
        """
        super().__init__(tunnel_manager, settings, "qwen3_vl_fp8")
    
    def _add_video_to_messages(
        self,
        messages: List[Dict],
        video_url: str
    ) -> List[Dict]:
        """
        Transform messages to include video URL for multimodal input.
        
        Args:
            messages: Original messages
            video_url: URL to video clip
            
        Returns:
            Transformed messages with video content
        """
        transformed_messages = []
        
        for msg in messages:
            if msg.get('role') == 'user' and isinstance(msg.get('content'), str):
                # Convert text content to multimodal content array with video
                multimodal_content = [
                    {"type": "video_url", "video_url": {"url": video_url}},
                    {"type": "text", "text": msg['content']}
                ]
                transformed_messages.append({
                    "role": "user",
                    "content": multimodal_content
                })
            else:
                # Keep other messages as-is
                transformed_messages.append(msg)
        
        return transformed_messages
    
    def call(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        video_url: Optional[str] = None,
        max_tokens: Optional[int] = None
    ) -> Optional[str]:
        """
        Call Qwen3-VL-FP8 model with optional video input.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            temperature: Temperature parameter (default: 0.7)
            video_url: Optional URL to video clip for multimodal input
            max_tokens: Maximum tokens to generate (default: from settings)
            
        Returns:
            Generated content string or None if failed
        """
        if max_tokens is None:
            max_tokens = self.settings.max_tokens
        
        # Transform messages if video URL is provided
        if video_url:
            logger.info(f"Building multimodal request with video: {video_url}")
            messages = self._add_video_to_messages(messages, video_url)
        
        # Build Qwen3-VL-FP8-specific payload
        # Note: Qwen3-VL-FP8 doesn't require model_id
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        self._log_request(payload, purpose="multimodal call" if video_url else "call")
        
        try:
            response = self._make_request(payload, timeout=600)
            content = self._extract_content(response)
            
            if content:
                logger.info(f"Qwen3-VL-FP8 response length: {len(content)} characters")
            else:
                logger.warning("Qwen3-VL-FP8 returned empty content")
            
            return content
            
        except Exception as e:
            logger.error(f"Qwen3-VL-FP8 call failed: {str(e)}")
            raise

