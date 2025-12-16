"""
Services layer for Video Moments application.
Contains business logic and orchestration for various operations.
"""

# Main services
from app.services.audio_service import (
    get_audio_directory,
    get_audio_path,
    check_audio_exists,
    get_audio_url,
    extract_audio_from_video,
    process_audio_async
)

from app.services.transcript_service import (
    get_transcript_directory,
    get_transcript_path,
    check_transcript_exists,
    load_transcript,
    save_transcript,
    process_transcription_async
)

from app.services.moments_service import (
    get_moments_directory,
    get_moments_file_path,
    generate_moment_id,
    load_moments,
    save_moments,
    validate_moment,
    get_moment_by_id,
    add_moment
)

from app.services.video_clipping_service import (
    get_moment_clips_directory,
    get_clip_path,
    check_clip_exists,
    get_clip_url,
    get_video_duration,
    get_clip_duration,
    extract_video_clip,
    process_clip_extraction_async,
    extract_clips_for_video
)

from app.services.thumbnail_service import (
    get_thumbnails_directory,
    get_thumbnail_path,
    extract_frame_from_video,
    generate_thumbnail,
    generate_thumbnails_for_all_videos,
    get_thumbnail_url
)

__all__ = [
    # Audio service
    "get_audio_directory",
    "get_audio_path",
    "check_audio_exists",
    "get_audio_url",
    "extract_audio_from_video",
    "process_audio_async",
    
    # Transcript service
    "get_transcript_directory",
    "get_transcript_path",
    "check_transcript_exists",
    "load_transcript",
    "save_transcript",
    "process_transcription_async",
    
    # Moments service
    "get_moments_directory",
    "get_moments_file_path",
    "generate_moment_id",
    "load_moments",
    "save_moments",
    "validate_moment",
    "get_moment_by_id",
    "add_moment",
    
    # Video clipping service
    "get_moment_clips_directory",
    "get_clip_path",
    "check_clip_exists",
    "get_clip_url",
    "get_video_duration",
    "get_clip_duration",
    "extract_video_clip",
    "process_clip_extraction_async",
    "extract_clips_for_video",
    
    # Thumbnail service
    "get_thumbnails_directory",
    "get_thumbnail_path",
    "extract_frame_from_video",
    "generate_thumbnail",
    "generate_thumbnails_for_all_videos",
    "get_thumbnail_url",
]

