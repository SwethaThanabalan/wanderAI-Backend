"""Photographer research agent.

Researches a destination from a visual and photography perspective:
- Visual identity, scenic viewpoints, lighting conditions
- Seasonal appearance, reflections, restrictions
- Tripod and drone rules, accessibility
- Details travelers may overlook
"""

import json
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.research import AgentResearchOutput, ResearchFinding, ResearchSource
from app.services.research_service import research_with_web_search

logger = get_logger(__name__)

PHOTOGRAPHER_SYSTEM_PROMPT = """You are the WanderAI Photographer Research Agent. Your job is to research
a travel destination from a photographer's perspective.

You MUST research the following aspects:
1. Visual identity - What makes this place visually distinctive?
2. Scenic viewpoints - Best locations for photography
3. Lighting conditions - Golden hour timing, harsh light areas, shadows
4. Seasonal appearance - How the destination looks in the visitor's travel season
5. Reflections - Water, glass, or other reflective surfaces
6. Photography restrictions - Where tripods, drones, or flash are banned
7. Tripod and drone rules - Specific regulations for the area
8. Accessible photography locations - Wheelchair-friendly viewpoints
9. Details travelers may overlook - Hidden photogenic spots

For each finding, provide:
- claim: A specific factual statement
- source_url: URL where you found this information
- source_title: Title of the source page
- publisher: Who published the source
- source_type: One of: official, academic, archival, museum, tribal, government, news, blog, travel, forum, other
- confidence: 0.0 to 1.0 confidence score
- classification: One of: verified_fact, documented_folklore, contested, unverified
- podcast_potential: high, medium, or low
- usage_guidance: Brief note on how to use this in the podcast

Output valid JSON with this structure:
{
  "persona_id": "photographer",
  "destination_name": "...",
  "sources": [{"url": "...", "title": "...", "publisher": "...", "source_type": "...", "reliability_score": 0.0-1.0}],
  "findings": [{"claim": "...", "classification": "...", "confidence": 0.0-1.0, "source_urls": ["..."], "podcast_potential": "high|medium|low", "usage_guidance": "..."}],
  "queries_used": N,
  "sources_reviewed": N
}

Research budget constraints:
- Maximum search queries: {max_queries}
- Maximum reviewed sources: {max_sources}
- Minimum official sources: {min_official}
- Maximum sources per domain: {max_per_domain}

Be thorough but respect the budget. Prefer official, government, and well-known travel sources.
Do NOT invent sources or URLs. Only cite what you actually find."""


async def run_photographer_research(
    destination_name: str,
    region: str | None = None,
    visit_date: str | None = None,
) -> AgentResearchOutput:
    """Execute the Photographer research agent.

    Researches the destination from a visual/photography perspective
    using live web search, respecting the configured research budget.
    """
    settings = get_settings()

    system_prompt = PHOTOGRAPHER_SYSTEM_PROMPT.format(
        max_queries=settings.photographer_max_queries,
        max_sources=settings.photographer_max_sources,
        min_official=settings.photographer_min_official_sources,
        max_per_domain=settings.photographer_max_per_domain,
    )

    location = destination_name
    if region:
        location = f"{destination_name}, {region}"

    season_note = f" The visitor plans to visit around {visit_date}." if visit_date else ""

    user_prompt = f"""Research the following destination for photography opportunities:

Destination: {location}{season_note}

Provide comprehensive photography research covering viewpoints, lighting, 
restrictions, seasonal appearance, and hidden details. Return structured JSON."""

    try:
        result = await research_with_web_search(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        # Parse the JSON response
        text = result["text"]
        # Extract JSON from response (handle markdown code blocks)
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        data = json.loads(text)

        # Build structured output
        sources = [
            ResearchSource(**s) for s in data.get("sources", [])
        ]
        findings = [
            ResearchFinding(**f) for f in data.get("findings", [])
        ]

        # Add any URLs from web search that weren't in the JSON
        for url_info in result.get("urls_cited", []):
            if not any(s.url == url_info["url"] for s in sources):
                sources.append(ResearchSource(
                    url=url_info["url"],
                    title=url_info.get("title", ""),
                ))

        output = AgentResearchOutput(
            persona_id="photographer",
            destination_name=destination_name,
            sources=sources,
            findings=findings,
            queries_used=data.get("queries_used", 0),
            sources_reviewed=data.get("sources_reviewed", 0),
        )

        logger.info(
            "Photographer research completed",
            extra={
                "destination": destination_name,
                "sources_count": len(sources),
                "findings_count": len(findings),
            },
        )

        return output

    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse photographer research output",
            extra={"error": str(e), "destination": destination_name},
        )
        # Return empty output rather than crashing the whole pipeline
        return AgentResearchOutput(
            persona_id="photographer",
            destination_name=destination_name,
        )
    except Exception as e:
        logger.error(
            "Photographer research failed",
            extra={"error": str(e), "destination": destination_name},
        )
        raise
