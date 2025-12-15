"""
Application configuration using Pydantic Settings.
All configuration values can be overridden via environment variables or .env file.
"""
from pydantic_settings import BaseSettings
from pathlib import Path
from typing import Optional


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
    video_server_base_url: str = "http://localhost:8080"
    video_server_clips_path: str = "moment_clips"
    duration_tolerance: float = 0.5  # Tolerance for transcript-video duration matching
    
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

