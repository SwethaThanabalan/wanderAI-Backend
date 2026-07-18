"""Text-to-speech service using OpenAI TTS API."""

import asyncio
import io
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.podcast import PodcastScript, ScriptSegment

logger = get_logger(__name__)

# Voice mapping for personas
PERSONA_VOICES: dict[str, str] = {
    "photographer": "nova",      # Warm, conversational female voice
    "historian": "onyx",         # Deep, authoritative male voice
}


def _get_openai_client() -> AsyncOpenAI:
    """Return an async OpenAI client."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_segment_audio(
    segment: ScriptSegment,
    voice: str | None = None,
) -> bytes:
    """Generate audio for a single script segment.

    Returns raw audio bytes (mp3 format).
    """
    client = _get_openai_client()

    resolved_voice = voice or PERSONA_VOICES.get(segment.speaker, "alloy")

    try:
        response = await client.audio.speech.create(
            model="tts-1",
            voice=resolved_voice,
            input=segment.dialogue,
            response_format="mp3",
        )

        audio_bytes = response.content

        logger.debug(
            "Generated segment audio",
            extra={
                "segment_id": segment.segment_id,
                "speaker": segment.speaker,
                "voice": resolved_voice,
                "bytes": len(audio_bytes),
            },
        )

        return audio_bytes

    except Exception as e:
        logger.error(
            "TTS generation failed for segment",
            extra={"segment_id": segment.segment_id, "error": str(e)},
        )
        raise


async def generate_episode_audio(script: PodcastScript) -> bytes:
    """Generate audio for all segments and combine into a single episode.

    Processes segments sequentially to maintain order, but could be
    parallelized with ordering preserved in the future.

    Returns combined audio bytes (mp3).
    """
    all_audio_parts: list[bytes] = []

    for segment in script.segments:
        voice = PERSONA_VOICES.get(segment.speaker, "alloy")
        audio_bytes = await generate_segment_audio(segment, voice=voice)
        all_audio_parts.append(audio_bytes)

    # Simple concatenation for MP3 frames
    # For production, use pydub or ffmpeg for proper audio merging
    combined = b"".join(all_audio_parts)

    logger.info(
        "Episode audio generated",
        extra={
            "segments_count": len(script.segments),
            "total_bytes": len(combined),
        },
    )

    return combined


async def estimate_duration_seconds(text: str) -> float:
    """Estimate speech duration based on word count.

    Average speaking rate is approximately 150 words per minute.
    """
    word_count = len(text.split())
    return (word_count / 150.0) * 60.0
