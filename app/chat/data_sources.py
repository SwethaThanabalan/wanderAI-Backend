"""Per-persona data source search.

Each persona has its own curated set of domains and search strategies.
When a persona mentions or suggests a place, this module searches
persona-specific sources to enrich the response with real data.

Uses OpenAI's web_search_preview tool scoped to each persona's domain expertise.
"""

from __future__ import annotations

from typing import Any

from app.chat.personas import PERSONA_CONFIGS
from app.core.logging import get_logger
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


def _build_search_queries(
    persona_id: str,
    place_name: str,
    destination: str | None = None,
) -> list[str]:
    """Build persona-specific search queries for a place.

    Each persona searches for different aspects of the same location.
    """
    config = PERSONA_CONFIGS.get(persona_id)
    if not config:
        return [f"{place_name} travel guide"]

    location = f"{place_name} {destination}" if destination else place_name

    # Build queries based on persona's search keywords
    queries = []
    for keyword in config.search_keywords[:4]:
        queries.append(f"{location} {keyword}")

    # Always add a general query with preferred domains
    if config.search_domains:
        top_domain = config.search_domains[0]
        queries.append(f"site:{top_domain} {location}")

    return queries[:5]  # Cap at 5 queries


def _build_search_system_prompt(persona_id: str, place_name: str) -> str:
    """Build a system prompt for the persona-specific search task."""
    config = PERSONA_CONFIGS.get(persona_id)
    if not config:
        return f"Research {place_name} for travel planning."

    source_list = "\n".join(f"- {src}" for src in config.data_sources)

    return f"""\
You are researching "{place_name}" from the perspective of a {config.display_name}.

YOUR DATA SOURCES (prioritize these):
{source_list}

YOUR FOCUS AREAS:
Search for information relevant to: {', '.join(config.search_keywords[:6])}

OUTPUT FORMAT:
Return a JSON object with the following structure:
{{
    "place_name": "exact place name",
    "description": "2-3 sentence description from THIS persona's angle",
    "category": "one of: hiking, dining, viewpoint, museum, landmark, nature, nightlife, market, beach, park, historic_site, other",
    "latitude": float or null,
    "longitude": float or null,
    "highlights": ["list", "of", "3-5", "highlights"],
    "rating": float or null (out of 5),
    "price_level": "$ or $$ or $$$ or $$$$ or null",
    "address": "street address if found or null",
    "source_url": "primary source URL",
    "best_time": "best time to visit if relevant",
    "insider_tip": "one insider tip from your research"
}}

Be factual. Only include data you actually find. Use null for unknown fields.
Do NOT invent coordinates or ratings. Only include them if found in sources."""


async def search_persona_sources(
    persona_id: str,
    place_name: str,
    destination: str | None = None,
) -> dict[str, Any] | None:
    """Search a persona's specific data sources for information about a place.

    Returns enriched location data or None if search fails/finds nothing useful.
    """
    client = get_openai_client()
    config = PERSONA_CONFIGS.get(persona_id)

    if not config:
        logger.warning(f"No persona config found for: {persona_id}")
        return None

    system_prompt = _build_search_system_prompt(persona_id, place_name)

    # Build the user prompt with search context
    location_context = f"{place_name}"
    if destination:
        location_context = f"{place_name} in/near {destination}"

    user_prompt = (
        f"Research this place: {location_context}\n\n"
        f"Search these types of sources specifically:\n"
        + "\n".join(f"- {domain}" for domain in config.search_domains[:5])
        + f"\n\nFocus on: {', '.join(config.search_keywords[:5])}"
        + "\n\nReturn the JSON object with your findings."
    )

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await client.responses.create(
            model="gpt-4o",
            tools=[{"type": "web_search_preview"}],
            input=messages,
        )

        output_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text

        if not output_text:
            return None

        # Try to parse JSON from the response
        import json

        # Handle markdown code block wrapping
        text = output_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            result = json.loads(text)
            logger.info(
                "Persona source search completed",
                extra={
                    "persona": persona_id,
                    "place": place_name,
                    "has_coordinates": result.get("latitude") is not None,
                },
            )
            return result
        except json.JSONDecodeError:
            # If we can't parse JSON, try to extract it from mixed content
            import re
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    return result
                except json.JSONDecodeError:
                    pass

            logger.warning(
                "Could not parse search result as JSON",
                extra={"persona": persona_id, "place": place_name},
            )
            return None

    except Exception as e:
        logger.error(
            "Persona source search failed",
            extra={"persona": persona_id, "place": place_name, "error": str(e)},
        )
        return None


async def enrich_location_cards(
    persona_id: str,
    place_names: list[str],
    destination: str | None = None,
) -> list[dict[str, Any]]:
    """Search persona sources for multiple places and return enriched location data.

    Searches in parallel for efficiency.
    """
    import asyncio

    if not place_names:
        return []

    # Cap at 3 places per message to avoid rate limits and latency
    places_to_search = place_names[:3]

    tasks = [
        search_persona_sources(persona_id, place, destination)
        for place in places_to_search
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(
                "Location enrichment failed for place",
                extra={"place": places_to_search[i], "error": str(result)},
            )
            # Return a basic card without enrichment
            enriched.append({
                "place_name": places_to_search[i],
                "description": None,
                "category": None,
                "latitude": None,
                "longitude": None,
                "highlights": [],
            })
        elif result is not None:
            enriched.append(result)
        else:
            # Search returned nothing — still include basic info
            enriched.append({
                "place_name": places_to_search[i],
                "description": None,
                "category": None,
                "latitude": None,
                "longitude": None,
                "highlights": [],
            })

    return enriched
