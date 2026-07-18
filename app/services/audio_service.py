"""Audio service for combining TTS segments into a single episode file."""

from uuid import UUID

from app.core.logging import get_logger
from app.models.podcast import PodcastScript, ScriptSegment
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)

# Consistent voice assignments per persona
PERSONA_VOICES: dict[str, str] = {
    "photographer": "nova",
    "historian": "onyx",
}


async def generate_segment_audio(segment: ScriptSegment) -> bytes:
    """Generate TTS audio for a single dialogue segment.

    Returns raw MP3 bytes.
    """
    client = get_openai_client()
    voice = PERSONA_VOICES.get(segment.speaker, "alloy")

    try:
        response = await client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=segment.dialogue,
            response_format="mp3",
        )

        audio_bytes = response.content

        logger.debug(
            "Generated segment audio",
            extra={
                "segment_id": segment.segment_id,
                "speaker": segment.speaker,
                "voice": voice,
                "bytes": len(audio_bytes),
            },
        )

        return audio_bytes

    except Exception as e:
        logger.error(
            "TTS failed for segment",
            extra={"segment_id": segment.segment_id, "error": str(e)},
        )
        raise


async def generate_episode_audio(script: PodcastScript) -> bytes:
    """Generate audio for all segments and concatenate into episode.mp3.

    Processes segments sequentially to maintain dialogue order.
    Returns combined MP3 bytes.
    """
    all_parts: list[bytes] = []

    for segment in script.segments:
        audio_bytes = await generate_segment_audio(segment)
        all_parts.append(audio_bytes)

    # MP3 frame concatenation — valid for sequential playback
    combined = b"".join(all_parts)

    logger.info(
        "Episode audio generated",
        extra={
            "segments": len(script.segments),
            "total_bytes": len(combined),
        },
    )

    return combined


def estimate_duration_seconds(script: PodcastScript) -> int:
    """Estimate total episode duration from dialogue word count.

    ~150 words per minute average speaking rate.
    """
    total_words = sum(len(seg.dialogue.split()) for seg in script.segments)
    return int((total_words / 150.0) * 60.0)
