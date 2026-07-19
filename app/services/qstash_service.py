"""QStash service for production background job processing."""

from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.jobs import JobStatus

logger = get_logger(__name__)


class QStashPublishError(Exception):
    """Raised when QStash publish fails."""
    pass


async def enqueue_job_processing(job_id: UUID) -> str | None:
    """Enqueue a job for background processing.

    In development: returns None (local BackgroundTasks is used instead).
    In production: publishes to QStash which calls our internal endpoint.

    If publishing fails after the Supabase job is already created,
    the job is marked as 'failed' with a sanitized error message.
    Raises QStashPublishError so the API can return 503.
    """
    settings = get_settings()

    if settings.is_development:
        logger.debug("Development mode: skipping QStash enqueue", extra={"job_id": str(job_id)})
        return None

    if not settings.qstash_token:
        logger.warning("QStash token not configured, cannot enqueue job")
        await _mark_job_failed(job_id, "QStash token not configured")
        raise QStashPublishError("QStash token not configured")

    try:
        from qstash import QStash

        client = QStash(
            token=settings.qstash_token,
            base_url=settings.qstash_url,
        )

        destination_url = f"{settings.public_api_url}/v1/internal/jobs/{job_id}/process"

        result = client.message.publish_json(
            url=destination_url,
            body={"job_id": str(job_id)},
            retries=3,
        )

        message_id = result.message_id if hasattr(result, "message_id") else str(result)

        logger.info(
            "Enqueued job for processing via QStash",
            extra={
                "job_id": str(job_id),
                "message_id": message_id,
                "qstash_region": settings.qstash_url,
            },
        )

        return message_id

    except Exception as e:
        # Sanitize: never log tokens or full error that might contain credentials
        error_msg = str(e)
        if "token" in error_msg.lower() or "key" in error_msg.lower():
            sanitized = "QStash authentication or region error"
        else:
            sanitized = f"QStash publish failed: {type(e).__name__}"

        logger.error(
            "Failed to enqueue job via QStash",
            extra={"job_id": str(job_id), "error": sanitized},
        )

        await _mark_job_failed(job_id, sanitized)
        raise QStashPublishError(sanitized) from e


async def _mark_job_failed(job_id: UUID, error_message: str) -> None:
    """Mark a job as failed when QStash publish fails."""
    try:
        from app.services import supabase_service
        supabase_service.update_job_status(
            job_id=job_id,
            status=JobStatus.FAILED,
            error_message=error_message,
        )
    except Exception as e:
        logger.error(
            "Failed to mark job as failed after QStash error",
            extra={"job_id": str(job_id), "error": str(e)},
        )
