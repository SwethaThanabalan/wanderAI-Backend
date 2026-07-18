"""Historian research agent.

Researches a destination from a historical and cultural perspective:
- Indigenous history and place-name origins
- Settlement history, major events, architecture
- Local industries, documented folklore
- Contested interpretations

Must:
- Distinguish verified history from folklore
- Avoid invented quotations
- Prefer tribal, archival, museum, government, and academic sources
- Not refer to living communities only in the past tense
"""

import json
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.research import AgentResearchOutput, ResearchFinding, ResearchSource
from app.services.research_service import research_with_web_search

logger = get_logger(__name__)

HISTORIAN_SYSTEM_PROMPT = """You are the WanderAI Historian Research Agent. Your job is to research
a travel destination from a historical and cultural perspective.

You MUST research the following aspects:
1. Indigenous history - Original peoples, their relationship with this place
2. Place-name origins - Etymology, original names in indigenous languages
3. Settlement history - Key dates, founders, development
4. Major events - Significant historical events at this location
5. Architecture - Notable structures, their age, style, and history
6. Local industries - Historical and current economic activities
7. Documented folklore - Legends, myths, and oral traditions with CLEAR labeling
8. Contested interpretations - Where historians disagree

Critical rules:
- ALWAYS distinguish verified history from folklore. Label folklore clearly.
- NEVER invent quotations or attribute fake quotes to historical figures.
- Prefer tribal, archival, museum, government, and academic sources.
- NEVER refer to living communities only in the past tense.
- Include confidence levels and source citations for every claim.
- Note when information comes from oral tradition vs. written records.

For each finding, provide:
- claim: A specific factual or documented statement
- source_url: URL where you found this information
- source_title: Title of the source page
- publisher: Who published the source
- source_type: One of: official, academic, archival, museum, tribal, government, news, blog, travel, forum, other
- confidence: 0.0 to 1.0 confidence score
- classification: One of: verified_fact, documented_folklore, contested, unverified
- podcast_potential: high, medium, or low
- usage_guidance: Brief note on how to use this in the podcast

Output valid JSON with this structure:
{{
  "persona_id": "historian",
  "destination_name": "...",
  "sources": [{{"url": "...", "title": "...", "publisher": "...", "source_type": "...", "reliability_score": 0.0-1.0}}],
  "findings": [{{"claim": "...", "classification": "...", "confidence": 0.0-1.0, "source_urls": ["..."], "podcast_potential": "high|medium|low", "usage_guidance": "..."}}],
  "queries_used": N,
  "sources_reviewed": N
}}

Research budget constraints:
- Maximum search queries: {max_queries}
- Maximum reviewed sources: {max_sources}
- Minimum official/archival/museum/academic/tribal sources: {min_official}
- Maximum sources per domain: {max_per_domain}

Be thorough but respect the budget. Prioritize authoritative historical sources.
Do NOT invent sources or URLs. Only cite what you actually find."""


async def run_historian_research(
    destination_name: str,
    region: str | None = None,
    visit_date: str | None = None,
) -> AgentResearchOutput:
    """Execute the Historian research agent.

    Researches the destination from a historical and cultural perspective
    using live web search, respecting the configured research budget.
    """
    settings = get_settings()

    system_prompt = HISTORIAN_SYSTEM_PROMPT.format(
        max_queries=settings.historian_max_queries,
        max_sources=settings.historian_max_sources,
        min_official=settings.historian_min_official_sources,
        max_per_domain=settings.historian_max_per_domain,
    )

    location = destination_name
    if region:
        location = f"{destination_name}, {region}"

    user_prompt = f"""Research the following destination for its history and cultural significance:

Destination: {location}

Provide comprehensive historical research covering indigenous history, place-name origins,
settlement history, major events, architecture, local industries, documented folklore,
and any contested interpretations. Return structured JSON."""

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
            persona_id="historian",
            destination_name=destination_name,
            sources=sources,
            findings=findings,
            queries_used=data.get("queries_used", 0),
            sources_reviewed=data.get("sources_reviewed", 0),
        )

        logger.info(
            "Historian research completed",
            extra={
                "destination": destination_name,
                "sources_count": len(sources),
                "findings_count": len(findings),
            },
        )

        return output

    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse historian research output",
            extra={"error": str(e), "destination": destination_name},
        )
        return AgentResearchOutput(
            persona_id="historian",
            destination_name=destination_name,
        )
    except Exception as e:
        logger.error(
            "Historian research failed",
            extra={"error": str(e), "destination": destination_name},
        )
        raise
