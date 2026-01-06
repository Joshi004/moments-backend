"""
Pipeline history persistence to JSON files.
Stores all completed pipeline runs for each video.
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
from app.core.config import get_settings
from app.models.pipeline_schemas import PipelineStage
from app.services.pipeline.status import get_status

logger = logging.getLogger(__name__)


def _get_history_directory() -> Path:
    """Get the history directory, creating it if necessary."""
    settings = get_settings()
    history_dir = Path(__file__).parent.parent.parent.parent / settings.pipeline_history_dir
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir


def _get_history_file_path(video_id: str) -> Path:
    """Get the path to the history file for a video."""
    history_dir = _get_history_directory()
    return history_dir / f"{video_id}.json"


def _calculate_stage_duration(stage_data: Dict[str, str]) -> Optional[float]:
    """Calculate duration for a stage from start and end times."""
    started_at = stage_data.get("started_at", "")
    completed_at = stage_data.get("completed_at", "")
    
    if started_at and completed_at:
        try:
            return float(completed_at) - float(started_at)
        except ValueError:
            return None
    return None


def _build_history_entry(status_data: Dict[str, str]) -> Dict[str, Any]:
    """
    Build a history entry from Redis status data.
    
    Args:
        status_data: Raw status data from Redis Hash
    
    Returns:
        Formatted history entry dictionary
    """
    # Parse config
    config_str = status_data.get("config", "{}")
    try:
        config = json.loads(config_str)
    except json.JSONDecodeError:
        config = {}
    
    # Calculate total duration
    started_at_str = status_data.get("started_at", "")
    completed_at_str = status_data.get("completed_at", "")
    total_duration = None
    
    if started_at_str and completed_at_str:
        try:
            total_duration = float(completed_at_str) - float(started_at_str)
        except ValueError:
            pass
    
    # Build stages dictionary
    stages = {}
    for stage in PipelineStage:
        prefix = stage.value
        stage_status = status_data.get(f"{prefix}_status", "pending")
        stage_skipped = status_data.get(f"{prefix}_skipped", "false") == "true"
        
        stage_entry: Dict[str, Any] = {
            "status": stage_status,
            "skipped": stage_skipped,
        }
        
        # Add skip reason if skipped
        if stage_skipped:
            skip_reason = status_data.get(f"{prefix}_skip_reason", "")
            if skip_reason:
                stage_entry["skip_reason"] = skip_reason
        
        # Add timing if not skipped
        if not stage_skipped and stage_status not in ["pending", "skipped"]:
            started_at = status_data.get(f"{prefix}_started_at", "")
            completed_at = status_data.get(f"{prefix}_completed_at", "")
            
            if started_at:
                try:
                    stage_entry["started_at"] = float(started_at)
                except ValueError:
                    pass
            
            if completed_at:
                try:
                    stage_entry["completed_at"] = float(completed_at)
                except ValueError:
                    pass
            
            # Calculate duration
            if "started_at" in stage_entry and "completed_at" in stage_entry:
                stage_entry["duration_seconds"] = stage_entry["completed_at"] - stage_entry["started_at"]
        
        # Add refinement progress if applicable
        if stage == PipelineStage.MOMENT_REFINEMENT:
            refinement_total = status_data.get("refinement_total", "0")
            refinement_processed = status_data.get("refinement_processed", "0")
            try:
                stage_entry["moments_refined"] = int(refinement_processed)
                stage_entry["moments_total"] = int(refinement_total)
            except ValueError:
                pass
        
        stages[stage.value] = stage_entry
    
    # Build result summary
    result = {}
    
    # Count moments generated
    if stages.get("generation", {}).get("status") == "completed":
        # Could extract this from somewhere if tracked
        pass
    
    # Count moments refined
    refinement_stage = stages.get("refinement", {})
    if "moments_refined" in refinement_stage:
        result["moments_refined"] = refinement_stage["moments_refined"]
    
    # Build final entry
    entry = {
        "request_id": status_data.get("request_id", ""),
        "video_id": status_data.get("video_id", ""),
        "status": status_data.get("status", "unknown"),
        "model": status_data.get("model", ""),
        "started_at": float(started_at_str) if started_at_str else 0,
        "completed_at": float(completed_at_str) if completed_at_str else None,
        "total_duration_seconds": total_duration,
        "stages": stages,
        "result": result,
        "error_stage": status_data.get("error_stage", "") or None,
        "error_message": status_data.get("error_message", "") or None,
    }
    
    return entry


async def save_to_history(video_id: str) -> None:
    """
    Save completed pipeline run to history JSON file.
    Appends to existing array of runs.
    
    Args:
        video_id: Video identifier
    """
    # Get current status from Redis
    status_data = get_status(video_id)
    
    if not status_data:
        logger.warning(f"No status data found for {video_id}, cannot save to history")
        return
    
    # Build history entry
    entry = _build_history_entry(status_data)
    
    # Load existing history
    history_file = _get_history_file_path(video_id)
    
    if history_file.exists():
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            if not isinstance(history, list):
                logger.warning(f"History file for {video_id} is not a list, starting fresh")
                history = []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse history file for {video_id}: {e}, starting fresh")
            history = []
    else:
        history = []
    
    # Append new entry
    history.append(entry)
    
    # Save back to file
    try:
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved pipeline run to history for {video_id}: {entry['request_id']}")
    except Exception as e:
        logger.error(f"Failed to save history for {video_id}: {e}")
        raise


async def load_history(video_id: str) -> List[Dict[str, Any]]:
    """
    Load all historical pipeline runs for a video.
    
    Args:
        video_id: Video identifier
    
    Returns:
        List of history entries (oldest first)
    """
    history_file = _get_history_file_path(video_id)
    
    if not history_file.exists():
        return []
    
    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
        
        if not isinstance(history, list):
            logger.warning(f"History file for {video_id} is not a list")
            return []
        
        return history
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse history file for {video_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"Failed to load history for {video_id}: {e}")
        return []


async def get_latest_run(video_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the most recent completed pipeline run for a video.
    
    Args:
        video_id: Video identifier
    
    Returns:
        Latest history entry or None
    """
    history = await load_history(video_id)
    
    if history:
        return history[-1]  # Last entry is the most recent
    
    return None





