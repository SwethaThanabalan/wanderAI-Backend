"""Photographer research agent.

Researches a destination from a visual and photography perspective:
- Visual identity, scenic viewpoints, lighting conditions
- Seasonal appearance, reflections, restrictions
- Tripod and drone rules, accessibility
- Details travelers may overlook

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


class PhotographerSource(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    source_type: str = "other"
    reliability_score: float | None = None


class PhotographerFinding(BaseModel):
    claim: str
    classification: str = "unverified"
    confidence: float | None = None
    source_urls: list[str] = Field(default_factory=list)
    podcast_potential: str = "medium"
    usage_guidance: str = ""


class PhotographerResearchResult(BaseModel):
    """Structured output schema for the Photographer agent."""

    persona_id: str = "photographer"
    destination_name: str
    sources: list[PhotographerSource] = Field(default_factory=list)
    findings: list[PhotographerFinding] = Field(default_factory=list)
    queries_used: int = 0
    sources_reviewed: int = 0


# --- Prompts (no JSON examples, no .format() on JSON content) ---


PHOTOGRAPHER_SYSTEM_PROMPT = """\
You are the WanderAI Photographer Research Agent. Research a travel destination \
from a photographer's perspective. Be THOROUGH — aim for at least 10-12 distinct findings.

Research ALL of these aspects (do not skip any):
1. Visual identity — what makes this place visually distinctive, unique colors, textures
2. Scenic viewpoints — at least 3-4 specific named locations for photography
3. Lighting conditions — golden hour timing, blue hour, harsh midday, best times of day
4. Seasonal appearance — how it looks in the visitor's travel season specifically
5. Reflections — water, glass, or other reflective surfaces and when they're best
6. Photography restrictions — tripods, drones, flash bans, permit requirements
7. Accessible photography locations — wheelchair-friendly viewpoints
8. Details travelers overlook — hidden angles, lesser-known spots, local secrets
9. Weather and atmosphere — fog, mist, rain, clouds and how they affect photos
10. Wildlife or nature subjects — animals, birds, plants unique to photograph here

For each finding include: a specific factual claim, the source URL, confidence \
(0-1), classification (verified_fact, documented_folklore, contested, or \
unverified), podcast_potential (high, medium, low), and brief usage guidance.

IMPORTANT: Aim for at LEAST 10 findings. Cover multiple aspects, not just 2-3.
Do NOT invent sources or URLs. Only cite what you actually find.

SOURCE PRIORITY (search for these types of sources specifically):
1. National Park Service / government park pages
2. Local tourism boards and visitor centers (e.g., county tourism, chamber of commerce)
3. Local photographers' blogs and guides specific to this area
4. Regional newspapers and magazines
5. Hiking and trail community sites (AllTrails, WTA, etc.)
6. Local photography groups and forums
7. Official park webcams and conditions pages
8. Travel photographers who have written about this specific location
9. Reddit threads and trip reports from visitors
10. Local outdoor recreation guides"""


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
        f"- Maximum search queries: {settings.photographer_max_queries}",
        f"- Maximum reviewed sources: {settings.photographer_max_sources}",
        f"- Minimum official sources: {settings.photographer_min_official_sources}",
        f"- Maximum sources per domain: {settings.photographer_max_per_domain}",
    ]

    if visit_date:
        lines.append(f"\nThe visitor plans to visit around {visit_date}. Focus on what this place looks like in that specific season.")

    lines.append("""
Search strategy — use DIVERSE queries like:
- "[destination] photography guide"
- "[destination] best viewpoints"
- "[destination] [season] conditions"
- "[destination] local tips photography"
- "[destination] visitor center recommendations"
- "[destination] drone rules tripod policy"
- "[destination] hidden spots locals know"
- "[destination] sunrise sunset times [month]"

Look for LOCAL sources: regional tourism sites, local photographer blogs, park ranger tips, trail community posts, and visitor trip reports. These give the best insider information.""")

    return "\n".join(lines)


async def run_photographer_research(
    destination_name: str,
    region: str | None = None,
    visit_date: str | None = None,
) -> AgentResearchOutput:
    """Execute the Photographer research agent.

    Uses structured output for reliable parsing. Raises on failure
    instead of returning empty results.
    """
    settings = get_settings()

    user_prompt = _build_user_prompt(destination_name, region, visit_date, settings)

    # Step 1: Web search to gather live URLs
    search_result = await research_with_web_search(
        system_prompt=PHOTOGRAPHER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    # Step 2: Parse the result using structured output with retry
    # Build a follow-up prompt that includes web search findings
    web_context = search_result["text"]
    urls_cited = search_result.get("urls_cited", [])

    structured_prompt = (
        f"{user_prompt}\n\n"
        f"Based on your web research, here is what you found:\n{web_context}\n\n"
        "Now organize your findings into the required structured format."
    )

    try:
        parsed: PhotographerResearchResult = await research_with_structured_output(
            system_prompt=PHOTOGRAPHER_SYSTEM_PROMPT,
            user_prompt=structured_prompt,
            output_schema=PhotographerResearchResult,
            max_retries=1,
        )
    except Exception as e:
        logger.error(
            "Photographer structured output failed",
            extra={"destination": destination_name, "error": str(e)},
        )
        raise RuntimeError(f"Photographer research failed: {e}") from e

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
        persona_id="photographer",
        destination_name=destination_name,
        sources=sources,
        findings=findings,
        queries_used=parsed.queries_used,
        sources_reviewed=parsed.sources_reviewed,
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
