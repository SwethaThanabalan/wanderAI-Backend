"""API route definitions."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.api.dependencies import get_app_settings, get_request_id, get_user_id
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import validate_user_ownership, verify_qstash_signature
from app.models.jobs import (
    CreatePodcastJobRequest,
    CreatePodcastJobResponse,
    EpisodeMetadata,
    JobStatus,
    PodcastJobResponse,
)
from app.services import supabase_service
from app.services.qstash_service import enqueue_job_processing
from app.workflows.podcast_generation import process_podcast_job

logger = get_logger(__name__)

# Public routes
router = APIRouter()

# Internal routes (for QStash/worker callbacks)
internal_router = APIRouter()


# --- Health ---


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


# --- Podcast Jobs ---


@router.post(
    "/v1/podcast-jobs",
    response_model=CreatePodcastJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_podcast_job(
    request_body: CreatePodcastJobRequest,
    background_tasks: BackgroundTasks,
    request_id: str = Depends(get_request_id),
    user_id: str | None = Depends(get_user_id),
    settings: Settings = Depends(get_app_settings),
):
    """Create a new podcast generation job.

    Returns HTTP 202 and immediately begins async processing.
    """
    logger.info(
        "Creating podcast job",
        extra={
            "request_id": request_id,
            "destination": request_body.destination_name,
            "personas": [p.value for p in request_body.personas],
        },
    )

    # Insert job into Supabase
    job = supabase_service.create_research_job(
        trip_id=request_body.trip_id,
        stop_id=request_body.stop_id,
        destination_name=request_body.destination_name,
        region=request_body.region,
        visit_date=request_body.visit_date,
        episode_minutes=request_body.episode_minutes,
        personas=[p.value for p in request_body.personas],
        user_id=UUID(user_id) if user_id else None,
    )

    job_id = UUID(job["id"])

    # Trigger processing
    if settings.is_development:
        # Local: use FastAPI BackgroundTasks
        background_tasks.add_task(process_podcast_job, job_id)
    else:
        # Production: enqueue via QStash
        await enqueue_job_processing(job_id)

    return CreatePodcastJobResponse(job_id=job_id, status=JobStatus.QUEUED)


@router.get(
    "/v1/podcast-jobs/{job_id}",
    response_model=PodcastJobResponse,
)
async def get_podcast_job(
    job_id: UUID,
    request_id: str = Depends(get_request_id),
    user_id: str | None = Depends(get_user_id),
):
    """Get the status and details of a podcast generation job."""
    job = supabase_service.get_research_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    # Enforce ownership when auth is enabled
    validate_user_ownership(job.get("user_id"), user_id)

    # Build response
    response = PodcastJobResponse(
        job_id=UUID(job["id"]),
        status=job["status"],
        destination_name=job["destination_name"],
        region=job.get("region"),
        personas=job["personas"],
        episode_minutes=job["episode_minutes"],
        error_message=job.get("error_message"),
        created_at=job["created_at"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
    )

    # Attach episode metadata if completed
    if job["status"] == JobStatus.COMPLETED and job.get("result"):
        episode = supabase_service.get_podcast_episode_by_job(job_id)
        if episode:
            response.episode = EpisodeMetadata(
                episode_id=UUID(episode["id"]),
                title=episode["title"],
                duration_seconds=episode.get("duration_seconds"),
                audio_url=None,  # Signed URLs generated on demand
                transcript_url=None,
                chapters=episode.get("chapters"),
                citations=episode.get("citations"),
            )

    return response


# --- Internal Processing ---


@internal_router.post("/v1/internal/jobs/{job_id}/process")
async def process_job_internal(
    job_id: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    request_id: str = Depends(get_request_id),
    settings: Settings = Depends(get_app_settings),
):
    """Internal endpoint called by QStash or worker to process a job.

    Verifies QStash signature in production.
    Idempotent: completed jobs are skipped.
    """
    # Verify QStash signature in production
    await verify_qstash_signature(request)

    # Check job exists
    job = supabase_service.get_research_job(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    # Idempotency: skip completed jobs
    if job["status"] == JobStatus.COMPLETED:
        logger.info("Job already completed", extra={"job_id": str(job_id)})
        return {"status": "already_completed"}

    # Process in background
    background_tasks.add_task(process_podcast_job, job_id)

    return {"status": "processing", "job_id": str(job_id)}


# --- File Download (local storage) ---


@router.get("/v1/files/{file_path:path}")
async def download_file(file_path: str):
    """Serve locally stored episode files."""
    from app.services.storage_service import get_file_path

    absolute_path = get_file_path(file_path)

    if not absolute_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    # Determine media type
    media_type = "application/octet-stream"
    if file_path.endswith(".mp3"):
        media_type = "audio/mpeg"
    elif file_path.endswith(".json"):
        media_type = "application/json"

    return FileResponse(absolute_path, media_type=media_type)
