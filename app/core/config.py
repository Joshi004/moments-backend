"""
Application configuration using Pydantic Settings.
All configuration values can be overridden via environment variables or .env file.
"""
import os
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional, List


class Settings(BaseSettings):
    """Application settings with environment variable support."""
    
    # Application
    app_name: str = "Video Moments API"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"
    
    # Paths (relative to backend root)
    temp_base_dir: Path = Path("temp")
    temp_cleanup_interval_hours: float = 6.0    # Run cleanup every 6 hours
    temp_max_age_hours: float = 24.0            # Delete files older than 24 hours
    
    # Model: MiniMax
    minimax_name: str = "MiniMax"
    minimax_model_id: Optional[str] = None
    minimax_host: str = "100.80.5.15"
    minimax_port: int = 9084
    minimax_supports_video: bool = False
    
    # Model: Qwen3-VL
    qwen_name: str = "Qwen3-VL"
    qwen_model_id: str = "qwen3-vl-235b-thinking"
    qwen_host: str = "100.90.255.107"
    qwen_port: int = 8010
    qwen_supports_video: bool = False
    
    # Model: Qwen3-Omni
    qwen3_omni_name: str = "Qwen3-Omini"
    qwen3_omni_model_id: Optional[str] = None
    qwen3_omni_host: str = "localhost"
    qwen3_omni_port: int = 7101
    qwen3_omni_supports_video: bool = False
    qwen3_omni_top_p: float = 0.95
    qwen3_omni_top_k: int = 20
    
    # Model: Qwen3-VL-FP8
    qwen3_vl_fp8_name: str = "Qwen3-VL-FP8"
    qwen3_vl_fp8_model_id: Optional[str] = None
    qwen3_vl_fp8_host: str = "100.90.255.107"
    qwen3_vl_fp8_port: int = 8010
    qwen3_vl_fp8_supports_video: bool = True
    
    # Service: Parakeet (Transcription)
    parakeet_name: str = "Parakeet"
    parakeet_host: str = "100.80.5.15"
    parakeet_port: int = 8006
    
    # Video Clipping Configuration
    clip_padding: float = 30.0  # Padding in seconds
    clip_margin: float = 2.0     # Margin for word boundaries in seconds

    # Video Server Configuration
    video_server_port: int = int(os.getenv('BACKEND_PORT', '7005'))
    duration_tolerance: float = 0.5  # Tolerance for transcript-video duration matching
    
    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    
    # Database Configuration (PostgreSQL)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vision_ai"
    database_sync_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/vision_ai"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_echo: bool = False
    
    # Job Lock Configuration
    job_lock_ttl: int = 900      # 15 minutes
    job_result_ttl: int = 30     # 30 seconds post-completion
    
    # Tunnel mode (local dev without Tailscale VPN)
    # When true, start_backend.sh creates SSH tunnels automatically.
    use_tunnels: bool = False

    # CORS Configuration
    # Comma-separated list of allowed origins. Default "*" allows all origins.
    # Example: "http://localhost:3005,https://staging.example.com"
    cors_origins: str = "*"

    # Container identification
    container_id: str = os.getenv("HOSTNAME", f"backend-{os.getpid()}")
    
    # Video Encoding Configuration
    parallel_workers: int = 4
    macos_encoder: str = "h264_videotoolbox"
    macos_quality: int = 70
    linux_encoder: str = "libx264"
    linux_preset: str = "fast"
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"
    
    # AI Model Configuration
    max_tokens: int = 15000
    default_temperature: float = 0.7
    
    # Pipeline Configuration
    pipeline_lock_ttl: int = 1800  # 30 minutes

    # Orphaned Job Recovery Configuration
    # How often (seconds) the worker writes its heartbeat key to Redis.
    heartbeat_interval_seconds: int = 10
    # TTL (seconds) on the heartbeat key. Must be > heartbeat_interval_seconds.
    # Redis auto-expires this key if the worker dies, signalling a crash.
    heartbeat_ttl_seconds: int = 30
    # TTL (seconds) on the active pipeline status hash (pipeline:{video_id}:active).
    # Refreshed after each stage. Falls back to Redis auto-expiry when the only
    # worker dies so the frontend eventually stops polling.
    status_ttl_seconds: int = 1200  # 20 minutes
    # How many times a message can be delivered before being moved to the DLQ.
    # Prevents a poison message from crashing workers in an infinite loop.
    max_message_retries: int = 3
    # Redis Stream key used as the Dead Letter Queue.
    dead_letter_stream: str = "pipeline:dead_letters"
    # TTL (seconds) for individual DLQ entries.
    dead_letter_ttl_seconds: int = 604800  # 7 days
    # Min idle time (ms) before XAUTOCLAIM picks up a stale message.
    claim_min_idle_ms: int = 60000  # 1 minute

    # Pipeline History Configuration (Redis-based)
    pipeline_history_ttl: int = 86400         # 24 hours for completed runs
    pipeline_history_max_runs: int = 50       # Max runs to keep per video
    
    # GCS Configuration
    gcs_bucket_name: str = "rumble-ai-bucket-1"
    gcs_audio_prefix: str = "audio/"
    gcs_clips_prefix: str = "clips/"
    gcs_videos_prefix: str = "videos/"
    gcs_thumbnails_prefix: str = "thumbnails/"
    gcs_signed_url_expiry_hours: float = 168.0
    gcs_upload_timeout_seconds: int = 1800  # 30 minutes
    gcs_max_retries: int = 3
    gcs_retry_base_delay: float = 1.0  # Exponential: 1s, 2s, 4s
    
    # GCS Service Account Configuration
    gcs_service_account_file: Optional[str] = None  # Relative or absolute path to JSON file
    
    # Video Download Configuration
    video_download_timeout_seconds: int = 1800  # 30 minutes
    video_download_max_size_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GB
    video_download_max_concurrent: int = 2
    video_download_chunk_size: int = 8192  # 8 KB chunks
    video_download_retry_count: int = 3
    video_download_retry_base_delay: float = 2.0
    
    # Pipeline Concurrency Configuration
    # Cross-pipeline limits for coordinating resource usage across concurrent executions
    max_concurrent_pipelines: int = 2  # Max pipelines running simultaneously in worker
    audio_extraction_max_concurrent: int = 2  # Max concurrent FFmpeg audio extractions
    transcription_max_concurrent: int = 2  # Max concurrent transcription API calls
    moment_generation_max_concurrent: int = 2  # Max concurrent AI generation calls
    clip_extraction_max_concurrent: int = 4  # Max total FFmpeg clip extractions
    refinement_max_concurrent: int = 1  # Max concurrent refinement API calls
    
    # Generic filename patterns (trigger hash-based ID)
    video_download_generic_names: List[str] = [
        "video", "clip", "output", "download", "untitled", 
        "temp", "file", "movie", "media"
    ]
    
    @property
    def gcs_credentials_path(self) -> Optional[Path]:
        """Get the full path to GCS service account credentials."""
        if self.gcs_service_account_file:
            # If absolute path provided, use it
            if Path(self.gcs_service_account_file).is_absolute():
                return Path(self.gcs_service_account_file)
            
            # Otherwise, treat as relative to backend root
            backend_dir = Path(__file__).parent.parent.parent
            return backend_dir / self.gcs_service_account_file
        
        return None
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    


# Global settings instance (lazily initialized)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

