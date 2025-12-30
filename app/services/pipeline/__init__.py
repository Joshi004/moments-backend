"""
Pipeline services package for unified video processing.
"""
from app.services.pipeline.status import (
    initialize_status,
    update_stage_status,
    mark_stage_started,
    mark_stage_completed,
    mark_stage_skipped,
    mark_stage_failed,
    get_status,
    delete_status,
    update_pipeline_status,
    update_current_stage,
    get_current_stage,
)
from app.services.pipeline.lock import (
    acquire_lock,
    release_lock,
    is_locked,
    refresh_lock,
    set_cancellation_flag,
    check_cancellation,
    clear_cancellation,
)
from app.services.pipeline.history import (
    save_to_history,
    load_history,
    get_latest_run,
)
from app.services.pipeline.upload_service import SCPUploader
from app.services.pipeline.orchestrator import execute_pipeline

__all__ = [
    # Status
    "initialize_status",
    "update_stage_status",
    "mark_stage_started",
    "mark_stage_completed",
    "mark_stage_skipped",
    "mark_stage_failed",
    "get_status",
    "delete_status",
    "update_pipeline_status",
    "update_current_stage",
    "get_current_stage",
    # Lock
    "acquire_lock",
    "release_lock",
    "is_locked",
    "refresh_lock",
    "set_cancellation_flag",
    "check_cancellation",
    "clear_cancellation",
    # History
    "save_to_history",
    "load_history",
    "get_latest_run",
    # Upload
    "SCPUploader",
    # Orchestrator
    "execute_pipeline",
]



