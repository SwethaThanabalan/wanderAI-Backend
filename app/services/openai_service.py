"""Shared OpenAI client factory.

Provides a single async OpenAI client used by research, podcast, and TTS services.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_openai_client() -> AsyncOpenAI:
    """Return a configured async OpenAI client.

    Raises RuntimeError if OPENAI_API_KEY is not set.
    """
    settings = get_settings()

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    return AsyncOpenAI(api_key=settings.openai_api_key)
