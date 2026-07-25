"""Conversation persistence service.

Stores and retrieves AI chat conversation state in Supabase so that:
- The server can restore context by conversation_id without the client resending everything
- Conversations survive app restarts and session expiry
- The frontend can sync its local state with the backend's source of truth
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.services.supabase_service import get_supabase

logger = get_logger(__name__)

TABLE = "chat_conversations"


def create_conversation(
    session_id: str,
    destination: str | None = None,
    state: str | None = None,
    country: str | None = None,
    region: str | None = None,
    trip_id: str | None = None,
    trip_name: str | None = None,
    trip_dates: dict | None = None,
    current_stop_id: str | None = None,
    current_stop_name: str | None = None,
    selected_persona_ids: list[str] | None = None,
    traveler_preferences: dict | None = None,
    user_preferences: dict | None = None,
    collected_places: list[str] | None = None,
    itinerary_summary: list[str] | None = None,
    current_plan: list[dict] | None = None,
    user_id: str | None = None,
) -> dict:
    """Create a new conversation record and return it."""
    client = get_supabase()

    data: dict[str, Any] = {
        "session_id": session_id,
        "destination": destination,
        "state": state,
        "country": country,
        "region": region,
        "trip_name": trip_name,
        "trip_dates": trip_dates,
        "current_stop_id": current_stop_id,
        "current_stop_name": current_stop_name,
        "selected_persona_ids": selected_persona_ids or [],
        "traveler_preferences": traveler_preferences,
        "user_preferences": user_preferences,
        "collected_places": collected_places or [],
        "itinerary_summary": itinerary_summary or [],
        "current_plan": current_plan or [],
        "accepted_stops": [],
        "recent_messages": [],
        "message_count": 0,
        "status": "active",
    }

    if trip_id:
        data["trip_id"] = trip_id
    if user_id:
        data["user_id"] = user_id

    response = client.table(TABLE).insert(data).execute()

    if not response.data:
        raise RuntimeError("Failed to create conversation record")

    record = response.data[0]
    logger.info(
        "Conversation created",
        extra={"conversation_id": record["id"], "session_id": session_id, "destination": destination},
    )
    return record


def get_conversation(conversation_id: str) -> dict | None:
    """Fetch a conversation by ID. Returns None if not found or archived."""
    client = get_supabase()

    response = (
        client.table(TABLE)
        .select("*")
        .eq("id", conversation_id)
        .neq("status", "archived")
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


def get_conversation_by_session(session_id: str) -> dict | None:
    """Fetch the active conversation for a session_id."""
    client = get_supabase()

    response = (
        client.table(TABLE)
        .select("*")
        .eq("session_id", session_id)
        .eq("status", "active")
        .order("last_active_at", desc=True)
        .limit(1)
        .execute()
    )

    if not response.data:
        return None

    return response.data[0]


def get_conversations_for_trip(trip_id: str, limit: int = 10) -> list[dict]:
    """Fetch recent conversations for a trip."""
    client = get_supabase()

    response = (
        client.table(TABLE)
        .select("*")
        .eq("trip_id", trip_id)
        .eq("status", "active")
        .order("last_active_at", desc=True)
        .limit(limit)
        .execute()
    )

    return response.data or []


def update_conversation_context(
    conversation_id: str,
    destination: str | None = None,
    state: str | None = None,
    country: str | None = None,
    current_stop_id: str | None = None,
    current_stop_name: str | None = None,
    selected_persona_ids: list[str] | None = None,
    collected_places: list[str] | None = None,
    itinerary_summary: list[str] | None = None,
) -> dict | None:
    """Update the grounding context fields of a conversation."""
    client = get_supabase()

    data: dict[str, Any] = {
        "last_active_at": datetime.now(timezone.utc).isoformat(),
    }

    if destination is not None:
        data["destination"] = destination
    if state is not None:
        data["state"] = state
    if country is not None:
        data["country"] = country
    if current_stop_id is not None:
        data["current_stop_id"] = current_stop_id
    if current_stop_name is not None:
        data["current_stop_name"] = current_stop_name
    if selected_persona_ids is not None:
        data["selected_persona_ids"] = selected_persona_ids
    if collected_places is not None:
        data["collected_places"] = collected_places
    if itinerary_summary is not None:
        data["itinerary_summary"] = itinerary_summary

    response = (
        client.table(TABLE)
        .update(data)
        .eq("id", conversation_id)
        .execute()
    )

    return response.data[0] if response.data else None


def append_messages(
    conversation_id: str,
    new_messages: list[dict],
    max_recent: int = 20,
) -> None:
    """Append messages to the conversation's recent_messages.

    Keeps only the most recent `max_recent` messages to avoid bloating.
    """
    client = get_supabase()

    # Fetch current messages
    response = (
        client.table(TABLE)
        .select("recent_messages, message_count")
        .eq("id", conversation_id)
        .execute()
    )

    if not response.data:
        return

    current = response.data[0]
    messages = current.get("recent_messages") or []
    messages.extend(new_messages)

    # Trim to max_recent
    if len(messages) > max_recent:
        messages = messages[-max_recent:]

    count = (current.get("message_count") or 0) + len(new_messages)

    client.table(TABLE).update({
        "recent_messages": messages,
        "message_count": count,
        "last_active_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", conversation_id).execute()


def update_accepted_stops(
    conversation_id: str,
    accepted_stops: list[dict],
) -> None:
    """Update the accepted stops list."""
    client = get_supabase()

    client.table(TABLE).update({
        "accepted_stops": accepted_stops,
        "last_active_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", conversation_id).execute()


def update_collected_places(
    conversation_id: str,
    collected_places: list[str],
) -> None:
    """Update the collected places list."""
    client = get_supabase()

    client.table(TABLE).update({
        "collected_places": collected_places,
        "last_active_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", conversation_id).execute()


def mark_conversation_stale(conversation_id: str) -> None:
    """Mark a conversation as stale (e.g., trip was deleted)."""
    client = get_supabase()

    client.table(TABLE).update({
        "status": "stale",
        "last_active_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", conversation_id).execute()

    logger.info("Conversation marked stale", extra={"conversation_id": conversation_id})


def archive_conversation(conversation_id: str) -> None:
    """Archive a conversation (soft delete)."""
    client = get_supabase()

    client.table(TABLE).update({
        "status": "archived",
        "last_active_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", conversation_id).execute()

    logger.info("Conversation archived", extra={"conversation_id": conversation_id})
