"""Text-to-speech service using OpenAI TTS API.

Note: The main audio generation logic is in audio_service.py.
This module is kept for backward compatibility and direct TTS access.
"""

from app.services.audio_service import (
    PERSONA_VOICES,
    generate_episode_audio,
    generate_segment_audio,
)

__all__ = ["PERSONA_VOICES", "generate_episode_audio", "generate_segment_audio"]
