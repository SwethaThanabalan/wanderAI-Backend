"""Supabase client and database operations."""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import UUID

from supabase import Client, create_client

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.jobs import JobStatus

logger = get_logger(__name__)


@lru_cache
def get_supabase() -> Client:
    """Return a cached Supabase client using the service-role key."""
    settings = get_settings()

    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL is not configured.")

    if not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not configured.")

    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )


# --- Research Jobs ---


def create_research_job(
    trip_id: UUID,
    stop_id: UUID,
    destination_name: str,
    region: str | None,
    visit_date: Any | None,
    episode_minutes: int,
    personas: list[str],
    user_id: UUID | None = None,
) -> dict:
    """Insert a new research job and return the created record."""
    client = get_supabase()

    data: dict[str, Any] = {
        "trip_id": str(trip_id),
        "stop_id": str(stop_id),
        "destination_name": destination_name,
        "region": region,
        "visit_date": str(visit_date) if visit_date else None,
        "episode_minutes": episode_minutes,
        "personas": personas,
        "status": JobStatus.QUEUED,
    }

    if user_id:
        data["user_id"] = str(user_id)

    response = client.table("research_jobs").insert(data).execute()

    if not response.data:
        raise RuntimeError("Failed to create research job")

    logger.info("Created research job", extra={"job_id": response.data[0]["id"]})
    return response.data[0]


def get_research_job(job_id: UUID) -> dict | None:
    """Fetch a research job by ID. Returns None if not found."""
    client = get_supabase()

    response = (
        client.table("research_jobs")
        .select("*")
        .eq("id", str(job_id))
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


def update_job_status(
    job_id: UUID,
    status: JobStatus,
    error_message: str | None = None,
) -> dict | None:
    """Update a job's status. Sets started_at/completed_at timestamps as appropriate."""
    client = get_supabase()

    data: dict[str, Any] = {"status": status}

    if status == JobStatus.RESEARCHING:
        data["started_at"] = datetime.now(timezone.utc).isoformat()
    elif status in (JobStatus.COMPLETED, JobStatus.FAILED):
        data["completed_at"] = datetime.now(timezone.utc).isoformat()

    if error_message:
        data["error_message"] = error_message

    response = (
        client.table("research_jobs")
        .update(data)
        .eq("id", str(job_id))
        .execute()
    )

    if not response.data:
        return None

    logger.info(
        "Updated job status",
        extra={"job_id": str(job_id), "status": status},
    )
    return response.data[0]


def save_job_result(job_id: UUID, result: dict, citations: list[dict] | None = None) -> dict | None:
    """Save processing result and citations to a job."""
    client = get_supabase()

    data: dict[str, Any] = {"result": result}
    if citations:
        data["citations"] = citations

    response = (
        client.table("research_jobs")
        .update(data)
        .eq("id", str(job_id))
        .execute()
    )

    return response.data[0] if response.data else None


def save_job_object_keys(
    job_id: UUID,
    audio_object_key: str | None = None,
    transcript_object_key: str | None = None,
    metadata_object_key: str | None = None,
) -> dict | None:
    """Save storage object keys to a job."""
    client = get_supabase()

    data: dict[str, Any] = {}
    if audio_object_key:
        data["audio_object_key"] = audio_object_key
    if transcript_object_key:
        data["transcript_object_key"] = transcript_object_key
    if metadata_object_key:
        data["metadata_object_key"] = metadata_object_key

    if not data:
        return None

    response = (
        client.table("research_jobs")
        .update(data)
        .eq("id", str(job_id))
        .execute()
    )

    return response.data[0] if response.data else None


# --- Research Sources ---


def insert_research_sources(
    research_job_id: UUID,
    persona_id: str,
    sources: list[dict],
) -> list[dict]:
    """Bulk-insert research sources for a job/persona."""
    if not sources:
        return []

    client = get_supabase()

    rows = []
    for source in sources:
        rows.append({
            "research_job_id": str(research_job_id),
            "persona_id": persona_id,
            "url": source.get("url", ""),
            "title": source.get("title"),
            "publisher": source.get("publisher"),
            "source_type": source.get("source_type", "other"),
            "reliability_score": source.get("reliability_score"),
            "supporting_excerpt": source.get("supporting_excerpt"),
        })

    response = client.table("research_sources").insert(rows).execute()
    return response.data or []


# --- Research Findings ---


def insert_research_findings(
    research_job_id: UUID,
    persona_id: str,
    findings: list[dict],
) -> list[dict]:
    """Bulk-insert research findings for a job/persona."""
    if not findings:
        return []

    client = get_supabase()

    rows = []
    for finding in findings:
        rows.append({
            "research_job_id": str(research_job_id),
            "persona_id": persona_id,
            "claim": finding.get("claim", ""),
            "classification": finding.get("classification", "unverified"),
            "confidence": finding.get("confidence"),
            "approved": finding.get("approved", False),
            "source_ids": finding.get("source_ids", []),
            "podcast_potential": finding.get("podcast_potential"),
            "usage_guidance": finding.get("usage_guidance"),
        })

    response = client.table("research_findings").insert(rows).execute()
    return response.data or []


def update_findings_approval(
    research_job_id: UUID,
    approved_claims: list[str],
) -> None:
    """Mark findings as approved based on their claim text."""
    client = get_supabase()

    # Get all findings for the job
    response = (
        client.table("research_findings")
        .select("id, claim")
        .eq("research_job_id", str(research_job_id))
        .execute()
    )

    if not response.data:
        return

    for finding in response.data:
        if finding["claim"] in approved_claims:
            client.table("research_findings").update(
                {"approved": True}
            ).eq("id", finding["id"]).execute()


# --- Podcast Episodes ---


def create_podcast_episode(
    research_job_id: UUID,
    user_id: UUID | None,
    trip_id: UUID,
    stop_id: UUID,
    title: str,
    destination_name: str,
    duration_seconds: int | None,
    personas: list[str],
    chapters: list[dict] | None = None,
    citations: list[dict] | None = None,
    audio_object_key: str | None = None,
    transcript_object_key: str | None = None,
    metadata_object_key: str | None = None,
) -> dict:
    """Insert a podcast episode record."""
    client = get_supabase()

    data: dict[str, Any] = {
        "research_job_id": str(research_job_id),
        "trip_id": str(trip_id),
        "stop_id": str(stop_id),
        "title": title,
        "destination_name": destination_name,
        "duration_seconds": duration_seconds,
        "personas": personas,
        "chapters": chapters,
        "citations": citations,
        "audio_object_key": audio_object_key,
        "transcript_object_key": transcript_object_key,
        "metadata_object_key": metadata_object_key,
    }

    if user_id:
        data["user_id"] = str(user_id)

    response = client.table("podcast_episodes").insert(data).execute()

    if not response.data:
        raise RuntimeError("Failed to create podcast episode")

    logger.info(
        "Created podcast episode",
        extra={"episode_id": response.data[0]["id"], "job_id": str(research_job_id)},
    )
    return response.data[0]


def get_podcast_episode_by_job(research_job_id: UUID) -> dict | None:
    """Fetch a podcast episode by its research job ID."""
    client = get_supabase()

    response = (
        client.table("podcast_episodes")
        .select("*")
        .eq("research_job_id", str(research_job_id))
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]
