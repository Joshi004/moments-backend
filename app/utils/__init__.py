"""
Utility functions and configuration for Video Moments application.
Contains pure utility functions and configuration constants.
"""

# Timestamp utilities
from app.utils.timestamp import (
    calculate_padded_boundaries,
    extract_words_in_range,
    normalize_word_timestamps,
    denormalize_timestamp
)

# Video utilities
from app.utils.video import (
    get_videos_directory,
    get_video_files,
    get_video_by_filename
)

# Model configuration
from app.utils.model_config import (
    MODELS,
    CLIPPING_CONFIG,
    VIDEO_SERVER_CONFIG,
    VIDEO_ENCODING_CONFIG,
    get_model_config,
    get_model_url,
    get_transcription_service_url,
    get_clipping_config,
    get_video_server_config,
    model_supports_video,
    get_video_clip_url,
    get_duration_tolerance,
    get_encoding_config,
    get_parallel_workers
)

# Logging utilities
from app.utils.logging_config import (
    setup_logging,
    get_request_id,
    set_request_id,
    generate_request_id,
    set_operation,
    get_operation,
    log_event,
    log_operation_start,
    log_operation_complete,
    log_operation_error,
    operation_logger,
    log_status_check
)

__all__ = [
    # Timestamp utilities
    "calculate_padded_boundaries",
    "extract_words_in_range",
    "normalize_word_timestamps",
    "denormalize_timestamp",
    
    # Video utilities
    "get_videos_directory",
    "get_video_files",
    "get_video_by_filename",
    
    # Model configuration
    "MODELS",
    "CLIPPING_CONFIG",
    "VIDEO_SERVER_CONFIG",
    "VIDEO_ENCODING_CONFIG",
    "get_model_config",
    "get_model_url",
    "get_transcription_service_url",
    "get_clipping_config",
    "get_video_server_config",
    "model_supports_video",
    "get_video_clip_url",
    "get_duration_tolerance",
    "get_encoding_config",
    "get_parallel_workers",
    
    # Logging utilities
    "setup_logging",
    "get_request_id",
    "set_request_id",
    "generate_request_id",
    "set_operation",
    "get_operation",
    "log_event",
    "log_operation_start",
    "log_operation_complete",
    "log_operation_error",
    "operation_logger",
    "log_status_check",
]

