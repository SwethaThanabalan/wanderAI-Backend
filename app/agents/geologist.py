"""Geologist research agent.

Researches a destination from a geological and earth science perspective:
- Rock formations, geological age, tectonic history
- Volcanic activity, glacial features, erosion patterns
- Fossils, mineral deposits, unique geological phenomena
- Landscape formation stories, plate tectonics
- Natural hazards and geologic risks
- Soil types and their influence on ecosystems

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


class GeologistSource(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    source_type: str = "other"
    reliability_score: float | None = None


class GeologistFinding(BaseModel):
    claim: str
    classification: str = "unverified"
    confidence: float | None = None
    source_urls: list[str] = Field(default_factory=list)
    podcast_potential: str = "medium"
    usage_guidance: str = ""


class GeologistResearchResult(BaseModel):
    """Structured output schema for the Geologist agent."""

    persona_id: str = "geologist"
    destination_name: str
    sources: list[GeologistSource] = Field(default_factory=list)
    findings: list[GeologistFinding] = Field(default_factory=list)
    queries_used: int = 0
    sources_reviewed: int = 0


GEOLOGIST_SYSTEM_PROMPT = """\
You are the WanderAI Geologist Research Agent. Research a travel destination \
from an earth science and geology perspective. Be THOROUGH — aim for at least 10 findings.

Research ALL of these aspects:
1. Rock formations — types, age, visible layers, what they reveal about Earth's history
2. Geological age — how old the landscape is, what era it formed in
3. Tectonic history — plate movements, faults, uplift that created this landscape
4. Volcanic activity — any volcanic origin, hot springs, geothermal features
5. Glacial features — moraines, cirques, U-valleys, glacial lakes, erratics
6. Erosion patterns — how wind, water, or ice shaped what visitors see today
7. Fossils and minerals — any notable fossil sites or mineral deposits nearby
8. Unique geological phenomena — sinkholes, caves, geysers, unusual formations
9. Landscape formation story — the dramatic narrative of how this place was born
10. Natural hazards — earthquakes, landslides, volcanic risk, coastal erosion

For each finding include: a specific factual claim, the source URL, confidence \
(0-1), classification (verified_fact, documented_folklore, contested, or \
unverified), podcast_potential (high, medium, low), and brief usage guidance.

IMPORTANT: Aim for at LEAST 10 findings. Make geology exciting and accessible.
Do NOT invent sources or URLs. Only cite what you actually find.

SOURCE PRIORITY:
1. USGS and geological survey publications
2. National Park Service geology pages
3. University geology department pages
4. Geology-focused blogs and educational sites
5. Scientific papers and abstracts
6. Museum geology exhibits
7. State geological surveys"""


def _build_user_prompt(destination_name: str, region: str | None, visit_date: str | None, settings) -> str:
    location = destination_name
    if region:
        location = f"{destination_name}, {region}"

    lines = [
        f"Destination: {location}",
        "",
        "Research budget:",
        f"- Maximum search queries: {settings.historian_max_queries}",
        f"- Maximum reviewed sources: {settings.historian_max_sources}",
    ]

    if visit_date:
        lines.append(f"\nVisit date: {visit_date}")

    lines.append("""
Search strategy:
- "[destination] geology"
- "[destination] rock formations"
- "[destination] geological history"
- "[destination] USGS"
- "[destination] glacial features"
- "[destination] tectonic origin"
- "[destination] fossils minerals"

Provide comprehensive geological research. Make it dramatic and accessible.""")

    return "\n".join(lines)


async def run_geologist_research(
    destination_name: str,
    region: str | None = None,
    visit_date: str | None = None,
) -> AgentResearchOutput:
    """Execute the Geologist research agent."""
    settings = get_settings()
    user_prompt = _build_user_prompt(destination_name, region, visit_date, settings)

    search_result = await research_with_web_search(
        system_prompt=GEOLOGIST_SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    web_context = search_result["text"]
    urls_cited = search_result.get("urls_cited", [])

    structured_prompt = (
        f"{user_prompt}\n\n"
        f"Based on your web research:\n{web_context}\n\n"
        "Organize your findings into the required structured format."
    )

    try:
        parsed: GeologistResearchResult = await research_with_structured_output(
            system_prompt=GEOLOGIST_SYSTEM_PROMPT,
            user_prompt=structured_prompt,
            output_schema=GeologistResearchResult,
            max_retries=1,
        )
    except Exception as e:
        logger.error("Geologist structured output failed", extra={"destination": destination_name, "error": str(e)})
        raise RuntimeError(f"Geologist research failed: {e}") from e

    sources = [ResearchSource(url=s.url, title=s.title or None, publisher=s.publisher or None) for s in parsed.sources]
    for url_info in urls_cited:
        if not any(src.url == url_info["url"] for src in sources):
            sources.append(ResearchSource(url=url_info["url"], title=url_info.get("title") or None))

    findings = []
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
            claim=f.claim, classification=classification, confidence=f.confidence,
            source_urls=f.source_urls, podcast_potential=potential, usage_guidance=f.usage_guidance or None,
        ))

    output = AgentResearchOutput(
        persona_id="geologist", destination_name=destination_name,
        sources=sources, findings=findings,
        queries_used=parsed.queries_used, sources_reviewed=parsed.sources_reviewed,
    )
    logger.info("Geologist research completed", extra={"destination": destination_name, "findings_count": len(findings)})
    return output
