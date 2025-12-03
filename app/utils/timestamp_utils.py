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
    
    Algorithm (Zero-based normalization friendly):
    - For start: Find the first word where word.start >= (moment_start - padding)
    - For end: Find the first word where word.end >= (moment_end + padding)
    - This ensures clip_start is the start of the first included word, eliminating
      negative timestamps when normalizing with offset = clip_start
    
    Args:
        word_timestamps: List of word timestamp dictionaries with 'word', 'start', 'end'
        moment_start: Original moment start time in seconds
        moment_end: Original moment end time in seconds
        padding: Seconds to pad before start and after end (single value for both sides)
        margin: Additional margin in seconds to allow going slightly beyond padding
                (default: 2.0) - used as fallback tolerance
    
    Returns:
        Tuple of (clip_start, clip_end) in seconds, aligned to word boundaries
    
    Example:
        >>> words = [
        ...     {'word': 'the', 'start': 29.76, 'end': 30.12},
        ...     {'word': 'quick', 'start': 32.50, 'end': 32.80},
        ...     # ... more words ...
        ...     {'word': 'end', 'start': 149.8, 'end': 150.2}
        ... ]
        >>> calculate_padded_boundaries(words, 60.0, 120.0, 30.0)
        (32.50, 150.2)  # First word starting at/after 30s, first word ending at/after 150s
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
    
    logger.debug(
        f"Finding word boundaries for moment [{moment_start:.2f}s - {moment_end:.2f}s] "
        f"with {padding:.1f}s padding"
    )
    logger.debug(f"Target start: {target_start:.2f}s, Target end: {target_end:.2f}s")
    
    # Find the start boundary: FIRST word that starts at or AFTER target_start
    clip_start = None
    best_start_word = None
    
    for word in word_timestamps:
        word_start = float(word['start'])
        
        # Find the first word that starts at or after target
        if word_start >= target_start:
            clip_start = word_start
            best_start_word = word['word']
            break
    
    # If no word found at or after target_start, use the first available word
    if clip_start is None:
        if word_timestamps:
            clip_start = float(word_timestamps[0]['start'])
            best_start_word = word_timestamps[0]['word']
            logger.warning(
                f"No word found at or after target start {target_start:.2f}s, "
                f"using first available word at {clip_start:.2f}s"
            )
        else:
            clip_start = target_start
            logger.warning(f"No words available, using target start: {clip_start:.2f}s")
    
    # Find the end boundary: FIRST word that ends at or AFTER target_end
    clip_end = None
    best_end_word = None
    
    for word in word_timestamps:
        word_end = float(word['end'])
        
        # Find the first word that ends at or after target
        if word_end >= target_end:
            clip_end = word_end
            best_end_word = word['word']
            break
    
    # If no word found at or after target_end, use the last available word
    if clip_end is None:
        if word_timestamps:
            clip_end = float(word_timestamps[-1]['end'])
            best_end_word = word_timestamps[-1]['word']
            logger.warning(
                f"No word found at or after target end {target_end:.2f}s, "
                f"using last available word at {clip_end:.2f}s"
            )
        else:
            clip_end = target_end
            logger.warning(f"No words available, using target end: {clip_end:.2f}s")
    
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
    
    A word is included only if it is fully contained within the time range, i.e., if:
    word_start >= start_time AND word_end <= end_time
    
    Args:
        word_timestamps: List of word timestamp dictionaries with 'word', 'start', 'end'
        start_time: Start of the time range in seconds
        end_time: End of the time range in seconds
    
    Returns:
        List of word dictionaries that are fully contained within the specified range
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
        
        # Include word only if it is fully contained within the range
        if word_start >= start_time and word_end <= end_time:
            extracted.append({
                'word': str(word_data['word']),
                'start': word_start,
                'end': word_end
            })
    
    logger.debug(
        f"Extracted {len(extracted)} words from range [{start_time:.2f}s - {end_time:.2f}s]"
    )
    
    return extracted


def normalize_word_timestamps(
    words: List[Dict],
    offset: float
) -> List[Dict]:
    """
    Normalize word timestamps by subtracting an offset to make them relative to 0.
    
    This is useful when preparing transcripts to send alongside video clips,
    where the clip starts at 0 but the original transcript has absolute timestamps.
    
    Args:
        words: List of word dictionaries with 'word', 'start', and 'end' fields
        offset: The time offset to subtract from all timestamps (typically clip_start)
    
    Returns:
        New list of word dictionaries with normalized timestamps (start and end shifted by -offset)
    
    Example:
        >>> words = [
        ...     {'word': 'the', 'start': 28.5, 'end': 29.0},
        ...     {'word': 'key', 'start': 29.0, 'end': 29.3}
        ... ]
        >>> normalize_word_timestamps(words, 28.5)
        [
            {'word': 'the', 'start': 0.0, 'end': 0.5},
            {'word': 'key', 'start': 0.5, 'end': 0.8}
        ]
    """
    if not words:
        return []
    
    normalized = []
    for word in words:
        if not isinstance(word, dict):
            continue
        
        if 'word' not in word or 'start' not in word or 'end' not in word:
            logger.warning(f"Word missing required fields: {word}")
            continue
        
        try:
            normalized.append({
                'word': word['word'],
                'start': float(word['start']) - offset,
                'end': float(word['end']) - offset
            })
        except (ValueError, TypeError) as e:
            logger.warning(f"Error normalizing word timestamp: {word}, error: {e}")
            continue
    
    logger.info(
        f"Normalized {len(normalized)} words with offset {offset:.2f}s "
        f"(first word now starts at {normalized[0]['start']:.2f}s)" if normalized else 
        f"Normalized 0 words with offset {offset:.2f}s"
    )
    
    return normalized


def denormalize_timestamp(
    relative_time: float,
    offset: float
) -> float:
    """
    Convert a normalized (relative) timestamp back to absolute time by adding the offset.
    
    This is the inverse operation of normalize_word_timestamps for individual timestamps.
    
    Args:
        relative_time: Time relative to 0 (e.g., from normalized transcript or model response)
        offset: The original offset that was subtracted (typically clip_start)
    
    Returns:
        Absolute timestamp (relative_time + offset)
    
    Example:
        >>> denormalize_timestamp(31.62, 28.5)
        60.12
    """
    absolute_time = relative_time + offset
    logger.debug(f"Denormalized timestamp: {relative_time:.2f}s + {offset:.2f}s = {absolute_time:.2f}s")
    return absolute_time

