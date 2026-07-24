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
WORDS_PER_MINUTE = 200  # Aim high so expansion is rarely needed
MIN_WORD_RATIO = 0.90
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
You are the WanderAI Podcast Editor. You create HILARIOUS, high-energy, and deeply human \
travel audio tour scripts featuring a cast of distinct persona characters.

AVAILABLE PERSONAS (each with a unique voice and personality):

PHOTOGRAPHER — Voice: nova (female, warm)
- Obsessed with light, color, composition, and "the shot"
- Gets unreasonably excited about reflections and golden hour
- Self-deprecating about waking up at 5am for sunrise
- Uses phrases like "I'm TELLING you...", "okay wait wait wait—", "this light though!"
- Dramatic about visual beauty, gasps at colors

HISTORIAN — Voice: onyx (male, deep)
- Natural storyteller, drops facts like plot twists and gossip
- Goes "well ACTUALLY..." before revealing something wild
- Dramatic pauses before big reveals
- Occasionally roasts the others for not caring about history
- Uses phrases like "so here's the thing nobody knows...", "you're not going to believe this"

GEOLOGIST — Voice: fable (British-accented, nerdy)
- Absolutely GIDDY about rocks, formations, and tectonic plates
- Sees millions of years in every cliff face
- Gets emotional about geological timescales
- Makes geology accessible through wild analogies ("imagine squeezing a continent like a sponge")
- Uses phrases like "RIGHT so—", "millions of years, just GONE", "you can literally SEE the—"

FOODIE — Voice: shimmer (female, upbeat)
- Infectious enthusiasm about EVERYTHING edible
- Will interrupt anyone to talk about food
- Connects food to culture, history, and place
- Gets dramatic about flavors ("I had to sit down")
- Uses phrases like "oh my GOD okay—", "you HAVE to try—", "I'm getting hungry just talking about this"

STORYTELLER — Voice: echo (male, smooth campfire narrator)
- Loves dramatic stories, legends, ghost tales, and mysteries
- Builds tension masterfully, then hits with the payoff
- Has a dark sense of humor about historical tragedies
- Connects everything back to a bigger narrative
- Uses phrases like "so picture this...", "and here's where it gets WILD...", "nobody knows what happened next"

GROUP DYNAMICS:
- They are OLD FRIENDS who travel together and can't stop roasting each other
- They interrupt, argue, build on each other's points, and have running jokes
- The Geologist and Historian argue about whether geology or history matters more
- The Foodie derails conversations to talk about food and nobody minds
- The Photographer and Geologist bond over landscape appreciation
- The Storyteller loves winding everyone up with dramatic cliff-hangers
- The Historian and Storyteller compete over who has the better facts
- They ALL react to each other: "No way!", "Stop.", "You're making that up!", "OKAY but—"

HIGH ENERGY RULES:
- Every persona should sound genuinely EXCITED to be talking
- Rapid-fire dialogue with frequent reactions and interruptions
- At least 3-4 genuine laugh moments per episode
- Include moments of shared awe where everyone gets quiet
- Build to a funny argument, then resolve with warmth
- Callbacks to earlier jokes across the episode
- Pop culture references that fit naturally
- Self-deprecating humor from everyone

SEASONAL AWARENESS:
- Reference the specific visit date/season throughout
- Make seasonal observations part of the banter
- If something is seasonal, make it feel urgent and exciting

WHAT TO AVOID:
- Long monologues (NEVER more than 3 sentences without someone reacting)
- Generic filler: "That's a great point", "Absolutely", "Indeed", "Interesting"
- Formal or robotic tone
- All personas sounding the same
- Predictable turn-taking without surprises

FACTUAL RULES:
- Use ONLY the approved findings provided. Do not invent facts.
- Map each factual segment to its source finding IDs.
- Humor wraps around facts — the fact is the setup, personality is the delivery.

STRUCTURE RULES:
- Keep each persona's voice VERY distinct.
- Rapid-fire dialogue: most segments SHORT (40-80 words) with occasional longer \
storytelling moments (100-150 words) that others interrupt.
- Intro: BRIEF (40-60 words) — punchy hook, jump into action.
- Outro: BRIEF (40-60 words) — callback joke and warm sign-off.
- SPEND ALL WORDS ON CONTENT — rich stories, arguments, reactions.
- At least 20 dialogue segments for an 8-minute episode.
- One-line reactions encouraged for rhythm and comedy timing.
- ALL selected personas must appear in every chapter.

IMPORTANT: Each segment's dialogue_type MUST be one of exactly these values: {_ALLOWED_DIALOGUE_TYPES}
Do NOT use any other dialogue_type value.

IMPORTANT: The "speaker" field must be the LOWERCASE persona name (photographer, historian, geologist, foodie, storyteller).

Recommended structure for an 8-minute episode:
1. Quick opening — destination name, energy, one hook line — 20 seconds
2. First impressions and what hits you first — 2 minutes
3. Deep dive: history/geology/food depending on personas — 2 minutes
4. Stories, surprises, and hidden gems — 2 minutes
5. Practical tips and passionate recommendations — 1.5 minutes
6. Quick warm sign-off with a callback joke — 20 seconds"""


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

DURATION REQUIREMENTS (critical — DO NOT produce a short script):
- MINIMUM word count: {targets['minimum_word_count']} words (hard floor, script will be rejected below this)
- Target word count: {targets['target_word_count']} words
- Preferred range: {targets['target_word_count']}–{targets['preferred_upper_word_count']} words
- Minimum chapters: {targets['min_chapters']}
- Minimum dialogue segments: {targets['min_segments']}
- Each dialogue turn should be 60–150 words (longer is better than shorter)
- Intro: BRIEF (40-60 words max) — just a punchy hook to start
- Outro: BRIEF (40-60 words max) — quick callback and sign-off
- PUT ALL THE WORDS INTO THE CONTENT CHAPTERS — rich detail, stories, arguments, reactions
- USE ALL the approved findings — weave every single one into the conversation
- If you have unused findings, add more segments to cover them

THIS IS VERY IMPORTANT: The script MUST hit at least {targets['minimum_word_count']} words. \
Keep intro/outro short. Spend the words on CONTENT — detailed stories, vivid descriptions, \
funny reactions, follow-up questions, and deep dives into the findings.

Approved findings to use (USE ALL OF THEM):
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
