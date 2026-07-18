"""FastAPI dependencies for request handling."""

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.logging import generate_request_id
from app.core.security import get_current_user_id


async def get_request_id(request: Request) -> str:
    """Extract or generate a request ID for tracing."""
    request_id = request.headers.get("X-Request-ID")
    if not request_id:
        request_id = generate_request_id()
    return request_id


async def get_user_id(request: Request) -> str | None:
    """Extract the authenticated user ID from the request."""
    return await get_current_user_id(request)


def get_app_settings() -> Settings:
    """Return the application settings."""
    return get_settings()
