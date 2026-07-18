"""Podcast script generation service."""

import json
from typing import Any

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.podcast import PodcastScript

logger = get_logger(__name__)


def _get_openai_client() -> AsyncOpenAI:
    """Return an async OpenAI client."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def generate_podcast_script(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    approved_findings: list[dict],
) -> PodcastScript:
    """Generate a conversational podcast script from approved findings.

    The Podcast Editor does NOT have web access. It works only with
    the approved findings provided to it.
    """
    client = _get_openai_client()

    findings_text = json.dumps(approved_findings, indent=2)

    system_prompt = """You are the WanderAI Podcast Editor. You create engaging, conversational 
travel podcast scripts featuring two personas: a Photographer and a Historian.

Rules:
- Use ONLY the approved findings provided. Do not invent facts.
- Keep each persona's voice distinct and authentic.
- Avoid repetitive or fake banter.
- Create natural conversation flow with observations, facts, stories, and transitions.
- Generate an episode title, chapters, and dialogue segments.
- Map each factual segment to its source finding IDs.
- Target the requested episode duration.
- Include an intro and outro.

Output a valid JSON object with this structure:
{
  "title": "Episode title",
  "destination_name": "...",
  "episode_minutes_target": N,
  "personas": ["photographer", "historian"],
  "chapters": [{"chapter_id": "ch-01", "title": "...", "start_segment_id": "seg-01", "end_segment_id": "seg-03", "topic": "..."}],
  "segments": [{"segment_id": "seg-01", "speaker": "photographer|historian", "dialogue": "...", "finding_ids": ["..."], "dialogue_type": "observation|fact|story|question|response|transition|intro|outro", "duration_estimate_seconds": N}],
  "total_estimated_duration_seconds": N
}"""

    user_prompt = f"""Create a podcast episode for:
- Destination: {destination_name}
- Region: {region or 'Not specified'}
- Target duration: {episode_minutes} minutes
- Personas: {', '.join(personas)}

Approved findings to use:
{findings_text}"""

    try:
        response = await client.responses.create(
            model="gpt-4o",
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            text={"format": {"type": "json_object"}},
        )

        output_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text

        script_data = json.loads(output_text)
        script = PodcastScript(**script_data)

        logger.info(
            "Podcast script generated",
            extra={
                "title": script.title,
                "segments_count": len(script.segments),
                "duration_estimate": script.total_estimated_duration_seconds,
            },
        )

        return script

    except Exception as e:
        logger.error("Podcast script generation failed", extra={"error": str(e)})
        raise
