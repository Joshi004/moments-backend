"""
Base abstract class for prompt tasks.

This module defines the Strategy pattern interface for building prompts and
parsing responses for different AI tasks.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
import logging

from app.services.ai.prompt_tasks.sections import PromptSection
from app.services.ai.prompt_tasks.config import get_model_config

logger = logging.getLogger(__name__)


class BasePromptTask(ABC):
    """
    Abstract base class for all prompt tasks.
    
    This class implements the Strategy pattern, where different tasks
    (generation, refinement, validation) can provide their own implementations
    while sharing the common prompt-building logic.
    """
    
    @abstractmethod
    def get_output_type(self) -> str:
        """
        Get the expected output type for this task.
        
        Returns:
            Either 'array' or 'object'
        """
        pass
    
    @abstractmethod
    def get_sections(self) -> List[PromptSection]:
        """
        Get the ordered list of sections for this task's prompt.
        
        Returns:
            List of PromptSection enums in the order they should appear
        """
        pass
    
    @abstractmethod
    def render_section(self, section: PromptSection, context: Dict) -> Optional[str]:
        """
        Render a specific section with the provided context.
        
        Args:
            section: The PromptSection to render
            context: Dictionary containing all data needed to render sections
        
        Returns:
            Rendered section content, or None to skip this section
        """
        pass
    
    @abstractmethod
    def parse_response(self, response: Dict) -> Any:
        """
        Parse the AI model response and extract the relevant data.
        
        Args:
            response: Dictionary containing the AI model response
        
        Returns:
            Extracted data in task-specific format (e.g., list of moments, tuple of timestamps)
        
        Raises:
            ValueError: If response cannot be parsed or is invalid
        """
        pass
    
    def build_prompt(self, model_key: str, context: Dict) -> str:
        """
        Build the complete prompt for this task using the Strategy pattern.
        
        This method implements the Builder pattern, assembling the prompt from
        sections defined by the concrete task class and applying model-specific
        configurations.
        
        Args:
            model_key: Model identifier (e.g., 'qwen3_vl_fp8', 'minimax')
            context: Dictionary containing all data needed to render sections
        
        Returns:
            Complete assembled prompt string
        """
        # Get model configuration for this task's output type
        model_config = get_model_config(model_key, self.get_output_type())
        
        # Get the ordered sections for this task
        sections = self.get_sections()
        
        # Build list of rendered sections
        parts = []
        
        # Handle JSON header placement based on model priority
        header_at_top = model_config.header_priority == "top"
        
        for section in sections:
            if section == PromptSection.JSON_HEADER:
                if header_at_top:
                    # Add header at the very beginning
                    parts.append(model_config.json_header)
                # If not at top, we'll handle it in its natural position
                continue
            elif section == PromptSection.JSON_FOOTER:
                # Footer is always added if present
                if model_config.json_footer:
                    parts.append(model_config.json_footer)
                continue
            
            # Render the section using task-specific logic
            rendered = self.render_section(section, context)
            if rendered is not None:  # Allow empty string but skip None
                parts.append(rendered)
        
        # Assemble all parts with double newlines for readability
        complete_prompt = "\n\n".join(parts)
        
        logger.debug(
            f"Built prompt for {self.__class__.__name__} using model {model_key}, "
            f"length: {len(complete_prompt)} chars, sections: {len(parts)}"
        )
        
        return complete_prompt
    
    def _validate_context(self, context: Dict, required_keys: List[str]) -> None:
        """
        Validate that context contains all required keys.
        
        Args:
            context: Context dictionary to validate
            required_keys: List of required key names
        
        Raises:
            ValueError: If any required key is missing
        """
        missing_keys = [key for key in required_keys if key not in context]
        if missing_keys:
            raise ValueError(
                f"Missing required context keys for {self.__class__.__name__}: {missing_keys}"
            )
