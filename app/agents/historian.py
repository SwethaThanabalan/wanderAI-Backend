"""Historian research agent.

Researches a destination from a historical and cultural perspective:
- Indigenous history and place-name origins
- Settlement history, major events, architecture
- Local industries, documented folklore
- Contested interpretations

Uses OpenAI structured outputs for reliable JSON parsing.
"""

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.research import (
    AgentResearchOutput,
    FindingClassification,
    PodcastPotential,
    ResearchFinding,
    ResearchSource,
    SourceType,
)
from app.services.research_service import research_with_structured_output, research_with_web_search

logger = get_logger(__name__)


# --- Pydantic schema for structured output ---


class HistorianSource(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    source_type: str = "other"
    reliability_score: float | None = None


class HistorianFinding(BaseModel):
    claim: str
    classification: str = "unverified"
    confidence: float | None = None
    source_urls: list[str] = Field(default_factory=list)
    podcast_potential: str = "medium"
    usage_guidance: str = ""


class HistorianResearchResult(BaseModel):
    """Structured output schema for the Historian agent."""

    persona_id: str = "historian"
    destination_name: str
    sources: list[HistorianSource] = Field(default_factory=list)
    findings: list[HistorianFinding] = Field(default_factory=list)
    queries_used: int = 0
    sources_reviewed: int = 0


# --- Prompts (no JSON examples, no .format() on JSON content) ---


HISTORIAN_SYSTEM_PROMPT = """\
You are the WanderAI Historian Research Agent. Research a travel destination \
from a historical and cultural perspective. Be THOROUGH — aim for at least 12-15 distinct findings.

Research ALL of these aspects (do not skip any):
1. Indigenous history — original peoples, their relationship with this place, tribal names
2. Place-name origins — etymology, original names in indigenous languages, name changes
3. Settlement history — key dates, founders, development timeline
4. Major events — significant historical events, turning points, disasters
5. Architecture — notable structures, their age, style, builders, and stories
6. Local industries — historical and current economic activities, how they shaped the area
7. Documented folklore — legends, myths, oral traditions (CLEARLY LABELED as folklore)
8. Contested interpretations — where historians disagree, multiple perspectives
9. Notable people — famous visitors, residents, or figures connected to this place
10. Cultural traditions — ongoing practices, festivals, ceremonies tied to the location
11. Environmental history — how the landscape changed over time, conservation efforts
12. Surprising or little-known facts — the kind that make people say "wait, really?"

Critical rules:
- ALWAYS distinguish verified history from folklore. Label folklore clearly.
- NEVER invent quotations or attribute fake quotes to historical figures.
- Prefer tribal, archival, museum, government, and academic sources.
- NEVER refer to living communities only in the past tense.
- Include confidence levels and source citations for every claim.
- Note when information comes from oral tradition vs. written records.

IMPORTANT: Aim for at LEAST 12 findings. Cover multiple aspects, not just 3-4.
For each finding include: a specific factual claim, the source URL, confidence \
(0-1), classification (verified_fact, documented_folklore, contested, or \
unverified), podcast_potential (high, medium, low), and brief usage guidance.

SOURCE PRIORITY (search for these types of sources specifically):
1. Tribal nation websites and cultural centers
2. National Park Service / government interpretive pages
3. County and regional historical societies
4. Local museums and archives
5. University research and academic papers about the area
6. Regional newspapers (historical articles, anniversary pieces)
7. Oral history projects and community interviews
8. Local heritage and preservation organizations
9. State archives and historical records
10. Documentary films and podcast episodes about the area

Do NOT invent sources or URLs. Only cite what you actually find."""


def _build_user_prompt(
    destination_name: str,
    region: str | None,
    visit_date: str | None,
    settings,
) -> str:
    location = destination_name
    if region:
        location = f"{destination_name}, {region}"

    lines = [
        f"Destination: {location}",
        "",
        "Research budget:",
        f"- Maximum search queries: {settings.historian_max_queries}",
        f"- Maximum reviewed sources: {settings.historian_max_sources}",
        f"- Minimum official/archival/museum/academic/tribal sources: {settings.historian_min_official_sources}",
        f"- Maximum sources per domain: {settings.historian_max_per_domain}",
    ]

    if visit_date:
        lines.append(f"\nThe visitor plans to visit around {visit_date}. Include any seasonal events, festivals, or historical commemorations near that time.")

    lines.append("""
Search strategy — use DIVERSE queries like:
- "[destination] history"
- "[destination] indigenous peoples tribe"
- "[destination] place name origin etymology"
- "[destination] historical events timeline"
- "[destination] local legends folklore"
- "[destination] museum archives"
- "[destination] notable people famous visitors"
- "[destination] conservation history"
- "[destination] cultural traditions ceremonies"
- "[destination] local newspaper history"

Look for LOCAL sources: tribal websites, county historical societies, regional museums, local newspaper archives, university research, park interpretive materials, oral history projects, and community heritage sites. These provide the authentic local perspective that makes a podcast feel like insider knowledge.""")

    return "\n".join(lines)


async def run_historian_research(
    destination_name: str,
    region: str | None = None,
    visit_date: str | None = None,
) -> AgentResearchOutput:
    """Execute the Historian research agent.

    Uses structured output for reliable parsing. Raises on failure
    instead of returning empty results.
    """
    settings = get_settings()

    user_prompt = _build_user_prompt(destination_name, region, visit_date, settings)

    # Step 1: Web search to gather live URLs
    search_result = await research_with_web_search(
        system_prompt=HISTORIAN_SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    # Step 2: Parse the result using structured output with retry
    web_context = search_result["text"]
    urls_cited = search_result.get("urls_cited", [])

    structured_prompt = (
        f"{user_prompt}\n\n"
        f"Based on your web research, here is what you found:\n{web_context}\n\n"
        "Now organize your findings into the required structured format."
    )

    try:
        parsed: HistorianResearchResult = await research_with_structured_output(
            system_prompt=HISTORIAN_SYSTEM_PROMPT,
            user_prompt=structured_prompt,
            output_schema=HistorianResearchResult,
            max_retries=1,
        )
    except Exception as e:
        logger.error(
            "Historian structured output failed",
            extra={"destination": destination_name, "error": str(e)},
        )
        raise RuntimeError(f"Historian research failed: {e}") from e

    # Convert to domain models
    sources: list[ResearchSource] = []
    for s in parsed.sources:
        try:
            source_type = SourceType(s.source_type) if s.source_type in SourceType.__members__.values() else SourceType.OTHER
        except ValueError:
            source_type = SourceType.OTHER
        sources.append(ResearchSource(
            url=s.url,
            title=s.title or None,
            publisher=s.publisher or None,
            source_type=source_type,
            reliability_score=s.reliability_score,
        ))

    # Also persist URLs cited during web search
    for url_info in urls_cited:
        if not any(src.url == url_info["url"] for src in sources):
            sources.append(ResearchSource(
                url=url_info["url"],
                title=url_info.get("title") or None,
            ))

    findings: list[ResearchFinding] = []
    for f in parsed.findings:
        try:
            classification = FindingClassification(f.classification)
        except ValueError:
            classification = FindingClassification.UNVERIFIED
        try:
            potential = PodcastPotential(f.podcast_potential)
        except ValueError:
            potential = PodcastPotential.MEDIUM

        findings.append(ResearchFinding(
            claim=f.claim,
            classification=classification,
            confidence=f.confidence,
            source_urls=f.source_urls,
            podcast_potential=potential,
            usage_guidance=f.usage_guidance or None,
        ))

    output = AgentResearchOutput(
        persona_id="historian",
        destination_name=destination_name,
        sources=sources,
        findings=findings,
        queries_used=parsed.queries_used,
        sources_reviewed=parsed.sources_reviewed,
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
