"""QStash service for production background job processing."""

from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def enqueue_job_processing(job_id: UUID) -> str | None:
    """Enqueue a job for background processing.

    In development: returns None (local BackgroundTasks is used instead).
    In production: publishes to QStash which calls our internal endpoint.
    """
    settings = get_settings()

    if settings.is_development:
        logger.debug("Development mode: skipping QStash enqueue", extra={"job_id": str(job_id)})
        return None

    if not settings.qstash_token:
        logger.warning("QStash token not configured, cannot enqueue job")
        return None

    try:
        from qstash import QStash

        client = QStash(settings.qstash_token)

        destination_url = f"{settings.public_api_url}/v1/internal/jobs/{job_id}/process"

        result = client.message.publish_json(
            url=destination_url,
            body={"job_id": str(job_id)},
            retries=3,
            delay="0s",
        )

        message_id = result.message_id if hasattr(result, "message_id") else str(result)

        logger.info(
            "Enqueued job for processing via QStash",
            extra={"job_id": str(job_id), "message_id": message_id},
        )

        return message_id

    except Exception as e:
        logger.error(
            "Failed to enqueue job via QStash",
            extra={"job_id": str(job_id), "error": str(e)},
        )
        raise
