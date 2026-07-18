"""Podcast Editor agent.

Generates a conversational podcast script from verified findings.
The Podcast Editor does NOT have internet access — it works only
with the approved findings provided by the Verification agent.

Includes a script critic that can request one controlled revision.
Uses Pydantic structured output for reliable script generation.
"""

import json

from app.core.logging import get_logger
from app.models.podcast import PodcastScript
from app.models.research import ResearchFinding
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


# The dialogue_type values must exactly match the DialogueType enum
_ALLOWED_DIALOGUE_TYPES = "observation, fact, story, question, response, transition, intro, outro, advice"


EDITOR_SYSTEM_PROMPT = f"""\
You are the WanderAI Podcast Editor. You create engaging, conversational \
travel podcast scripts featuring two personas: a Photographer and a Historian.

Rules:
- Use ONLY the approved findings provided. Do not invent facts.
- Keep each persona's voice distinct and authentic.
- The Photographer speaks with visual, sensory language about light, color, and composition.
- The Historian provides context, stories, and cultural depth with measured authority.
- Avoid repetitive or fake banter.
- Create natural conversation flow.
- Generate an episode title, chapters, and dialogue segments.
- Map each factual segment to its source finding IDs.
- Target the requested episode duration.
- Include an intro and outro.

IMPORTANT: Each segment's dialogue_type MUST be one of exactly these values: {_ALLOWED_DIALOGUE_TYPES}
Do NOT use any other dialogue_type value."""


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


async def _generate_script_structured(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    findings_text: str,
    error_context: str | None = None,
) -> PodcastScript:
    """Generate a podcast script using Pydantic structured output."""
    client = get_openai_client()

    user_prompt = f"""Create a podcast episode for:
- Destination: {destination_name}
- Region: {region or 'Not specified'}
- Target duration: {episode_minutes} minutes
- Personas: {', '.join(personas)}

Approved findings to use:
{findings_text}"""

    if error_context:
        user_prompt += f"\n\nPREVIOUS ATTEMPT FAILED with this validation error:\n{error_context}\n\nFix the issue. Use ONLY allowed dialogue_type values: {_ALLOWED_DIALOGUE_TYPES}"

    response = await client.responses.parse(
        model="gpt-4o",
        input=[
            {"role": "system", "content": EDITOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        text_format=PodcastScript,
    )

    parsed = response.output_parsed
    if parsed is None:
        raise ValueError("Podcast editor returned no parseable output")

    return parsed


async def _generate_script_with_retry(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    findings_text: str,
) -> PodcastScript:
    """Generate script with one retry on validation failure."""
    try:
        return await _generate_script_structured(
            destination_name=destination_name,
            region=region,
            episode_minutes=episode_minutes,
            personas=personas,
            findings_text=findings_text,
        )
    except Exception as first_error:
        logger.warning(
            "Script generation failed, retrying with error context",
            extra={"error": str(first_error)},
        )

        try:
            return await _generate_script_structured(
                destination_name=destination_name,
                region=region,
                episode_minutes=episode_minutes,
                personas=personas,
                findings_text=findings_text,
                error_context=str(first_error),
            )
        except Exception as retry_error:
            logger.error(
                "Script generation failed on retry",
                extra={"error": str(retry_error)},
            )
            raise


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
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    findings_text: str,
) -> PodcastScript:
    """Revise the script based on critic feedback using structured output."""
    client = get_openai_client()

    script_json = json.dumps(script.model_dump(), indent=2, default=str)

    revision_prompt = f"""Revise this podcast script based on the following feedback:

FEEDBACK:
{revision_guidance}

CURRENT SCRIPT:
{script_json}

APPROVED FINDINGS (for reference):
{findings_text}

Output the revised script. Use ONLY these dialogue_type values: {_ALLOWED_DIALOGUE_TYPES}"""

    response = await client.responses.parse(
        model="gpt-4o",
        input=[
            {"role": "system", "content": EDITOR_SYSTEM_PROMPT},
            {"role": "user", "content": revision_prompt},
        ],
        text_format=PodcastScript,
    )

    parsed = response.output_parsed
    if parsed is None:
        raise ValueError("Script revision returned no parseable output")

    return parsed


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
    Uses Pydantic structured output for reliable parsing with one retry.
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

    # Step 1: Generate initial script with retry
    script = await _generate_script_with_retry(
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
            extra={"issues": issues},
        )

        revised_script = await _revise_script(
            script=script,
            revision_guidance=revision_guidance,
            destination_name=destination_name,
            region=region,
            episode_minutes=episode_minutes,
            personas=personas,
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
