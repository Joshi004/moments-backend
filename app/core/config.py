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
    videos_dir: Path = Path("static/videos")
    static_dir: Path = Path("static")
    audios_dir: Path = Path("static/audios")
    transcripts_dir: Path = Path("static/transcripts")
    moments_dir: Path = Path("static/moments")
    thumbnails_dir: Path = Path("static/thumbnails")
    moment_clips_dir: Path = Path("static/moment_clips")
    temp_processing_dir: Path = Path("temp/processing")
    
    # SSH Tunnel Configuration
    ssh_user: str = "naresh"
    ssh_host: str = "85.234.64.44"
    ssh_remote_host: str = "worker-9"
    
    # Model: MiniMax
    minimax_name: str = "MiniMax"
    minimax_model_id: Optional[str] = None
    minimax_local_port: int = 8007
    minimax_remote_port: int = 7104
    minimax_supports_video: bool = False
    
    # Model: Qwen3-VL
    qwen_name: str = "Qwen3-VL"
    qwen_model_id: str = "qwen3-vl-235b-thinking"
    qwen_local_port: int = 6101
    qwen_remote_port: int = 7001
    qwen_supports_video: bool = False
    
    # Model: Qwen3-Omni
    qwen3_omni_name: str = "Qwen3-Omini"
    qwen3_omni_model_id: Optional[str] = None
    qwen3_omni_local_port: int = 7101
    qwen3_omni_remote_port: int = 8002
    qwen3_omni_supports_video: bool = False
    qwen3_omni_top_p: float = 0.95
    qwen3_omni_top_k: int = 20
    
    # Model: Qwen3-VL-FP8
    qwen3_vl_fp8_name: str = "Qwen3-VL-FP8"
    qwen3_vl_fp8_model_id: Optional[str] = None
    qwen3_vl_fp8_local_port: int = 6010
    qwen3_vl_fp8_remote_port: int = 8010
    qwen3_vl_fp8_supports_video: bool = True
    
    # Service: Parakeet (Transcription)
    parakeet_name: str = "Parakeet"
    parakeet_local_port: int = 6106
    parakeet_remote_port: int = 8006
    
    # Video Clipping Configuration
    clip_padding: float = 30.0  # Padding in seconds
    clip_margin: float = 2.0     # Margin for word boundaries in seconds
    
    # Video Server Configuration
    # Use BACKEND_PORT if available, otherwise default to 7005
    # The video clips are served by the FastAPI backend itself at /moment_clips
    video_server_port: int = int(os.getenv('BACKEND_PORT', '7005'))
    video_server_clips_path: str = "moment_clips"
    duration_tolerance: float = 0.5  # Tolerance for transcript-video duration matching
    
    # Redis Configuration
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None
    
    # Database Configuration (PostgreSQL)
    database_url: str = "postgresql+asyncpg://nareshjoshi@localhost:5432/vision_ai"
    database_sync_url: str = "postgresql+psycopg2://nareshjoshi@localhost:5432/vision_ai"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_echo: bool = False
    
    # Job Lock Configuration
    job_lock_ttl: int = 900      # 15 minutes
    job_result_ttl: int = 30     # 30 seconds post-completion
    
    # Container identification
    container_id: str = os.getenv("HOSTNAME", f"backend-{os.getpid()}")
    
    @property
    def video_server_base_url(self) -> str:
        """Get the video server base URL using the backend port."""
        return f"http://localhost:{self.video_server_port}"
    
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
    
    # Pipeline History Configuration (Redis-based)
    pipeline_history_ttl: int = 86400         # 24 hours for completed runs
    pipeline_history_max_runs: int = 50       # Max runs to keep per video
    
    # SCP Upload Configuration (DEPRECATED - using GCS now)
    # scp_remote_host: str = "naresh@85.234.64.44"
    # scp_audio_remote_path: str = "/home/naresh/datasets/audios/"
    # scp_clips_remote_path: str = "/home/naresh/datasets/moment_clips/"
    # scp_connect_timeout: int = 10
    
    # GCS Configuration
    gcs_bucket_name: str = "rumble-ai-bucket-1"
    gcs_audio_prefix: str = "audio/"
    gcs_clips_prefix: str = "clips/"
    gcs_videos_prefix: str = "videos/"
    gcs_thumbnails_prefix: str = "thumbnails/"
    gcs_signed_url_expiry_hours: float = 4.0
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
    
    # URL Registry
    url_registry_file: Path = Path("static/url_registry.json")
    
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
    
    # Helper methods to maintain compatibility with old API
    def get_model_config(self, model_key: str) -> dict:
        """Get configuration for a specific model."""
        models = {
            "minimax": {
                "name": self.minimax_name,
                "model_id": self.minimax_model_id,
                "ssh_host": f"{self.ssh_user}@{self.ssh_host}",
                "ssh_remote_host": self.ssh_remote_host,
                "ssh_local_port": self.minimax_local_port,
                "ssh_remote_port": self.minimax_remote_port,
                "supports_video": self.minimax_supports_video,
            },
            "qwen": {
                "name": self.qwen_name,
                "model_id": self.qwen_model_id,
                "ssh_host": f"{self.ssh_user}@{self.ssh_host}",
                "ssh_remote_host": self.ssh_remote_host,
                "ssh_local_port": self.qwen_local_port,
                "ssh_remote_port": self.qwen_remote_port,
                "supports_video": self.qwen_supports_video,
            },
            "qwen3_omni": {
                "name": self.qwen3_omni_name,
                "model_id": self.qwen3_omni_model_id,
                "ssh_host": f"{self.ssh_user}@{self.ssh_host}",
                "ssh_remote_host": self.ssh_remote_host,
                "ssh_local_port": self.qwen3_omni_local_port,
                "ssh_remote_port": self.qwen3_omni_remote_port,
                "supports_video": self.qwen3_omni_supports_video,
                "top_p": self.qwen3_omni_top_p,
                "top_k": self.qwen3_omni_top_k,
            },
            "qwen3_vl_fp8": {
                "name": self.qwen3_vl_fp8_name,
                "model_id": self.qwen3_vl_fp8_model_id,
                "ssh_host": f"{self.ssh_user}@{self.ssh_host}",
                "ssh_remote_host": self.ssh_remote_host,
                "ssh_local_port": self.qwen3_vl_fp8_local_port,
                "ssh_remote_port": self.qwen3_vl_fp8_remote_port,
                "supports_video": self.qwen3_vl_fp8_supports_video,
            },
            "parakeet": {
                "name": self.parakeet_name,
                "ssh_host": f"{self.ssh_user}@{self.ssh_host}",
                "ssh_remote_host": self.ssh_remote_host,
                "ssh_local_port": self.parakeet_local_port,
                "ssh_remote_port": self.parakeet_remote_port,
            }
        }
        
        if model_key not in models:
            raise ValueError(f"Unknown model: {model_key}. Available models: {list(models.keys())}")
        
        return models[model_key]
    
    def get_model_url(self, model_key: str) -> str:
        """Get the local URL for a model API endpoint."""
        config = self.get_model_config(model_key)
        return f"http://localhost:{config['ssh_local_port']}/v1/chat/completions"
    
    def get_transcription_service_url(self) -> str:
        """Get the local URL for the transcription service endpoint."""
        config = self.get_model_config("parakeet")
        return f"http://localhost:{config['ssh_local_port']}/transcribe"
    
    def model_supports_video(self, model_key: str) -> bool:
        """Check if a model supports video input."""
        try:
            config = self.get_model_config(model_key)
            return config.get('supports_video', False)
        except ValueError:
            return False
    
    def get_video_clip_url(self, moment_id: str, video_filename: str) -> str:
        """Get the full URL for a video clip."""
        video_stem = Path(video_filename).stem
        clip_filename = f"{video_stem}_{moment_id}_clip.mp4"
        return f"{self.video_server_base_url}/{self.video_server_clips_path}/{clip_filename}"


# Global settings instance (lazily initialized)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

