"""Trip builder — constructs wanderAI.trip format JSON from accepted suggestions.

The app sends all accepted trip_updates and context, and this service
assembles the complete trip document ready for import into the iOS app.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)


# --- Request / Response Models ---


class AcceptedStop(BaseModel):
    """A stop the user has accepted from chat suggestions."""

    name: str
    day: int = 1
    sequence: int | None = None
    time: str | None = None  # HH:mm
    duration_minutes: int | None = None
    category: str | None = None
    description: str | None = None
    highlights: list[str] = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None


class BuildTripRequest(BaseModel):
    """Request to build the final wanderAI.trip JSON."""

    name: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None    # YYYY-MM-DD
    days_count: int = Field(default=1, ge=1, le=30)
    accepted_stops: list[AcceptedStop] = Field(min_length=1)
    interests: list[str] = Field(default_factory=list)
    travelers: int | None = None


# --- Builder Logic ---


def build_trip_json(request: BuildTripRequest) -> dict[str, Any]:
    """Construct the full wanderAI.trip format JSON from accepted stops."""

    trip_id = str(uuid4())
    generated_at = datetime.now(tz=None).astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Determine dates
    start = None
    if request.start_date:
        try:
            start = date.fromisoformat(request.start_date)
        except ValueError:
            pass

    # Group stops by day
    days_map: dict[int, list[AcceptedStop]] = {}
    for stop in request.accepted_stops:
        day_num = stop.day
        if day_num not in days_map:
            days_map[day_num] = []
        days_map[day_num].append(stop)

    # Ensure we cover all days up to days_count
    for d in range(1, request.days_count + 1):
        if d not in days_map:
            days_map[d] = []

    # Build days array
    days = []
    for day_num in sorted(days_map.keys()):
        stops = days_map[day_num]

        # Sort stops by sequence or time
        stops.sort(key=lambda s: (s.sequence or 99, s.time or "99:99"))

        # Compute day date
        day_date = None
        if start:
            day_date = (start + timedelta(days=day_num - 1)).isoformat()

        # Build stop objects
        stop_objects = []
        for idx, stop in enumerate(stops, 1):
            stop_obj: dict[str, Any] = {
                "id": str(uuid4()),
                "sequence": stop.sequence or idx,
                "name": stop.name,
                "mapReference": {
                    "latitude": stop.latitude or 0.0,
                    "longitude": stop.longitude or 0.0,
                    "mapLabel": stop.name,
                    "pinStyle": "primary",
                    "formattedAddress": None,
                    "placeId": None,
                },
                "category": stop.category,
                "plannedTime": stop.time,
                "estimatedDurationMinutes": stop.duration_minutes,
                "summary": stop.description,
                "description": stop.description,
                "history": None,
                "heroImage": None,
                "mustDo": [],
                "highlights": stop.highlights,
                "suitability": {},
                "reviews": [],
                "travelerTips": [],
                "tags": [stop.category] if stop.category else [],
                "community": None,
            }
            stop_objects.append(stop_obj)

        day_obj: dict[str, Any] = {
            "id": str(uuid4()),
            "dayNumber": day_num,
            "date": day_date,
            "title": f"Day {day_num}",
            "summary": None,
            "plannedDistanceMiles": None,
            "estimatedDrivingMinutes": None,
            "stops": stop_objects,
            "routeHighlights": [],
        }
        days.append(day_obj)

    # Build full trip document
    trip_document: dict[str, Any] = {
        "format": "wanderAI.trip",
        "formatVersion": "1.0.0",
        "generatedAt": generated_at,
        "trip": {
            "id": trip_id,
            "name": request.name,
            "primaryDestination": request.destination,
            "summary": f"A {request.days_count}-day trip to {request.destination}",
            "startDate": request.start_date,
            "endDate": request.end_date,
            "timeZone": None,
            "coverImage": None,
            "suitability": {},
            "plannedDistanceMiles": None,
            "highlights": request.interests,
            "days": days,
            "community": None,
            "metadata": {
                "createdBy": "wanderai-chat",
                "travelers": request.travelers,
                "interests": request.interests,
            },
        },
    }

    logger.info(
        "Trip JSON built",
        extra={
            "trip_id": trip_id,
            "destination": request.destination,
            "days": len(days),
            "total_stops": sum(len(d["stops"]) for d in days),
        },
    )

    return trip_document
