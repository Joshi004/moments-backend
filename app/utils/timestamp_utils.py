"""
Timestamp utility functions for aligning video clips and transcripts.

This module provides common utilities to calculate precise start/end timestamps
based on word-level transcript boundaries, ensuring that video clips and transcript
extractions use identical time boundaries.
"""
from typing import List, Dict, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


def calculate_padded_boundaries(
    word_timestamps: List[Dict],
    moment_start: float,
    moment_end: float,
    padding: float,
    margin: float = 2.0
) -> Tuple[float, float]:
    """
    Calculate exact start/end timestamps aligned to word boundaries.
    
    This function ensures that video clips and transcript extractions use the exact
    same start and end times by aligning them to word boundaries from the transcript.
    
    Algorithm:
    - For start: Find the nearest word.start <= (moment_start - padding)
    - For end: Find the nearest word.end >= (moment_end + padding)
    - Allow a margin (default 2 seconds) to go slightly beyond if needed
    
    Args:
        word_timestamps: List of word timestamp dictionaries with 'word', 'start', 'end'
        moment_start: Original moment start time in seconds
        moment_end: Original moment end time in seconds
        padding: Seconds to pad before start and after end (single value for both sides)
        margin: Additional margin in seconds to allow going slightly beyond padding
                (default: 2.0)
    
    Returns:
        Tuple of (clip_start, clip_end) in seconds, aligned to word boundaries
    
    Example:
        >>> words = [
        ...     {'word': 'the', 'start': 28.5, 'end': 29.0},
        ...     {'word': 'key', 'start': 29.0, 'end': 29.3},
        ...     # ... more words ...
        ...     {'word': 'end', 'start': 149.8, 'end': 150.2}
        ... ]
        >>> calculate_padded_boundaries(words, 60.0, 120.0, 30.0)
        (28.5, 150.2)  # Aligned to nearest word boundaries
    """
    if not word_timestamps:
        logger.warning("No word timestamps provided, using moment times with padding")
        return (
            max(0, moment_start - padding),
            moment_end + padding
        )
    
    # Calculate target boundaries with padding
    target_start = max(0, moment_start - padding)
    target_end = moment_end + padding
    
    # Allow margin to go slightly beyond if no exact match
    search_start_min = max(0, target_start - margin)
    search_end_max = target_end + margin
    
    logger.debug(
        f"Finding word boundaries for moment [{moment_start:.2f}s - {moment_end:.2f}s] "
        f"with {padding:.1f}s padding"
    )
    logger.debug(f"Target start: {target_start:.2f}s, Target end: {target_end:.2f}s")
    
    # Find the best start boundary
    # We want the word that starts at or before target_start, but as close as possible
    clip_start = None
    best_start_word = None
    
    for word in word_timestamps:
        word_start = float(word['start'])
        
        # Word must start within our search range
        if word_start < search_start_min:
            continue
        if word_start > target_start:
            break  # Gone too far
        
        # This word starts before or at target, and is within range
        if clip_start is None or word_start > clip_start:
            clip_start = word_start
            best_start_word = word['word']
    
    # If no word found before target_start, use the first word in search range
    if clip_start is None:
        for word in word_timestamps:
            word_start = float(word['start'])
            if word_start >= search_start_min:
                clip_start = word_start
                best_start_word = word['word']
                logger.warning(
                    f"No word found before target start {target_start:.2f}s, "
                    f"using first available word at {clip_start:.2f}s"
                )
                break
    
    # Find the best end boundary
    # We want the word that ends at or after target_end, but as close as possible
    clip_end = None
    best_end_word = None
    
    for word in reversed(word_timestamps):
        word_end = float(word['end'])
        
        # Word must end within our search range
        if word_end > search_end_max:
            continue
        if word_end < target_end:
            break  # Gone too far backwards
        
        # This word ends after or at target, and is within range
        if clip_end is None or word_end < clip_end:
            clip_end = word_end
            best_end_word = word['word']
    
    # If no word found after target_end, use the last word in search range
    if clip_end is None:
        for word in reversed(word_timestamps):
            word_end = float(word['end'])
            if word_end <= search_end_max:
                clip_end = word_end
                best_end_word = word['word']
                logger.warning(
                    f"No word found after target end {target_end:.2f}s, "
                    f"using last available word at {clip_end:.2f}s"
                )
                break
    
    # Fallback to padded moment times if no words found
    if clip_start is None:
        clip_start = target_start
        logger.warning(f"No suitable start word found, using target start: {clip_start:.2f}s")
    
    if clip_end is None:
        clip_end = target_end
        logger.warning(f"No suitable end word found, using target end: {clip_end:.2f}s")
    
    # Validate result
    if clip_end <= clip_start:
        logger.error(
            f"Invalid clip boundaries: end ({clip_end:.2f}s) <= start ({clip_start:.2f}s). "
            f"Falling back to padded moment times."
        )
        return (target_start, target_end)
    
    logger.info(
        f"Calculated clip boundaries: [{clip_start:.2f}s - {clip_end:.2f}s] "
        f"(duration: {clip_end - clip_start:.2f}s)"
    )
    logger.debug(
        f"Start word: '{best_start_word}' at {clip_start:.2f}s, "
        f"End word: '{best_end_word}' at {clip_end:.2f}s"
    )
    logger.debug(
        f"Padding applied: start delta={moment_start - clip_start:.2f}s, "
        f"end delta={clip_end - moment_end:.2f}s"
    )
    
    return (clip_start, clip_end)


def extract_words_in_range(
    word_timestamps: List[Dict],
    start_time: float,
    end_time: float
) -> List[Dict]:
    """
    Extract word timestamps that fall within the specified time range.
    
    A word is included if it overlaps with the time range, i.e., if:
    word_end >= start_time AND word_start <= end_time
    
    Args:
        word_timestamps: List of word timestamp dictionaries with 'word', 'start', 'end'
        start_time: Start of the time range in seconds
        end_time: End of the time range in seconds
    
    Returns:
        List of word dictionaries that overlap with the specified range
    """
    if not word_timestamps:
        return []
    
    extracted = []
    for word_data in word_timestamps:
        if not isinstance(word_data, dict):
            continue
        
        if 'word' not in word_data or 'start' not in word_data or 'end' not in word_data:
            continue
        
        try:
            word_start = float(word_data['start'])
            word_end = float(word_data['end'])
        except (ValueError, TypeError):
            logger.warning(f"Invalid timestamp values in word data: {word_data}")
            continue
        
        # Include word if it overlaps with the range
        if word_end >= start_time and word_start <= end_time:
            extracted.append({
                'word': str(word_data['word']),
                'start': word_start,
                'end': word_end
            })
    
    logger.debug(
        f"Extracted {len(extracted)} words from range [{start_time:.2f}s - {end_time:.2f}s]"
    )
    
    return extracted

