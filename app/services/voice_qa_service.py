"""Voice Q&A service.

Accepts audio input (user's spoken question), transcribes it via Whisper,
generates a contextual answer using GPT-4o with the trip/persona context,
then converts the answer to spoken audio via OpenAI TTS.

Returns raw MP3 bytes ready to stream back to the client.
"""

from __future__ import annotations

import io
from typing import Any

from app.chat.personas import PERSONA_CONFIGS, VOICE_RULES
from app.core.logging import get_logger
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)

# Voice mapping for TTS responses
RESPONSE_VOICES: dict[str, str] = {
    "planner": "alloy",
    "photographer": "nova",
    "historian": "onyx",
    "geologist": "fable",
    "foodie": "shimmer",
    "storyteller": "echo",
}

DEFAULT_VOICE = "nova"


async def transcribe_audio(audio_bytes: bytes, filename: str = "question.m4a") -> str:
    """Transcribe audio bytes to text using OpenAI Whisper.

    Supports: m4a, mp3, mp4, mpeg, mpga, wav, webm, ogg, flac
    """
    client = get_openai_client()

    # Wrap bytes in a file-like object with a name
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename

    try:
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="text",
        )

        text = transcript.strip() if isinstance(transcript, str) else str(transcript).strip()

        logger.info(
            "Audio transcribed",
            extra={"audio_filename": filename, "text_length": len(text)},
        )

        return text

    except Exception as e:
        logger.error("Transcription failed", extra={"error": str(e)})
        raise


async def generate_contextual_answer(
    question: str,
    persona_id: str | None = None,
    destination: str | None = None,
    trip_context: dict[str, Any] | None = None,
    script_context: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Generate a spoken-word answer to the user's question.

    The answer is grounded in the trip context and written for audio delivery.
    """
    client = get_openai_client()

    # Build system prompt
    persona_section = ""
    if persona_id and persona_id in PERSONA_CONFIGS:
        config = PERSONA_CONFIGS[persona_id]
        persona_section = f"You are {config.display_name}. Respond in character.\n\n"

    grounding = ""
    if destination:
        grounding += f"Active destination: {destination}\n"
    if trip_context:
        if trip_context.get("trip_name"):
            grounding += f"Trip: {trip_context['trip_name']}\n"
        if trip_context.get("state"):
            grounding += f"State: {trip_context['state']}\n"
        if trip_context.get("current_stop_name"):
            grounding += f"Current stop: {trip_context['current_stop_name']}\n"
        if trip_context.get("collected_places"):
            grounding += f"Places in trip: {', '.join(trip_context['collected_places'][:10])}\n"

    script_section = ""
    if script_context:
        # Provide the existing podcast/narration script as context
        # Trim to avoid token overflow (keep last ~3000 chars)
        trimmed = script_context[-3000:] if len(script_context) > 3000 else script_context
        script_section = f"\nEXISTING NARRATION CONTEXT (the user has been listening to this):\n{trimmed}\n"

    system_prompt = f"""{persona_section}You are answering a spoken question from a traveler.

{grounding}{script_section}
RESPONSE RULES:
- Answer the question directly and helpfully.
- Your response will be converted to SPOKEN AUDIO — write for the ear.
- Keep your answer concise but complete. Aim for 20-60 seconds of speech (50-150 words).
- Do NOT use markdown, bullet points, URLs, or visual formatting.
- Use natural spoken language with varied sentence length.
- If the question relates to the active destination or current narration, ground your answer there.
- If you don't know something, say so briefly rather than inventing information.
- Sound like a knowledgeable travel companion, not a search engine.
"""

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]

    # Add conversation history if provided
    if conversation_history:
        for msg in conversation_history[-6:]:  # Keep recent context only
            messages.append(msg)

    messages.append({"role": "user", "content": question})

    try:
        response = await client.responses.create(
            model="gpt-4o",
            input=messages,
        )

        output_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text

        if not output_text:
            output_text = "I'm not sure how to answer that. Could you ask again?"

        logger.info(
            "Answer generated",
            extra={
                "question_length": len(question),
                "answer_length": len(output_text),
                "persona": persona_id,
            },
        )

        return output_text

    except Exception as e:
        logger.error("Answer generation failed", extra={"error": str(e)})
        raise


async def text_to_speech(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Convert text to MP3 audio using OpenAI TTS.

    Returns raw MP3 bytes.
    """
    client = get_openai_client()

    try:
        response = await client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text,
            response_format="mp3",
        )

        audio_bytes = response.content

        logger.info(
            "TTS generated",
            extra={"voice": voice, "text_length": len(text), "audio_bytes": len(audio_bytes)},
        )

        return audio_bytes

    except Exception as e:
        logger.error("TTS failed", extra={"error": str(e)})
        raise


async def voice_question_to_voice_answer(
    audio_bytes: bytes,
    filename: str = "question.m4a",
    persona_id: str | None = None,
    destination: str | None = None,
    trip_context: dict[str, Any] | None = None,
    script_context: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[bytes, str, str]:
    """Full pipeline: audio question → transcribe → answer → TTS.

    Returns (audio_response_bytes, transcribed_question, answer_text).
    """
    # 1. Transcribe the question
    question_text = await transcribe_audio(audio_bytes, filename)

    if not question_text or len(question_text.strip()) < 2:
        # Empty or unintelligible audio
        fallback = "I didn't quite catch that. Could you ask again?"
        voice = RESPONSE_VOICES.get(persona_id or "", DEFAULT_VOICE)
        fallback_audio = await text_to_speech(fallback, voice)
        return fallback_audio, "", fallback

    # 2. Generate contextual answer
    answer_text = await generate_contextual_answer(
        question=question_text,
        persona_id=persona_id,
        destination=destination,
        trip_context=trip_context,
        script_context=script_context,
        conversation_history=conversation_history,
    )

    # 3. Convert answer to speech
    voice = RESPONSE_VOICES.get(persona_id or "", DEFAULT_VOICE)
    answer_audio = await text_to_speech(answer_text, voice)

    logger.info(
        "Voice Q&A completed",
        extra={
            "question": question_text[:100],
            "answer_length": len(answer_text),
            "audio_bytes": len(answer_audio),
            "persona": persona_id,
        },
    )

    return answer_audio, question_text, answer_text
