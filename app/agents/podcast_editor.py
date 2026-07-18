"""Podcast Editor agent.

Generates a conversational podcast script from verified findings.
The Podcast Editor does NOT have internet access — it works only
with the approved findings provided by the Verification agent.

Responsibilities:
- Use only approved findings
- Generate a conversational script with distinct persona voices
- Avoid repetitive or fake banter
- Generate episode title and chapters
- Map factual segments to source-backed findings
- Respect requested episode length
"""

from app.core.logging import get_logger
from app.models.podcast import PodcastScript
from app.models.research import ResearchFinding
from app.services.podcast_service import generate_podcast_script

logger = get_logger(__name__)


async def run_podcast_editor(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    approved_findings: list[ResearchFinding],
) -> PodcastScript:
    """Generate a podcast script from approved research findings.

    The editor has NO web access. It works exclusively with the
    findings that passed verification.
    """
    # Convert findings to dicts for the script generation service
    findings_dicts = [
        {
            "claim": f.claim,
            "classification": f.classification,
            "confidence": f.confidence,
            "source_urls": f.source_urls,
            "podcast_potential": f.podcast_potential,
            "usage_guidance": f.usage_guidance,
        }
        for f in approved_findings
    ]

    if not findings_dicts:
        logger.warning(
            "No approved findings available for script generation",
            extra={"destination": destination_name},
        )
        # Return a minimal script
        return PodcastScript(
            title=f"Exploring {destination_name}",
            destination_name=destination_name,
            episode_minutes_target=episode_minutes,
            personas=personas,
        )

    logger.info(
        "Starting podcast script generation",
        extra={
            "destination": destination_name,
            "findings_count": len(findings_dicts),
            "episode_minutes": episode_minutes,
        },
    )

    script = await generate_podcast_script(
        destination_name=destination_name,
        region=region,
        episode_minutes=episode_minutes,
        personas=personas,
        approved_findings=findings_dicts,
    )

    logger.info(
        "Podcast script completed",
        extra={
            "title": script.title,
            "chapters": len(script.chapters),
            "segments": len(script.segments),
        },
    )

    return script
