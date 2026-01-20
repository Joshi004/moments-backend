"""
DEPRECATED: This module is deprecated. Use app.services.ai.prompt_tasks instead.

This file is kept for backward compatibility with any external code that might
still import from it. All prompt configuration and building logic has been moved
to the new Strategy + Builder pattern implementation in prompt_tasks/.
"""

# For backward compatibility, re-export from new location
from app.services.ai.prompt_tasks.config import get_response_format_param

__all__ = ["get_response_format_param"]

