"""API route definitions."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse

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
from app.services.qstash_service import QStashPublishError, enqueue_job_processing
from app.services.temp_storage_service import (
    get_audio_path,
    get_citations_path,
    get_metadata_path,
    get_transcript_path,
)
from app.workflows.podcast_generation import process_podcast_job

logger = get_logger(__name__)

# Public routes
router = APIRouter()

# Internal routes (for QStash/worker callbacks)
internal_router = APIRouter()


# --- Health ---


@router.get("/health")
async def health_check():
    """Health check endpoint. Does not call external services."""
    from app.core.config import get_settings
    from app.main import APP_VERSION

    settings = get_settings()
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "environment": settings.app_env,
    }


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
        try:
            await enqueue_job_processing(job_id)
        except QStashPublishError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Job created but background processing is temporarily unavailable. Please retry.",
            )

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
    settings = get_app_settings()
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
            base_url = settings.public_api_url
            response.episode = EpisodeMetadata(
                episode_id=UUID(episode["id"]),
                title=episode["title"],
                duration_seconds=episode.get("duration_seconds"),
                audio_url=f"{base_url}/v1/episodes/{job_id}/audio",
                transcript_url=f"{base_url}/v1/episodes/{job_id}/transcript",
                chapters=episode.get("chapters"),
                citations=episode.get("citations"),
            )

    return response


# --- Episode Asset Endpoints ---


@router.get("/v1/episodes/{job_id}/audio")
async def get_episode_audio(job_id: UUID):
    """Download the episode audio file (MP3).

    Returns 404 if the job is not complete or audio is unavailable.
    """
    path = get_audio_path(job_id)
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio not found. Job may still be processing.",
        )
    return FileResponse(path, media_type="audio/mpeg", filename=f"{job_id}.mp3")


@router.get("/v1/episodes/{job_id}/transcript")
async def get_episode_transcript(job_id: UUID):
    """Download the episode transcript JSON.

    Returns 404 if unavailable.
    """
    path = get_transcript_path(job_id)
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript not found. Job may still be processing.",
        )
    return FileResponse(path, media_type="application/json", filename="transcript.json")


@router.get("/v1/episodes/{job_id}/citations")
async def get_episode_citations(job_id: UUID):
    """Download the episode citations JSON.

    Returns 404 if unavailable.
    """
    path = get_citations_path(job_id)
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Citations not found. Job may still be processing.",
        )
    return FileResponse(path, media_type="application/json", filename="citations.json")


@router.get("/v1/episodes/{job_id}/metadata")
async def get_episode_metadata(job_id: UUID):
    """Download the episode metadata JSON.

    Returns 404 if unavailable.
    """
    path = get_metadata_path(job_id)
    if not path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metadata not found. Job may still be processing.",
        )
    return FileResponse(path, media_type="application/json", filename="metadata.json")


# --- Voice Q&A ---


@router.post("/v1/voice-qa")
async def voice_question_answer(
    request: Request,
    request_id: str = Depends(get_request_id),
):
    """Voice-to-voice Q&A: send an audio question, get an audio answer.

    Accepts audio as the raw request body (Content-Type: audio/*).
    Transcribes the question, generates a contextual answer, converts to speech.

    Query parameters (all optional):
    - persona: which persona voice to use (photographer, historian, etc.)
    - destination: active destination for grounding
    - trip_name: current trip name
    - current_stop: current stop name for local context
    - script_context: base64-encoded recent narration text (for follow-up questions)

    Returns:
    - Content-Type: audio/mpeg (raw MP3 bytes)
    - X-Transcription: the transcribed question text
    - X-Answer-Text: the generated answer text

    The client can play the response body directly as MP3 audio.
    """
    from base64 import b64decode

    from fastapi.responses import Response

    from app.services.voice_qa_service import voice_question_to_voice_answer

    # Read raw audio bytes from request body
    audio_bytes = await request.body()
    if not audio_bytes or len(audio_bytes) < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must contain audio data (m4a, mp3, wav, etc.)",
        )

    # Extract optional context from query params
    params = request.query_params
    persona_id = params.get("persona")
    destination = params.get("destination")

    trip_context = {}
    if destination:
        trip_context["destination"] = destination
    if params.get("trip_name"):
        trip_context["trip_name"] = params.get("trip_name")
    if params.get("state"):
        trip_context["state"] = params.get("state")
    if params.get("current_stop"):
        trip_context["current_stop_name"] = params.get("current_stop")

    # Decode script context if provided (base64)
    script_context = None
    if params.get("script_context"):
        try:
            script_context = b64decode(params.get("script_context")).decode("utf-8")
        except Exception:
            pass  # Ignore malformed base64

    # Determine audio filename from content-type for proper transcription
    content_type = request.headers.get("content-type", "audio/m4a")
    ext_map = {
        "audio/m4a": "question.m4a",
        "audio/mp4": "question.mp4",
        "audio/mpeg": "question.mp3",
        "audio/mp3": "question.mp3",
        "audio/wav": "question.wav",
        "audio/x-wav": "question.wav",
        "audio/webm": "question.webm",
        "audio/ogg": "question.ogg",
        "audio/flac": "question.flac",
    }
    filename = ext_map.get(content_type, "question.m4a")

    logger.info(
        "Voice Q&A request",
        extra={
            "request_id": request_id,
            "audio_bytes": len(audio_bytes),
            "persona": persona_id,
            "destination": destination,
            "content_type": content_type,
        },
    )

    try:
        answer_audio, question_text, answer_text = await voice_question_to_voice_answer(
            audio_bytes=audio_bytes,
            filename=filename,
            persona_id=persona_id,
            destination=destination,
            trip_context=trip_context if trip_context else None,
            script_context=script_context,
        )

        return Response(
            content=answer_audio,
            media_type="audio/mpeg",
            headers={
                "X-Transcription": question_text[:500] if question_text else "",
                "X-Answer-Text": answer_text[:1000] if answer_text else "",
                "Content-Disposition": "inline; filename=\"answer.mp3\"",
            },
        )

    except Exception as e:
        logger.error("Voice Q&A failed", extra={"request_id": request_id, "error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Voice Q&A processing failed. Please try again.",
        )


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
