"""Script deduplicator — the final no-repetition gate for narration.

After the podcast editor produces a script, this agent:
1. Analyzes the full script for repeated facts, stories, phrases, or ideas
2. Removes or rewrites the repetitive segments
3. If cutting repetition drops the script below target, backfills with NEW
   content drawn from findings that haven't been fully used yet

This runs AFTER scripting and AFTER any expansion, so it is the last line of
defense before audio generation. Nothing repetitive should survive it.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.models.podcast import PodcastScript
from app.models.research import ResearchFinding
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)

_ALLOWED_DIALOGUE_TYPES = "observation, fact, story, question, response, transition, intro, outro, advice"


DEDUP_SYSTEM_PROMPT = f"""\
You are the WanderAI Repetition Auditor. You receive a finished podcast script and the
pool of approved research findings. Your single job: GUARANTEE the script contains NO
repetition, while keeping it rich and the right length.

DO THIS, IN ORDER:

STEP 1 — DETECT REPETITION
Scan every segment. Flag any case where the SAME fact, place, name, event, story, statistic,
or idea is stated more than once — even if worded differently. Also flag repeated phrases,
repeated adjectives, and repeated catchphrases.

STEP 2 — CUT THE REPETITION
For each repeated idea, keep the SINGLE best segment that expresses it (the most vivid,
specific, well-placed one). Remove or rewrite the others so the idea appears EXACTLY ONCE.
Rewrite rather than delete when the segment serves conversational flow but repeats content —
replace its repeated content with a genuinely new detail or a short natural reaction.

STEP 3 — BACKFILL IF SHORTENED
If cutting repetition drops the script below the target word count, add NEW segments that
cover approved findings NOT yet used (or under-used) in the script. Each backfill segment
must introduce genuinely new information — never reintroduce a cut idea.
Use the SAME personas/voices and conversational style already in the script.

HARD RULES:
- The final script must state each fact/story/idea EXACTLY ONCE.
- Never invent facts. Backfill only from the provided approved findings.
- Preserve the intro and outro (keep them brief).
- Keep speaker names lowercase: photographer, historian, geologist, foodie, storyteller.
- Every segment's dialogue_type MUST be one of: {_ALLOWED_DIALOGUE_TYPES}
- Preserve chapter structure; update chapter start/end segment ids if segments change.
- Keep the conversation natural — do not leave abrupt gaps where you cut content.

Return the COMPLETE corrected script in the required structured format."""


def _count_words(script: PodcastScript) -> int:
    return sum(len(seg.dialogue.split()) for seg in script.segments)


def _findings_to_text(findings: list[ResearchFinding]) -> str:
    return json.dumps(
        [
            {
                "claim": f.claim,
                "classification": f.classification,
                "confidence": f.confidence,
                "source_urls": f.source_urls,
                "podcast_potential": f.podcast_potential,
                "usage_guidance": f.usage_guidance,
            }
            for f in findings
        ],
        indent=2,
    )


async def deduplicate_script(
    script: PodcastScript,
    approved_findings: list[ResearchFinding],
    target_word_count: int,
    minimum_word_count: int,
) -> PodcastScript:
    """Audit the script for repetition, cut it, and backfill with new content.

    Returns the de-duplicated script. On failure, returns the original unchanged.
    """
    if not script.segments:
        return script

    client = get_openai_client()
    original_words = _count_words(script)
    script_json = json.dumps(script.model_dump(), indent=2, default=str)
    findings_text = _findings_to_text(approved_findings)

    user_prompt = f"""Audit this podcast script for repetition and fix it.

TARGET word count: {target_word_count}
MINIMUM word count: {minimum_word_count}
Current word count: {original_words}

INSTRUCTIONS:
1. Find and remove every repeated fact, story, idea, or phrase — each must appear ONCE.
2. If removing repetition drops you below the target, backfill with NEW segments that cover
   approved findings not yet used. Never reintroduce content you just cut.
3. Keep intro/outro brief, preserve chapters, keep the conversation natural.

CURRENT SCRIPT:
{script_json}

APPROVED FINDINGS (use these — and ONLY these — for any backfill):
{findings_text}

Return the complete corrected script. Use ONLY these dialogue_type values: {_ALLOWED_DIALOGUE_TYPES}"""

    try:
        response = await client.responses.parse(
            model="gpt-4o",
            input=[
                {"role": "system", "content": DEDUP_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            text_format=PodcastScript,
        )

        deduped = response.output_parsed
        if deduped is None or not deduped.segments:
            logger.warning("Deduplicator returned empty script, keeping original")
            return script

        new_words = _count_words(deduped)

        logger.info(
            "Script deduplicated",
            extra={
                "original_words": original_words,
                "deduped_words": new_words,
                "original_segments": len(script.segments),
                "deduped_segments": len(deduped.segments),
            },
        )

        # Safety guard: if the deduper collapsed the script far below the minimum,
        # the original is likely safer (dedup over-cut without backfilling).
        if new_words < minimum_word_count * 0.6 and original_words >= minimum_word_count:
            logger.warning(
                "Deduplicated script far too short, keeping original",
                extra={"deduped_words": new_words, "minimum": minimum_word_count},
            )
            return script

        return deduped

    except Exception as e:
        logger.warning(
            "Script deduplication failed, keeping original script",
            extra={"error": str(e)},
        )
        return script
