"""Security utilities for authentication and request verification."""

from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def verify_qstash_signature(request: Request) -> bool:
    """Verify QStash webhook signature for production requests.

    In development mode, this check is skipped.

    Uses PUBLIC_API_URL + request.url.path as the verification URL,
    since Render's internal request URL may differ from the public URL
    that QStash signed against.
    """
    settings = get_settings()

    if settings.is_development:
        return True

    if not settings.qstash_current_signing_key:
        logger.warning("QStash signing key not configured, skipping verification")
        return True

    signature = request.headers.get("upstash-signature")
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing QStash signature",
        )

    body = await request.body()

    # Reconstruct the URL QStash signed: PUBLIC_API_URL + path
    verification_url = (
        f"{settings.public_api_url.rstrip('/')}{request.url.path}"
    )

    try:
        from qstash import Receiver

        receiver = Receiver(
            current_signing_key=settings.qstash_current_signing_key.strip(),
            next_signing_key=(settings.qstash_next_signing_key or "").strip(),
        )

        receiver.verify(
            body=body.decode("utf-8"),
            signature=signature,
            url=verification_url,
        )

        return True

    except Exception as e:
        logger.warning(
            "QStash signature verification failed",
            extra={
                "verification_url": verification_url,
                "exception_type": type(e).__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid QStash signature",
        )


async def get_current_user_id(request: Request) -> str | None:
    """Extract user ID from Supabase JWT in the Authorization header.

    For the MVP, this returns None if no token is present.
    When auth is fully enabled, this will raise 401 for missing/invalid tokens.
    """
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        # MVP: allow unauthenticated requests
        return None

    # TODO: Validate Supabase JWT and extract user_id
    return None


def validate_user_ownership(
    resource_user_id: str | None,
    requesting_user_id: str | None,
) -> None:
    """Ensure the requesting user owns the resource.

    Skipped when auth is not yet enabled (both values None).
    """
    if requesting_user_id is None:
        return

    if resource_user_id is None:
        return

    if resource_user_id != requesting_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",
        )
