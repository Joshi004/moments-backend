"""
Default prompts for AI moment generation and refinement.

This module serves as the single source of truth for default prompts used
in the VideoMoments application. Prompts are centralized here to ensure
consistency across different API endpoints and services.
"""

# Refinement prompt - used internally by the backend only
# Users should never see or edit this prompt
DEFAULT_REFINEMENT_PROMPT = """Before refining the timestamps, let's define what a moment is: A moment is a segment of a video (with its corresponding transcript) that represents something engaging, meaningful, or valuable to the viewer. A moment should be a complete, coherent thought or concept that makes sense on its own.

Now, analyze the word-level transcript and identify the precise start and end timestamps for this moment. The current timestamps may be slightly off. Find the exact point where this topic/segment naturally begins and ends.

Guidelines:
- Start the moment at the first word that introduces the topic or begins the engaging segment
- End the moment at the last word that concludes the thought or completes the concept
- Be precise with word boundaries
- Ensure the moment captures complete sentences or phrases
- The refined moment should represent a coherent, engaging segment that makes complete sense on its own"""
