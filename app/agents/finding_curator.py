"""Finding curator — deduplicates, ranks, and filters research findings.

Sits between the verification phase and the scripting phase. Its job is to
guarantee the narration is built from UNIQUE, HIGH-QUALITY, ON-TOPIC facts:

1. Deduplicate overlapping claims (multiple agents often discover the same fact)
2. Rank by relevance to the destination + podcast potential
3. Drop off-topic or low-value findings
4. Return a tight, curated set that the editor must use (and ONLY that set)

This directly addresses: repetition, off-topic drift, and generic filler.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.models.research import FindingClassification, PodcastPotential, ResearchFinding
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


CURATOR_SYSTEM_PROMPT = """\
You are the WanderAI Content Curator. You receive a pool of verified research findings
about a single travel destination. Multiple research agents produced these independently,
so there is overlap and some off-topic material.

Your job is to produce the DEFINITIVE curated set of findings for an audio narration.

STRICT RULES:

1. DEDUPLICATE — If two or more findings describe the same fact, place, event, or story,
   MERGE them into ONE finding. Keep the richest version, combine the source URLs.
   The output must contain NO two findings that cover the same core idea.

2. ON-TOPIC ONLY — Keep ONLY findings that are directly about THIS destination or its
   immediate area. Drop generic facts that could apply to any place, tangents about other
   regions, and background that doesn't help a traveler standing at this location.

3. RANK BY VALUE — Order findings from most to least compelling. Prioritize:
   - Specific, surprising, or vivid facts over generic ones
   - Named people, dates, events, and concrete details
   - Things a traveler can physically see or experience here
   - High podcast_potential findings
   Drop findings that are vague, obvious, or purely administrative unless practically useful.

4. PRESERVE SOURCES — Every kept finding must retain at least one source URL.
   When merging, union the source URLs.

5. PRESERVE CLASSIFICATION — Keep each finding's classification. Never upgrade folklore to fact.

Return valid JSON with this exact structure:
{
  "curated_findings": [
    {
      "claim": "the single, rich, deduplicated claim",
      "classification": "verified_fact|documented_folklore|contested|unverified",
      "confidence": 0.0-1.0,
      "source_urls": ["url1", "url2"],
      "podcast_potential": "high|medium|low",
      "usage_guidance": "how to use this in narration"
    }
  ],
  "dropped_count": <number of findings removed as duplicates or off-topic>,
  "merge_notes": ["brief note on any merges or drops"]
}

Aim for a LEAN set — quality over quantity. Better to have 10 unique, remarkable findings
than 25 overlapping or generic ones. Do NOT pad the list."""


def _findings_to_dicts(findings: list[ResearchFinding]) -> list[dict]:
    return [
        {
            "claim": f.claim,
            "classification": f.classification,
            "confidence": f.confidence,
            "source_urls": f.source_urls,
            "podcast_potential": f.podcast_potential,
            "usage_guidance": f.usage_guidance,
        }
        for f in findings
    ]


def _potential_rank(potential: str | None) -> int:
    """Rank podcast potential for sorting (higher = better)."""
    return {"high": 3, "medium": 2, "low": 1}.get((potential or "medium"), 2)


def _fallback_dedup(findings: list[ResearchFinding]) -> list[ResearchFinding]:
    """Deterministic fallback dedup if the LLM curator fails.

    Removes findings with near-identical claim text, keeps highest potential.
    """
    seen: dict[str, ResearchFinding] = {}

    # Sort so higher-potential findings are seen first and kept
    ordered = sorted(
        findings,
        key=lambda f: (_potential_rank(f.podcast_potential), f.confidence or 0),
        reverse=True,
    )

    for f in ordered:
        # Normalize claim for comparison: lowercase, first 60 chars of alphanumerics
        normalized = "".join(c for c in f.claim.lower() if c.isalnum())[:60]
        if normalized not in seen:
            seen[normalized] = f

    return list(seen.values())


async def curate_findings(
    destination_name: str,
    region: str | None,
    approved_findings: list[ResearchFinding],
) -> list[ResearchFinding]:
    """Deduplicate, rank, and filter findings into a tight curated set.

    Returns the curated findings. Falls back to deterministic dedup on failure.
    """
    if not approved_findings:
        return []

    # If the set is already small, still run dedup but skip if trivially tiny
    if len(approved_findings) <= 2:
        return approved_findings

    client = get_openai_client()
    location = f"{destination_name}, {region}" if region else destination_name
    findings_json = json.dumps(_findings_to_dicts(approved_findings), indent=2)

    user_prompt = f"""Destination: {location}

Curate the following {len(approved_findings)} verified findings into a tight, unique, on-topic set.
Remove ALL duplicates and off-topic material. Rank the rest by narrative value.

FINDINGS:
{findings_json}

Return the curated JSON."""

    try:
        response = await client.responses.create(
            model="gpt-4o",
            input=[
                {"role": "system", "content": CURATOR_SYSTEM_PROMPT},
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

        if "```json" in output_text:
            output_text = output_text.split("```json")[1].split("```")[0]
        elif "```" in output_text:
            output_text = output_text.split("```")[1].split("```")[0]

        data = json.loads(output_text)
        curated_raw = data.get("curated_findings", [])

        curated: list[ResearchFinding] = []
        for item in curated_raw:
            try:
                curated.append(ResearchFinding(
                    claim=item["claim"],
                    classification=item.get("classification", "unverified"),
                    confidence=item.get("confidence"),
                    source_urls=item.get("source_urls", []),
                    podcast_potential=item.get("podcast_potential", "medium"),
                    usage_guidance=item.get("usage_guidance"),
                ))
            except Exception:
                continue

        # Guard: if curation collapsed everything, fall back
        if not curated:
            logger.warning("Curator returned empty set, using fallback dedup")
            return _fallback_dedup(approved_findings)

        logger.info(
            "Findings curated",
            extra={
                "destination": destination_name,
                "input_count": len(approved_findings),
                "curated_count": len(curated),
                "dropped": data.get("dropped_count", len(approved_findings) - len(curated)),
            },
        )

        return curated

    except Exception as e:
        logger.warning(
            "Finding curation failed, using fallback dedup",
            extra={"error": str(e)},
        )
        return _fallback_dedup(approved_findings)
