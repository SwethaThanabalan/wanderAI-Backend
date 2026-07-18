"""Podcast Editor agent.

Generates a conversational podcast script from verified findings.
The Podcast Editor does NOT have internet access — it works only
with the approved findings provided by the Verification agent.

Uses Pydantic structured output for reliable script generation.
Enforces duration targets using 175 words per minute.
Expands scripts that fall below the minimum word count (up to 2 retries).
"""

import json

from app.core.logging import get_logger
from app.models.podcast import PodcastScript
from app.models.research import ResearchFinding
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)

# Duration constants
WORDS_PER_MINUTE = 175
MIN_WORD_RATIO = 0.95
MIN_CHAPTERS_8_MIN = 5
MIN_SEGMENTS_8_MIN = 16
MAX_EXPANSION_RETRIES = 2

# The dialogue_type values must exactly match the DialogueType enum
_ALLOWED_DIALOGUE_TYPES = "observation, fact, story, question, response, transition, intro, outro, advice"


def _compute_duration_targets(episode_minutes: int) -> dict:
    """Compute word count and duration targets for a given episode length."""
    target_word_count = episode_minutes * WORDS_PER_MINUTE
    minimum_word_count = int(target_word_count * MIN_WORD_RATIO)
    preferred_upper = int(target_word_count * 1.1)
    min_duration_seconds = int(episode_minutes * 60 * MIN_WORD_RATIO)
    preferred_duration_lower = episode_minutes * 60
    preferred_duration_upper = int(episode_minutes * 60 * 1.08)
    min_chapters = max(5, episode_minutes // 2 + 1)
    min_segments = max(16, episode_minutes * 2)

    return {
        "target_word_count": target_word_count,
        "minimum_word_count": minimum_word_count,
        "preferred_upper_word_count": preferred_upper,
        "min_duration_seconds": min_duration_seconds,
        "preferred_duration_lower": preferred_duration_lower,
        "preferred_duration_upper": preferred_duration_upper,
        "min_chapters": min_chapters,
        "min_segments": min_segments,
    }


def count_script_words(script: PodcastScript) -> int:
    """Count total spoken words across all segments."""
    return sum(len(seg.dialogue.split()) for seg in script.segments)


EDITOR_SYSTEM_PROMPT = f"""\
You are the WanderAI Podcast Editor. You create engaging, conversational \
travel podcast scripts featuring two personas: a Photographer and a Historian.

Rules:
- Use ONLY the approved findings provided. Do not invent facts.
- Keep each persona's voice distinct and authentic.
- The Photographer speaks with visual, sensory language about light, color, and composition.
- The Historian provides context, stories, and cultural depth with measured authority.
- Avoid repetitive or fake banter.
- Create natural, flowing conversation — not monologues.
- Generate an episode title, chapters, and dialogue segments.
- Map each factual segment to its source finding IDs.
- Include an intro (at least 100 words) and outro (at least 100 words).
- Most dialogue turns should contain 60–120 words.
- Both personas must contribute meaningfully in every chapter.
- No one-line dialogue unless used sparingly for a natural transition.

IMPORTANT: Each segment's dialogue_type MUST be one of exactly these values: {_ALLOWED_DIALOGUE_TYPES}
Do NOT use any other dialogue_type value.

Recommended structure for an 8-minute episode:
1. Opening and destination setup — 1 minute
2. Arrival experience and visual identity — 1.5 minutes
3. Indigenous and historical context — 1.5 minutes
4. Photography viewpoints and lighting guidance — 1.5 minutes
5. Geology, stories, and notable events — 1.5 minutes
6. Practical traveler recap and closing — 1 minute"""


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
    targets = _compute_duration_targets(episode_minutes)

    user_prompt = f"""Create a podcast episode for:
- Destination: {destination_name}
- Region: {region or 'Not specified'}
- Target duration: {episode_minutes} minutes
- Personas: {', '.join(personas)}

DURATION REQUIREMENTS (critical):
- Target word count: {targets['target_word_count']} words
- Minimum word count: {targets['minimum_word_count']} words
- Preferred range: {targets['target_word_count']}–{targets['preferred_upper_word_count']} words
- Minimum chapters: {targets['min_chapters']}
- Minimum dialogue segments: {targets['min_segments']}
- Most dialogue turns should be 60–120 words
- Intro must be at least 100 words
- Outro must be at least 100 words

Approved findings to use:
{findings_text}"""

    if error_context:
        user_prompt += f"\n\nPREVIOUS ATTEMPT FAILED:\n{error_context}\n\nFix the issue. Use ONLY allowed dialogue_type values: {_ALLOWED_DIALOGUE_TYPES}"

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


async def _expand_script(
    script: PodcastScript,
    episode_minutes: int,
    personas: list[str],
    findings_text: str,
    missing_words: int,
) -> PodcastScript:
    """Expand an existing script to meet the minimum word count."""
    client = get_openai_client()
    targets = _compute_duration_targets(episode_minutes)

    script_json = json.dumps(script.model_dump(), indent=2, default=str)

    expansion_prompt = f"""The current script is {missing_words} words SHORT of the minimum requirement.

Current word count: {count_script_words(script)}
Required minimum: {targets['minimum_word_count']}
Target: {targets['target_word_count']}–{targets['preferred_upper_word_count']}

EXPAND the script by:
1. Adding richer explanations to existing segments
2. Adding meaningful follow-up questions between personas
3. Adding visual and historical context
4. Adding practical traveler guidance
5. Adding smooth chapter transitions
6. Adding callbacks to earlier details
7. Ensuring both personas contribute meaningfully in every chapter

Do NOT add:
- Empty filler or repeated facts
- Unsupported claims (use only the approved findings)
- Artificial silence or padding

CURRENT SCRIPT:
{script_json}

APPROVED FINDINGS (for reference):
{findings_text}

Output the expanded script. Minimum {targets['minimum_word_count']} words total dialogue. \
Use ONLY these dialogue_type values: {_ALLOWED_DIALOGUE_TYPES}"""

    response = await client.responses.parse(
        model="gpt-4o",
        input=[
            {"role": "system", "content": EDITOR_SYSTEM_PROMPT},
            {"role": "user", "content": expansion_prompt},
        ],
        text_format=PodcastScript,
    )

    parsed = response.output_parsed
    if parsed is None:
        raise ValueError("Script expansion returned no parseable output")

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
        return await _generate_script_structured(
            destination_name=destination_name,
            region=region,
            episode_minutes=episode_minutes,
            personas=personas,
            findings_text=findings_text,
            error_context=str(first_error),
        )


async def run_podcast_editor(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    approved_findings: list[ResearchFinding],
) -> PodcastScript:
    """Generate a podcast script that meets duration requirements.

    The editor has NO web access. It works exclusively with the
    findings that passed verification.

    If the script is below the preferred word count range, it will be
    expanded up to MAX_EXPANSION_RETRIES times.
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
    targets = _compute_duration_targets(episode_minutes)

    logger.info(
        "Generating podcast script",
        extra={
            "destination": destination_name,
            "findings_count": len(findings_dicts),
            "episode_minutes": episode_minutes,
            "target_words": targets["target_word_count"],
            "min_words": targets["minimum_word_count"],
        },
    )

    # Step 1: Generate initial script
    script = await _generate_script_with_retry(
        destination_name=destination_name,
        region=region,
        episode_minutes=episode_minutes,
        personas=personas,
        findings_text=findings_text,
    )

    word_count = count_script_words(script)
    logger.info(
        "Initial script generated",
        extra={
            "title": script.title,
            "segments": len(script.segments),
            "chapters": len(script.chapters),
            "word_count": word_count,
            "target": targets["target_word_count"],
        },
    )

    # Step 2: Expansion loop if below preferred range
    for attempt in range(MAX_EXPANSION_RETRIES):
        word_count = count_script_words(script)

        if word_count >= targets["target_word_count"]:
            logger.info("Script meets target word count", extra={"word_count": word_count})
            break

        missing = targets["target_word_count"] - word_count
        logger.info(
            "Script below target, expanding",
            extra={
                "attempt": attempt + 1,
                "word_count": word_count,
                "missing": missing,
            },
        )

        try:
            script = await _expand_script(
                script=script,
                episode_minutes=episode_minutes,
                personas=personas,
                findings_text=findings_text,
                missing_words=missing,
            )
        except Exception as e:
            logger.warning(
                "Script expansion failed",
                extra={"attempt": attempt + 1, "error": str(e)},
            )
            break

    final_word_count = count_script_words(script)
    logger.info(
        "Final script ready",
        extra={
            "title": script.title,
            "word_count": final_word_count,
            "segments": len(script.segments),
            "chapters": len(script.chapters),
            "meets_minimum": final_word_count >= targets["minimum_word_count"],
        },
    )

    return script
