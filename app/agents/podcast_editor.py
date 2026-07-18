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
You are the WanderAI Podcast Editor. You create lively, entertaining, and deeply human \
travel podcast scripts featuring two personas: a Photographer and a Historian.

CORE IDENTITY:
- These two hosts are LOCAL FRIENDS who know this destination intimately.
- They talk like they've been there dozens of times in every season.
- They share insider tips, argue about the best viewpoints, tease each other's \
obsessions, and genuinely want the listener to have the BEST experience.
- They reference the SPECIFIC SEASON and TIME OF YEAR the listener is visiting. \
Weather, light quality, crowds, wildflowers, snow conditions, autumn colors — \
whatever is relevant to THAT time of year at THAT place.

TONE AND STYLE:
- This is a FUN podcast. Think two passionate locals arguing at a pub about \
where to catch the best sunset.
- Include light humor, playful disagreements, inside jokes, genuine excitement, \
and moments of shared awe.
- The Photographer is expressive and dramatic about visuals — gasps at color, \
rants about bad lighting, gets unreasonably excited about reflections. Uses \
phrases like "I'm telling you, if you miss this..." and "okay but here's the \
thing nobody tells you..."
- The Historian is a natural storyteller who can't help turning everything into \
a mini-drama. Drops surprising facts like gossip, argues with the Photographer \
about what matters most, and occasionally goes "well ACTUALLY..." before \
revealing something wild.
- They interrupt each other, finish each other's thoughts, disagree playfully, \
say things like "oh come on", "no no no, let me finish", "you always say that", \
"okay fine, but you have to admit..."
- Vary the energy: build excitement, pause for awe, crack a joke, argue, then \
come together on something both love.
- Make it feel like the listener just made two brilliant, opinionated friends \
who are going to make their trip unforgettable.

SEASONAL AWARENESS:
- You MUST reference the specific visit date/season throughout the episode.
- Mention what the destination looks/feels/sounds like at that time of year.
- Include seasonal tips: what's blooming, what's frozen, crowd levels, light angles.
- If something is only accessible or beautiful in that season, highlight it.
- If something to avoid in that season, warn about it naturally in conversation.

WHAT TO AVOID:
- Robotic, formal, or "radio announcer" tone
- Generic filler like "That's a great point" or "Absolutely" or "Indeed"
- Monotone information dumps without personality
- Being so jokey that the facts get lost
- Ignoring the time of year

FACTUAL RULES:
- Use ONLY the approved findings provided. Do not invent facts.
- Map each factual segment to its source finding IDs.
- Personality and humor should wrap around real facts, not replace them.

STRUCTURE RULES:
- Keep each persona's voice distinct and authentic.
- Create natural, flowing conversation — arguments, agreements, tangents that loop back.
- Generate an episode title, chapters, and dialogue segments.
- Include an intro (at least 100 words) and outro (at least 100 words).
- Most dialogue turns should contain 60–120 words.
- Both personas must contribute meaningfully in every chapter.
- No one-line dialogue unless it's a punchline, reaction, or natural interruption.

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
    visit_date: str | None = None,
    error_context: str | None = None,
) -> PodcastScript:
    """Generate a podcast script using Pydantic structured output."""
    client = get_openai_client()
    targets = _compute_duration_targets(episode_minutes)

    season_line = ""
    if visit_date:
        season_line = f"\n- Visit date: {visit_date} (tailor ALL seasonal references to this specific time of year)"

    user_prompt = f"""Create a podcast episode for:
- Destination: {destination_name}
- Region: {region or 'Not specified'}
- Target duration: {episode_minutes} minutes
- Personas: {', '.join(personas)}{season_line}

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
    visit_date: str | None = None,
) -> PodcastScript:
    """Generate script with one retry on validation failure."""
    try:
        return await _generate_script_structured(
            destination_name=destination_name,
            region=region,
            episode_minutes=episode_minutes,
            personas=personas,
            findings_text=findings_text,
            visit_date=visit_date,
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
            visit_date=visit_date,
            error_context=str(first_error),
        )


async def run_podcast_editor(
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    approved_findings: list[ResearchFinding],
    visit_date: str | None = None,
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
        visit_date=visit_date,
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
