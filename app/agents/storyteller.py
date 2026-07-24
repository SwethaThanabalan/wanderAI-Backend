"""Storyteller research agent.

Researches a destination for its most compelling human stories:
- Legends, myths, and ghost stories
- Famous visitors and their experiences
- Dramatic historical events
- Local characters and eccentrics
- Love stories, tragedies, and triumphs
- Unsolved mysteries and conspiracy theories
- Origin stories of local traditions

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


class StorytellerSource(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    source_type: str = "other"
    reliability_score: float | None = None


class StorytellerFinding(BaseModel):
    claim: str
    classification: str = "unverified"
    confidence: float | None = None
    source_urls: list[str] = Field(default_factory=list)
    podcast_potential: str = "medium"
    usage_guidance: str = ""


class StorytellerResearchResult(BaseModel):
    """Structured output schema for the Storyteller agent."""

    persona_id: str = "storyteller"
    destination_name: str
    sources: list[StorytellerSource] = Field(default_factory=list)
    findings: list[StorytellerFinding] = Field(default_factory=list)
    queries_used: int = 0
    sources_reviewed: int = 0


STORYTELLER_SYSTEM_PROMPT = """\
You are the WanderAI Storyteller Research Agent. Research a travel destination \
for its most COMPELLING, dramatic, and entertaining human stories. Be THOROUGH — aim for 10+ findings.

Research ALL of these aspects:
1. Local legends and myths — the stories locals tell around campfires
2. Ghost stories and hauntings — any documented paranormal claims (labeled as folklore)
3. Famous visitors — celebrities, authors, presidents who came here and what happened
4. Dramatic historical events — shipwrecks, rescues, discoveries, escapes
5. Local characters and eccentrics — colorful personalities from the area's past
6. Love stories and tragedies — romantic or heartbreaking true stories tied to this place
7. Unsolved mysteries — disappearances, unexplained events, conspiracy theories
8. Origin stories — how local traditions, festivals, or customs began
9. Record-breakers — world records, firsts, or extreme achievements at this location
10. Pop culture connections — movies filmed here, songs written about it, books set here

CRITICAL RULES:
- ALWAYS label folklore, legends, and ghost stories as "documented_folklore"
- Distinguish between verified historical events and entertaining legends
- Stories should be DRAMATIC and ENTERTAINING — the kind that make listeners gasp
- Include the narrative arc: setup, tension, payoff

For each finding include: a specific story or claim, the source URL, confidence \
(0-1), classification (verified_fact, documented_folklore, contested, or \
unverified), podcast_potential (high, medium, low), and brief usage guidance.

IMPORTANT: Aim for at LEAST 10 findings. Prioritize stories with narrative drama.
Do NOT invent sources or URLs. Only cite what you actually find.

SOURCE PRIORITY:
1. Local history books and archives
2. Regional folklore collections
3. Newspaper archives (dramatic events)
4. Atlas Obscura and unusual-places sites
5. Ghost tour and paranormal investigation sites (for folklore only)
6. Local tourism storytelling pages
7. Documentary and podcast transcripts"""


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
- "[destination] legends myths stories"
- "[destination] ghost stories haunted"
- "[destination] famous visitors history"
- "[destination] unsolved mystery"
- "[destination] dramatic history events"
- "[destination] local folklore"
- "[destination] atlas obscura"
- "[destination] movies filmed"

Find the most DRAMATIC and ENTERTAINING stories. The kind that make people say "no way, really?".""")

    return "\n".join(lines)


async def run_storyteller_research(
    destination_name: str,
    region: str | None = None,
    visit_date: str | None = None,
) -> AgentResearchOutput:
    """Execute the Storyteller research agent."""
    settings = get_settings()
    user_prompt = _build_user_prompt(destination_name, region, visit_date, settings)

    search_result = await research_with_web_search(
        system_prompt=STORYTELLER_SYSTEM_PROMPT,
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
        parsed: StorytellerResearchResult = await research_with_structured_output(
            system_prompt=STORYTELLER_SYSTEM_PROMPT,
            user_prompt=structured_prompt,
            output_schema=StorytellerResearchResult,
            max_retries=1,
        )
    except Exception as e:
        logger.error("Storyteller structured output failed", extra={"destination": destination_name, "error": str(e)})
        raise RuntimeError(f"Storyteller research failed: {e}") from e

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
        persona_id="storyteller", destination_name=destination_name,
        sources=sources, findings=findings,
        queries_used=parsed.queries_used, sources_reviewed=parsed.sources_reviewed,
    )
    logger.info("Storyteller research completed", extra={"destination": destination_name, "findings_count": len(findings)})
    return output
