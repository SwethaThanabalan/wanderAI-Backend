"""Foodie research agent.

Researches a destination from a culinary and food culture perspective:
- Local specialties and signature dishes
- Best restaurants, markets, and food stalls
- Food history and cultural food traditions
- Seasonal ingredients and harvest calendar
- Street food and hidden gems
- Local beverages, breweries, wineries
- Food etiquette and customs

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


class FoodieSource(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    source_type: str = "other"
    reliability_score: float | None = None


class FoodieFinding(BaseModel):
    claim: str
    classification: str = "unverified"
    confidence: float | None = None
    source_urls: list[str] = Field(default_factory=list)
    podcast_potential: str = "medium"
    usage_guidance: str = ""


class FoodieResearchResult(BaseModel):
    """Structured output schema for the Foodie agent."""

    persona_id: str = "foodie"
    destination_name: str
    sources: list[FoodieSource] = Field(default_factory=list)
    findings: list[FoodieFinding] = Field(default_factory=list)
    queries_used: int = 0
    sources_reviewed: int = 0


FOODIE_SYSTEM_PROMPT = """\
You are the WanderAI Foodie Research Agent. Research a travel destination \
from a culinary and food culture perspective. Be THOROUGH — aim for at least 10 findings.

Research ALL of these aspects:
1. Signature local dishes — what THIS place is known for, not generic regional food
2. Best restaurants and eateries — specific names, what to order, local favorites
3. Markets and food halls — farmer's markets, fish markets, specialty shops
4. Street food and casual eats — food trucks, stands, grab-and-go spots
5. Food history — how the cuisine evolved, immigrant influences, indigenous foods
6. Seasonal ingredients — what's fresh and available during the visitor's trip
7. Local beverages — craft breweries, wineries, distilleries, coffee roasters
8. Hidden gems — places only locals know, no-sign restaurants, hole-in-the-wall spots
9. Food customs and etiquette — tipping, reservation culture, meal timing
10. Food experiences — cooking classes, food tours, farm visits

For each finding include: a specific factual claim, the source URL, confidence \
(0-1), classification (verified_fact, documented_folklore, contested, or \
unverified), podcast_potential (high, medium, low), and brief usage guidance.

IMPORTANT: Aim for at LEAST 10 findings. Be specific — name actual places and dishes.
Do NOT invent sources or URLs. Only cite what you actually find.

SOURCE PRIORITY:
1. Local food blogs and restaurant reviewers
2. Regional food magazines and publications
3. Tourism board food guides
4. Eater, Infatuation, or local food media
5. Restaurant websites and menus
6. TripAdvisor/Yelp notable mentions (for factual claims only)
7. Food festival and market websites"""


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
        lines.append(f"\nVisit date: {visit_date}. Focus on what's in season and available at that time.")

    lines.append("""
Search strategy:
- "[destination] best restaurants"
- "[destination] local food specialties"
- "[destination] food scene guide"
- "[destination] farmers market"
- "[destination] hidden gem restaurants"
- "[destination] craft brewery winery"
- "[destination] food history cuisine"
- "[destination] street food"

Be specific with restaurant names, dish names, and what makes them special.""")

    return "\n".join(lines)


async def run_foodie_research(
    destination_name: str,
    region: str | None = None,
    visit_date: str | None = None,
) -> AgentResearchOutput:
    """Execute the Foodie research agent."""
    settings = get_settings()
    user_prompt = _build_user_prompt(destination_name, region, visit_date, settings)

    search_result = await research_with_web_search(
        system_prompt=FOODIE_SYSTEM_PROMPT,
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
        parsed: FoodieResearchResult = await research_with_structured_output(
            system_prompt=FOODIE_SYSTEM_PROMPT,
            user_prompt=structured_prompt,
            output_schema=FoodieResearchResult,
            max_retries=1,
        )
    except Exception as e:
        logger.error("Foodie structured output failed", extra={"destination": destination_name, "error": str(e)})
        raise RuntimeError(f"Foodie research failed: {e}") from e

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
        persona_id="foodie", destination_name=destination_name,
        sources=sources, findings=findings,
        queries_used=parsed.queries_used, sources_reviewed=parsed.sources_reviewed,
    )
    logger.info("Foodie research completed", extra={"destination": destination_name, "findings_count": len(findings)})
    return output
