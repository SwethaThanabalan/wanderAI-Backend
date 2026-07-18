"""Local filesystem storage service for POC."""

import json
import os
from pathlib import Path
from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _get_storage_dir() -> Path:
    """Return the local storage directory, creating it if needed."""
    settings = get_settings()
    storage_path = Path(settings.local_storage_path)
    storage_path.mkdir(parents=True, exist_ok=True)
    return storage_path


def upload_audio(job_id: UUID, audio_data: bytes) -> str:
    """Save episode audio to local filesystem.

    Returns the object key (relative path).
    """
    storage_dir = _get_storage_dir()
    episode_dir = storage_dir / "episodes" / str(job_id)
    episode_dir.mkdir(parents=True, exist_ok=True)

    object_key = f"episodes/{job_id}/audio.mp3"
    file_path = storage_dir / object_key

    file_path.write_bytes(audio_data)

    logger.info("Saved audio locally", extra={"job_id": str(job_id), "path": str(file_path)})
    return object_key


def upload_transcript(job_id: UUID, transcript_data: dict) -> str:
    """Save episode transcript JSON to local filesystem.

    Returns the object key (relative path).
    """
    storage_dir = _get_storage_dir()
    episode_dir = storage_dir / "episodes" / str(job_id)
    episode_dir.mkdir(parents=True, exist_ok=True)

    object_key = f"episodes/{job_id}/transcript.json"
    file_path = storage_dir / object_key

    file_path.write_text(json.dumps(transcript_data, indent=2), encoding="utf-8")

    logger.info("Saved transcript locally", extra={"job_id": str(job_id), "path": str(file_path)})
    return object_key


def upload_metadata(job_id: UUID, metadata: dict) -> str:
    """Save episode metadata JSON to local filesystem.

    Returns the object key (relative path).
    """
    storage_dir = _get_storage_dir()
    episode_dir = storage_dir / "episodes" / str(job_id)
    episode_dir.mkdir(parents=True, exist_ok=True)

    object_key = f"episodes/{job_id}/metadata.json"
    file_path = storage_dir / object_key

    file_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    logger.info("Saved metadata locally", extra={"job_id": str(job_id), "path": str(file_path)})
    return object_key


def generate_signed_url(object_key: str, expires_in: int = 3600) -> str:
    """Generate a local file URL for downloading.

    In the POC, this returns a URL served by the backend itself.
    """
    settings = get_settings()
    return f"{settings.public_api_url}/v1/files/{object_key}"


def get_file_path(object_key: str) -> Path | None:
    """Get the absolute path for a stored file. Returns None if not found."""
    storage_dir = _get_storage_dir()
    file_path = storage_dir / object_key

    if file_path.exists():
        return file_path
    return None


def delete_episode_assets(job_id: UUID) -> None:
    """Delete all local assets for an episode."""
    storage_dir = _get_storage_dir()
    episode_dir = storage_dir / "episodes" / str(job_id)

    if episode_dir.exists():
        import shutil
        shutil.rmtree(episode_dir)
        logger.info("Deleted episode assets", extra={"job_id": str(job_id)})
