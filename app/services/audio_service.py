"""Audio service for combining TTS segments into a single episode file.

Includes MP3 duration measurement and audio validation.
"""

import struct
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

# Duration constants
WORDS_PER_MINUTE = 175


# --- MP3 Duration Measurement ---

# MP3 bitrate lookup tables
_BITRATE_TABLE_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_SAMPLE_RATE_TABLE_V1 = [44100, 48000, 32000, 0]


def measure_mp3_duration_seconds(mp3_data: bytes) -> float:
    """Estimate the duration of MP3 data in seconds.

    Uses a sampling approach: reads the first few frames to determine
    the average bitrate, then divides total size by bitrate.
    Falls back to word-count estimation if parsing fails.
    """
    if len(mp3_data) < 1024:
        return 0.0

    # Try to find average bitrate from first N frames
    total_bitrate = 0
    frame_count = 0
    pos = 0
    max_scan = min(len(mp3_data), 200_000)  # Scan up to ~200KB

    while pos < max_scan - 4 and frame_count < 50:
        # Look for frame sync (0xFFE0 mask for MPEG sync)
        if mp3_data[pos] == 0xFF and (mp3_data[pos + 1] & 0xE0) == 0xE0:
            header = struct.unpack(">I", mp3_data[pos:pos + 4])[0]

            # Extract fields
            version = (header >> 19) & 0x03
            layer = (header >> 17) & 0x03
            bitrate_idx = (header >> 12) & 0x0F
            sample_rate_idx = (header >> 10) & 0x03
            padding = (header >> 9) & 0x01

            # We only handle MPEG1 Layer 3
            if version == 3 and layer == 1 and bitrate_idx > 0 and bitrate_idx < 15 and sample_rate_idx < 3:
                bitrate = _BITRATE_TABLE_V1_L3[bitrate_idx] * 1000
                sample_rate = _SAMPLE_RATE_TABLE_V1[sample_rate_idx]

                total_bitrate += bitrate
                frame_count += 1

                # Calculate frame size and skip to next frame
                frame_size = (144 * bitrate) // sample_rate + padding
                pos += frame_size
                continue

        pos += 1

    if frame_count == 0 or total_bitrate == 0:
        # Fallback: assume 128kbps
        return (len(mp3_data) * 8) / 128000.0

    avg_bitrate = total_bitrate / frame_count
    duration = (len(mp3_data) * 8) / avg_bitrate

    return duration


# --- TTS Generation ---


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

    actual_duration = measure_mp3_duration_seconds(combined)

    logger.info(
        "Episode audio generated",
        extra={
            "segments": len(script.segments),
            "total_bytes": len(combined),
            "actual_duration_seconds": round(actual_duration, 1),
        },
    )

    return combined


def estimate_duration_seconds(script: PodcastScript) -> int:
    """Estimate total episode duration from dialogue word count.

    Uses 175 words per minute (calibrated for TTS delivery).
    """
    total_words = sum(len(seg.dialogue.split()) for seg in script.segments)
    return int((total_words / WORDS_PER_MINUTE) * 60.0)


def count_script_words(script: PodcastScript) -> int:
    """Count total spoken words across all segments."""
    return sum(len(seg.dialogue.split()) for seg in script.segments)
