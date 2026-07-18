"""Temporary local storage service using /tmp/wanderai/<job-id>/.

This is transient storage only. Do not assume Render local disk is durable.
A cleanup task removes directories older than 24 hours.
"""

import json
import shutil
import time
from pathlib import Path
from uuid import UUID

from app.core.logging import get_logger

logger = get_logger(__name__)

BASE_DIR = Path("/tmp/wanderai")

# Maximum age for temp files before cleanup (24 hours)
MAX_AGE_SECONDS = 24 * 60 * 60


def _get_job_dir(job_id: UUID) -> Path:
    """Return the temp directory for a job, creating it if needed."""
    job_dir = BASE_DIR / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


# --- Write Operations ---


def save_audio(job_id: UUID, audio_data: bytes) -> str:
    """Save episode audio to temp storage. Returns the object key."""
    job_dir = _get_job_dir(job_id)
    file_path = job_dir / "audio.mp3"
    file_path.write_bytes(audio_data)

    object_key = f"{job_id}/audio.mp3"
    logger.info("Saved audio", extra={"job_id": str(job_id), "bytes": len(audio_data)})
    return object_key


def save_transcript(job_id: UUID, transcript_data: dict) -> str:
    """Save episode transcript JSON. Returns the object key."""
    job_dir = _get_job_dir(job_id)
    file_path = job_dir / "transcript.json"
    file_path.write_text(json.dumps(transcript_data, indent=2), encoding="utf-8")

    object_key = f"{job_id}/transcript.json"
    logger.info("Saved transcript", extra={"job_id": str(job_id)})
    return object_key


def save_citations(job_id: UUID, citations_data: list[dict]) -> str:
    """Save episode citations JSON. Returns the object key."""
    job_dir = _get_job_dir(job_id)
    file_path = job_dir / "citations.json"
    file_path.write_text(json.dumps(citations_data, indent=2), encoding="utf-8")

    object_key = f"{job_id}/citations.json"
    logger.info("Saved citations", extra={"job_id": str(job_id)})
    return object_key


def save_metadata(job_id: UUID, metadata: dict) -> str:
    """Save episode metadata JSON. Returns the object key."""
    job_dir = _get_job_dir(job_id)
    file_path = job_dir / "metadata.json"
    file_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    object_key = f"{job_id}/metadata.json"
    logger.info("Saved metadata", extra={"job_id": str(job_id)})
    return object_key


# --- Read Operations ---


def get_file_path(job_id: UUID, filename: str) -> Path | None:
    """Get the absolute path for a stored file. Returns None if not found."""
    file_path = BASE_DIR / str(job_id) / filename
    if file_path.exists():
        return file_path
    return None


def get_audio_path(job_id: UUID) -> Path | None:
    """Get path to the episode audio file."""
    return get_file_path(job_id, "audio.mp3")


def get_transcript_path(job_id: UUID) -> Path | None:
    """Get path to the episode transcript."""
    return get_file_path(job_id, "transcript.json")


def get_citations_path(job_id: UUID) -> Path | None:
    """Get path to the episode citations."""
    return get_file_path(job_id, "citations.json")


def get_metadata_path(job_id: UUID) -> Path | None:
    """Get path to the episode metadata."""
    return get_file_path(job_id, "metadata.json")


# --- Cleanup ---


def delete_job_assets(job_id: UUID) -> None:
    """Delete all temp files for a specific job."""
    job_dir = BASE_DIR / str(job_id)
    if job_dir.exists():
        shutil.rmtree(job_dir)
        logger.info("Deleted job assets", extra={"job_id": str(job_id)})


def cleanup_old_temp_files() -> int:
    """Remove job directories older than 24 hours.

    Returns the number of directories cleaned up.
    Call this periodically (e.g., on app startup or via a scheduled task).
    """
    if not BASE_DIR.exists():
        return 0

    now = time.time()
    cleaned = 0

    for job_dir in BASE_DIR.iterdir():
        if not job_dir.is_dir():
            continue

        # Check modification time of the directory
        try:
            dir_mtime = job_dir.stat().st_mtime
            age_seconds = now - dir_mtime

            if age_seconds > MAX_AGE_SECONDS:
                shutil.rmtree(job_dir)
                cleaned += 1
                logger.info(
                    "Cleaned up old temp directory",
                    extra={"dir": str(job_dir), "age_hours": round(age_seconds / 3600, 1)},
                )
        except OSError as e:
            logger.warning("Failed to clean temp dir", extra={"dir": str(job_dir), "error": str(e)})

    if cleaned > 0:
        logger.info("Temp cleanup complete", extra={"cleaned": cleaned})

    return cleaned
