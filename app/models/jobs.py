"""Pydantic models for podcast job requests, responses, and state."""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class Persona(StrEnum):
    """Supported podcast personas."""

    PHOTOGRAPHER = "photographer"
    HISTORIAN = "historian"
    GEOLOGIST = "geologist"
    FOODIE = "foodie"
    STORYTELLER = "storyteller"


class JobStatus(StrEnum):
    """Podcast job processing states."""

    QUEUED = "queued"
    RESEARCHING = "researching"
    VERIFYING = "verifying"
    SCRIPTING = "scripting"
    GENERATING_AUDIO = "generating_audio"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Request Models ---


class CreatePodcastJobRequest(BaseModel):
    """Request body for POST /v1/podcast-jobs."""

    trip_id: UUID
    stop_id: UUID
    destination_name: str = Field(min_length=1, max_length=200)
    region: str | None = Field(default=None, max_length=200)
    visit_date: date | None = None
    episode_minutes: int = Field(default=8, ge=3, le=20)
    personas: list[Persona] = Field(
        default=[Persona.PHOTOGRAPHER, Persona.HISTORIAN],
        min_length=1,
        max_length=5,
    )


# --- Response Models ---


class CreatePodcastJobResponse(BaseModel):
    """Response body for POST /v1/podcast-jobs."""

    job_id: UUID
    status: JobStatus = JobStatus.QUEUED


class EpisodeMetadata(BaseModel):
    """Episode information returned when a job is completed."""

    episode_id: UUID
    title: str
    duration_seconds: int | None = None
    audio_url: str | None = None
    transcript_url: str | None = None
    chapters: list[dict] | None = None
    citations: list[dict] | None = None


class PodcastJobResponse(BaseModel):
    """Response body for GET /v1/podcast-jobs/{job_id}."""

    job_id: UUID
    status: JobStatus
    destination_name: str
    region: str | None = None
    personas: list[Persona]
    episode_minutes: int
    error_message: str | None = None
    episode: EpisodeMetadata | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


# --- Internal Models ---


class JobRecord(BaseModel):
    """Full job record as stored in Supabase."""

    id: UUID
    user_id: UUID | None = None
    trip_id: UUID
    stop_id: UUID
    destination_name: str
    region: str | None = None
    visit_date: date | None = None
    episode_minutes: int
    personas: list[str]
    status: JobStatus
    result: dict | None = None
    citations: list[dict] | None = None
    audio_object_key: str | None = None
    transcript_object_key: str | None = None
    metadata_object_key: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
