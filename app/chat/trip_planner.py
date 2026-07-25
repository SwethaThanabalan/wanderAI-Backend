"""Trip plan generator.

Takes accepted stops from a chat session, organizes them by proximity
and number of days, fetches detailed information and images for each stop
via web search, and produces a complete wanderAI.trip format document.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.chat.models import PlanStop, TripContext, UserPreferences
from app.core.logging import get_logger
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


# --- Proximity / Clustering ---


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two coordinates in kilometers."""
    R = 6371.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _cluster_stops_by_proximity(
    stops: list[dict[str, Any]],
    num_days: int,
) -> list[list[dict[str, Any]]]:
    """Cluster stops into day groups based on geographical proximity.

    Uses a greedy nearest-neighbor approach: start from the first stop,
    assign nearby stops to the same day until we've distributed evenly.
    """
    if not stops or num_days < 1:
        return [stops] if stops else []

    # Separate stops with and without coordinates
    geo_stops = [s for s in stops if s.get("latitude") and s.get("longitude")]
    no_geo_stops = [s for s in stops if not s.get("latitude") or not s.get("longitude")]

    if not geo_stops:
        # No coordinates — distribute evenly
        per_day = max(1, len(stops) // num_days)
        days = []
        for i in range(0, len(stops), per_day):
            days.append(stops[i:i + per_day])
        # Merge overflow into last day
        while len(days) > num_days:
            days[-2].extend(days.pop())
        return days

    # Greedy nearest-neighbor clustering
    remaining = list(geo_stops)
    days: list[list[dict[str, Any]]] = []
    stops_per_day = max(1, len(geo_stops) // num_days)

    while remaining and len(days) < num_days:
        if not days:
            # Start with the first stop
            current = remaining.pop(0)
        else:
            current = remaining.pop(0)

        day_cluster = [current]

        # Fill this day's cluster with nearest neighbors
        target_size = stops_per_day if len(days) < num_days - 1 else len(remaining) + 1
        while remaining and len(day_cluster) < target_size:
            last = day_cluster[-1]
            last_lat = last.get("latitude", 0)
            last_lon = last.get("longitude", 0)

            # Find nearest remaining stop
            nearest_idx = 0
            nearest_dist = float("inf")
            for i, s in enumerate(remaining):
                dist = _haversine_km(last_lat, last_lon, s.get("latitude", 0), s.get("longitude", 0))
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_idx = i

            day_cluster.append(remaining.pop(nearest_idx))

        days.append(day_cluster)

    # Any remaining go to the last day
    if remaining:
        if days:
            days[-1].extend(remaining)
        else:
            days.append(remaining)

    # Distribute no-geo stops across days evenly
    for i, stop in enumerate(no_geo_stops):
        day_idx = i % len(days)
        days[day_idx].append(stop)

    return days


def _estimate_driving_minutes(stops: list[dict[str, Any]]) -> int:
    """Rough estimate of total driving time between sequential stops in a day."""
    total_km = 0.0
    for i in range(len(stops) - 1):
        lat1 = stops[i].get("latitude") or 0
        lon1 = stops[i].get("longitude") or 0
        lat2 = stops[i + 1].get("latitude") or 0
        lon2 = stops[i + 1].get("longitude") or 0
        if lat1 and lon1 and lat2 and lon2:
            total_km += _haversine_km(lat1, lon1, lat2, lon2)
    # Assume average 50 km/h for scenic/mixed driving
    return int((total_km / 50) * 60) if total_km > 0 else 0


def _estimate_distance_miles(stops: list[dict[str, Any]]) -> float:
    """Estimate total distance in miles between sequential stops."""
    total_km = 0.0
    for i in range(len(stops) - 1):
        lat1 = stops[i].get("latitude") or 0
        lon1 = stops[i].get("longitude") or 0
        lat2 = stops[i + 1].get("latitude") or 0
        lon2 = stops[i + 1].get("longitude") or 0
        if lat1 and lon1 and lat2 and lon2:
            total_km += _haversine_km(lat1, lon1, lat2, lon2)
    return round(total_km * 0.621371, 1)


# --- Stop Detail Enrichment ---


async def _enrich_stop_details(stop: dict[str, Any], destination: str | None) -> dict[str, Any]:
    """Fetch detailed information and images for a single stop via web search.

    Returns an enriched stop dict with description, history, images, tips, etc.
    """
    client = get_openai_client()
    place_name = stop.get("name", "Unknown")
    location_context = f"{place_name} in {destination}" if destination else place_name

    system_prompt = """\
You are a travel research assistant. Given a specific place/stop on a trip, research it thoroughly
and return detailed information in JSON format.

Return EXACTLY this JSON structure (no markdown, no explanation, just JSON):
{
    "name": "exact place name",
    "summary": "1-2 sentence overview",
    "description": "2-3 paragraph rich description of the place, what makes it special, what to expect",
    "history": "brief historical background if relevant, null if not a historical site",
    "category": "city|nature|hiking|dining|viewpoint|museum|landmark|beach|park|historic_site|scenicDrive|other",
    "heroImage": {"type": "url", "value": "URL to a good representative photo", "altText": "description of image"},
    "mustDo": ["top thing to do #1", "top thing #2", "top thing #3"],
    "highlights": ["highlight 1", "highlight 2", "highlight 3", "highlight 4"],
    "travelerTips": [
        {"text": "practical tip for visitors", "tags": ["timing", "parking", "gear", "food", "budget"]}
    ],
    "tags": ["relevant", "tags", "for", "this", "place"],
    "estimatedDurationMinutes": 90,
    "mapReference": {
        "latitude": 0.0,
        "longitude": 0.0,
        "formattedAddress": "full address if available",
        "placeId": null,
        "mapLabel": "short label for map pin",
        "pinStyle": "primary"
    },
    "suitability": {
        "dogFriendly": {"status": "yes|no|partial", "details": "brief note"},
        "kidFriendly": {"status": "yes|no|partial", "details": "brief note"},
        "wheelchairAccessible": {"status": "yes|no|partial", "details": "brief note"}
    },
    "community": {"aggregateRating": 4.5, "reviewCount": 100, "popularityLabel": "Popular", "sourceLabel": "based on visitor reviews"}
}

IMPORTANT:
- Use real coordinates — look them up accurately
- Find a real, publicly accessible image URL (from wikimedia, NPS, tourism sites, etc.)
- Be factual — only include information you can verify
- If a field is unknown, use null (don't invent data)"""

    user_prompt = f"Research this travel stop thoroughly: {location_context}"

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
            return stop

        # Parse JSON response
        import json
        text = output_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            enriched = json.loads(text)
            # Merge with existing stop data (preserve any data already present)
            for key, value in enriched.items():
                if value is not None:
                    stop[key] = value
            return stop
        except json.JSONDecodeError:
            logger.warning("Could not parse enrichment JSON", extra={"place": place_name})
            return stop

    except Exception as e:
        logger.error("Stop enrichment failed", extra={"place": place_name, "error": str(e)})
        return stop


async def enrich_all_stops(
    stops: list[dict[str, Any]],
    destination: str | None,
) -> list[dict[str, Any]]:
    """Enrich all stops in parallel (batched to avoid rate limits)."""
    batch_size = 5
    enriched = []

    for i in range(0, len(stops), batch_size):
        batch = stops[i:i + batch_size]
        tasks = [_enrich_stop_details(stop, destination) for stop in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for j, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("Enrichment failed", extra={"stop": batch[j].get("name"), "error": str(result)})
                enriched.append(batch[j])
            else:
                enriched.append(result)

    return enriched


# --- Main Plan Generator ---


async def generate_trip_plan(
    accepted_stops: list[dict[str, Any]],
    trip_context: TripContext | None = None,
    current_plan: list[PlanStop] | None = None,
    user_preferences: UserPreferences | None = None,
) -> dict[str, Any]:
    """Generate a complete wanderAI.trip format plan from accepted stops.

    1. Determines number of days from trip_context
    2. Clusters stops by proximity into day groups
    3. Enriches each stop with detailed info and images via web search
    4. Assembles the full trip document with proper sequencing
    """
    destination = trip_context.destination if trip_context else "Unknown"
    region = trip_context.region if trip_context else None

    # Determine number of days
    num_days = 1
    start_date_str = trip_context.start_date if trip_context else None
    end_date_str = trip_context.end_date if trip_context else None

    start_dt: date | None = None
    end_dt: date | None = None

    if start_date_str:
        try:
            start_dt = date.fromisoformat(start_date_str)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_dt = date.fromisoformat(end_date_str)
        except ValueError:
            pass

    if start_dt and end_dt:
        num_days = max(1, (end_dt - start_dt).days + 1)
    elif current_plan:
        # Infer from plan's max day
        max_day = max((s.day or 1) for s in current_plan)
        num_days = max(1, max_day)

    # Merge accepted_stops with current_plan coordinates if available
    all_stops = list(accepted_stops)

    # Cluster stops by proximity into days
    day_clusters = _cluster_stops_by_proximity(all_stops, num_days)

    # Enrich all stops with detailed info and images
    flat_stops = [stop for day in day_clusters for stop in day]
    enriched_stops = await enrich_all_stops(flat_stops, destination)

    # Re-assign enriched stops back to day clusters
    idx = 0
    enriched_days: list[list[dict[str, Any]]] = []
    for day_cluster in day_clusters:
        day_enriched = []
        for _ in day_cluster:
            if idx < len(enriched_stops):
                day_enriched.append(enriched_stops[idx])
            idx += 1
        enriched_days.append(day_enriched)

    # Generate day titles using AI
    day_titles = await _generate_day_titles(enriched_days, destination)

    # Assemble the trip document
    trip_id = str(uuid4())
    generated_at = datetime.now(tz=None).astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Build days array
    days = []
    total_distance = 0.0
    for day_num, day_stops in enumerate(enriched_days, 1):
        day_date = None
        if start_dt:
            day_date = (start_dt + timedelta(days=day_num - 1)).isoformat()

        # Build stop objects
        stop_objects = []
        for seq, stop in enumerate(day_stops, 1):
            stop_obj = _build_stop_object(stop, seq)
            stop_objects.append(stop_obj)

        day_distance = _estimate_distance_miles(day_stops)
        day_driving = _estimate_driving_minutes(day_stops)
        total_distance += day_distance

        day_title = day_titles[day_num - 1] if day_num - 1 < len(day_titles) else f"Day {day_num}"
        day_summary = _generate_day_summary(day_stops)

        day_obj = {
            "id": str(uuid4()),
            "dayNumber": day_num,
            "date": day_date,
            "title": day_title,
            "summary": day_summary,
            "plannedDistanceMiles": day_distance,
            "estimatedDrivingMinutes": day_driving,
            "stops": stop_objects,
            "routeHighlights": [],
        }
        days.append(day_obj)

    # Build highlights from top stops
    highlights = []
    for day_stops in enriched_days:
        for stop in day_stops[:2]:
            highlights.append(stop.get("name", ""))
    highlights = highlights[:8]

    # Build trip summary
    trip_summary = f"A {num_days}-day adventure exploring {destination}"
    if region:
        trip_summary += f", {region}"

    # Determine suitability from enriched data
    suitability = _aggregate_suitability(enriched_stops)

    # Full trip document
    trip_document = {
        "format": "wanderAI.trip",
        "formatVersion": "1.0.0",
        "generatedAt": generated_at,
        "generator": {"type": "chat", "name": "WanderAI Chat", "version": "1.0"},
        "trip": {
            "id": trip_id,
            "name": f"{destination} Adventure" if destination else "My Trip",
            "summary": trip_summary,
            "primaryDestination": f"{destination}, {region}" if region else destination,
            "startDate": start_date_str,
            "endDate": end_date_str,
            "timeZone": None,
            "coverImage": _get_cover_image(enriched_stops),
            "suitability": suitability,
            "plannedDistanceMiles": round(total_distance, 1),
            "highlights": highlights,
            "days": days,
            "community": None,
            "metadata": {
                "createdBy": "wanderai-chat",
                "travelers": trip_context.travelers if trip_context else None,
                "interests": trip_context.interests if trip_context else [],
                "preferences": user_preferences.model_dump() if user_preferences else None,
            },
        },
    }

    logger.info(
        "Trip plan generated",
        extra={
            "trip_id": trip_id,
            "destination": destination,
            "days": len(days),
            "total_stops": sum(len(d["stops"]) for d in days),
            "total_distance_miles": round(total_distance, 1),
        },
    )

    return trip_document


def _build_stop_object(stop: dict[str, Any], sequence: int) -> dict[str, Any]:
    """Build a single stop object in the wanderAI.trip format."""
    map_ref = stop.get("mapReference", {})
    if not map_ref:
        map_ref = {
            "latitude": stop.get("latitude") or 0.0,
            "longitude": stop.get("longitude") or 0.0,
            "formattedAddress": stop.get("address"),
            "placeId": None,
            "mapLabel": stop.get("name", ""),
            "pinStyle": "primary",
        }

    hero_image = stop.get("heroImage")
    if not hero_image and stop.get("image_url"):
        hero_image = {
            "type": "url",
            "value": stop["image_url"],
            "altText": f"Photo of {stop.get('name', '')}",
        }

    # Build traveler tips
    raw_tips = stop.get("travelerTips", [])
    tips = []
    for i, tip in enumerate(raw_tips):
        if isinstance(tip, dict):
            tips.append({
                "id": str(uuid4()),
                "authorDisplayName": "WanderAI",
                "authorType": "ai",
                "text": tip.get("text", ""),
                "visitSeason": None,
                "helpfulCount": 0,
                "tags": tip.get("tags", []),
            })
        elif isinstance(tip, str):
            tips.append({
                "id": str(uuid4()),
                "authorDisplayName": "WanderAI",
                "authorType": "ai",
                "text": tip,
                "visitSeason": None,
                "helpfulCount": 0,
                "tags": [],
            })

    return {
        "id": str(uuid4()),
        "sequence": sequence,
        "name": stop.get("name", "Unknown"),
        "category": stop.get("category", "other"),
        "plannedTime": stop.get("time") or stop.get("plannedTime"),
        "estimatedDurationMinutes": stop.get("estimatedDurationMinutes") or stop.get("duration_minutes") or 60,
        "summary": stop.get("summary"),
        "description": stop.get("description"),
        "history": stop.get("history"),
        "mapReference": map_ref,
        "heroImage": hero_image,
        "mustDo": stop.get("mustDo", []),
        "highlights": stop.get("highlights", []),
        "suitability": stop.get("suitability", {}),
        "reviews": [],
        "travelerTips": tips,
        "tags": stop.get("tags", []),
        "community": stop.get("community"),
    }


def _generate_day_summary(stops: list[dict[str, Any]]) -> str:
    """Generate a brief summary of a day's stops."""
    names = [s.get("name", "") for s in stops if s.get("name")]
    if not names:
        return "A day of exploration"
    if len(names) == 1:
        return f"Exploring {names[0]}"
    if len(names) == 2:
        return f"From {names[0]} to {names[1]}"
    return f"From {names[0]} through {names[1]} to {names[-1]}"


async def _generate_day_titles(
    day_clusters: list[list[dict[str, Any]]],
    destination: str | None,
) -> list[str]:
    """Generate descriptive titles for each day using the stop names."""
    titles = []
    for i, stops in enumerate(day_clusters, 1):
        names = [s.get("name", "") for s in stops if s.get("name")]
        if not names:
            titles.append(f"Day {i}")
        elif len(names) == 1:
            titles.append(f"{names[0]}")
        else:
            # Use first and last as bookends
            titles.append(f"{names[0]} to {names[-1]}")
    return titles


def _get_cover_image(stops: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Get the best image from enriched stops to use as trip cover."""
    for stop in stops:
        hero = stop.get("heroImage")
        if hero and hero.get("value"):
            return hero
        if stop.get("image_url"):
            return {
                "type": "url",
                "value": stop["image_url"],
                "altText": f"Cover: {stop.get('name', '')}",
            }
    return None


def _aggregate_suitability(stops: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate suitability info from all stops."""
    # Default to unknown if no data
    result: dict[str, Any] = {}
    categories = ["dogFriendly", "kidFriendly", "wheelchairAccessible"]

    for cat in categories:
        statuses = []
        for stop in stops:
            suit = stop.get("suitability", {})
            if isinstance(suit, dict) and cat in suit:
                entry = suit[cat]
                if isinstance(entry, dict):
                    statuses.append(entry.get("status", "unknown"))

        if statuses:
            # If any say "no", the trip is "partial"
            if "no" in statuses:
                result[cat] = {"status": "partial", "details": "Some stops may not be suitable"}
            elif "partial" in statuses:
                result[cat] = {"status": "partial", "details": "Some stops have limited access"}
            else:
                result[cat] = {"status": "yes", "details": "All stops are suitable"}

    return result
