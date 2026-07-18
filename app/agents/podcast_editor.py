"""Podcast Editor agent.

Generates a conversational podcast script from verified findings.
The Podcast Editor does NOT have internet access — it works only
with the approved findings provided by the Verification agent.

Includes a script critic that can request one controlled revision.

Responsibilities:
- Use only approved findings
- Generate a conversational script with distinct persona voices
- Avoid repetitive or fake banter
- Generate episode title and chapters
- Map factual segments to source-backed findings
- Respect requested episode length
- Self-critique and revise once if needed
"""

import json

from app.core.logging import get_logger
from app.models.podcast import PodcastScript
from app.models.research import ResearchFinding
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


EDITOR_SYSTEM_PROMPT = """You are the WanderAI Podcast Editor. You create engaging, conversational 
travel podcast scripts featuring two personas: a Photographer and a Historian.

Rules:
- Use ONLY the approved findings provided. Do not invent facts.
- Keep each persona's voice distinct and authentic.
- The Photographer speaks with visual, sensory language about light, color, and composition.
- The Historian provides context, stories, and cultural depth with measured authority.
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


CRITIC_SYSTEM_PROMPT = """You are the WanderAI Script Critic. Review the podcast script and identify issues.

Check for:
1. Repetitive dialogue or echoing the same point
2. Fake/forced banter that sounds unnatural
3. Missing finding IDs on factual claims
4. Duration too far from target (>20% off)
5. Segments that sound like the wrong persona
6. Lack of conversational flow (all monologues, no back-and-forth)

If the script is good, respond with: {"approved": true, "notes": "..."}

If revision is needed, respond with:
{"approved": false, "issues": ["issue 1", "issue 2"], "revision_guidance": "Specific instructions for fixing the script"}

Output valid JSON only."""


async def _generate_script(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    findings_text: str,
) -> PodcastScript:
    """Generate a podcast script from findings."""
    client = get_openai_client()

    user_prompt = f"""Create a podcast episode for:
- Destination: {destination_name}
- Region: {region or 'Not specified'}
- Target duration: {episode_minutes} minutes
- Personas: {', '.join(personas)}

Approved findings to use:
{findings_text}"""

    response = await client.responses.create(
        model="gpt-4o",
        input=[
            {"role": "system", "content": EDITOR_SYSTEM_PROMPT},
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
    return PodcastScript(**script_data)


async def _critique_script(script: PodcastScript, episode_minutes: int) -> dict:
    """Run the script critic. Returns critique result."""
    client = get_openai_client()

    script_json = json.dumps(script.model_dump(), indent=2, default=str)

    user_prompt = f"""Review this podcast script. Target duration is {episode_minutes} minutes.

Script:
{script_json}"""

    response = await client.responses.create(
        model="gpt-4o",
        input=[
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
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

    return json.loads(output_text)


async def _revise_script(
    script: PodcastScript,
    revision_guidance: str,
    findings_text: str,
) -> PodcastScript:
    """Revise the script based on critic feedback."""
    client = get_openai_client()

    script_json = json.dumps(script.model_dump(), indent=2, default=str)

    revision_prompt = f"""Revise this podcast script based on the following feedback:

FEEDBACK:
{revision_guidance}

CURRENT SCRIPT:
{script_json}

APPROVED FINDINGS (for reference):
{findings_text}

Output the revised script as valid JSON with the same structure."""

    response = await client.responses.create(
        model="gpt-4o",
        input=[
            {"role": "system", "content": EDITOR_SYSTEM_PROMPT},
            {"role": "user", "content": revision_prompt},
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
    return PodcastScript(**script_data)


async def run_podcast_editor(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    approved_findings: list[ResearchFinding],
) -> PodcastScript:
    """Generate a podcast script with critic review and one allowed revision.

    The editor has NO web access. It works exclusively with the
    findings that passed verification.
    """
    # Convert findings to JSON text
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
        logger.warning("No approved findings for script generation")
        return PodcastScript(
            title=f"Exploring {destination_name}",
            destination_name=destination_name,
            episode_minutes_target=episode_minutes,
            personas=personas,
        )

    findings_text = json.dumps(findings_dicts, indent=2)

    logger.info(
        "Generating podcast script",
        extra={
            "destination": destination_name,
            "findings_count": len(findings_dicts),
            "episode_minutes": episode_minutes,
        },
    )

    # Step 1: Generate initial script
    script = await _generate_script(
        destination_name=destination_name,
        region=region,
        episode_minutes=episode_minutes,
        personas=personas,
        findings_text=findings_text,
    )

    logger.info(
        "Initial script generated",
        extra={"title": script.title, "segments": len(script.segments)},
    )

    # Step 2: Run the critic
    try:
        critique = await _critique_script(script, episode_minutes)

        if critique.get("approved", True):
            logger.info("Script approved by critic", extra={"notes": critique.get("notes", "")})
            return script

        # Step 3: One controlled revision
        revision_guidance = critique.get("revision_guidance", "")
        issues = critique.get("issues", [])
        logger.info(
            "Script revision requested by critic",
            extra={"issues": issues, "guidance": revision_guidance},
        )

        revised_script = await _revise_script(
            script=script,
            revision_guidance=revision_guidance,
            findings_text=findings_text,
        )

        logger.info(
            "Script revised",
            extra={"title": revised_script.title, "segments": len(revised_script.segments)},
        )

        return revised_script

    except Exception as e:
        # If critic/revision fails, use the initial script
        logger.warning(
            "Critic/revision failed, using initial script",
            extra={"error": str(e)},
        )
        return script
