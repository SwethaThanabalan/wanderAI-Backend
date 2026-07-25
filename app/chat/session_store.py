"""In-memory chat session store with conversation context grounding.

Maintains conversation history, trip context, user preferences, current plan,
and structured conversation context per session.

Sessions expire after 2 hours of inactivity.
Each message gets a single persona response, rotated round-robin.
The ConversationContext anchors all follow-up questions to the active destination.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from app.chat.models import (
    ChatMessage,
    ChatPersona,
    ChatRole,
    ContextUpdates,
    ConversationContext,
    LocationCard,
    PlanStop,
    ResolvedContext,
    TripContext,
    TripUpdate,
    UserPreferences,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# Sessions expire after 2 hours
SESSION_TTL_SECONDS = 2 * 60 * 60


class ChatSession:
    """A single chat session with conversation context grounding."""

    def __init__(
        self,
        session_id: str,
        personas: list[ChatPersona],
        trip_context: TripContext | None = None,
        current_plan: list[PlanStop] | None = None,
        user_preferences: UserPreferences | None = None,
        conversation_context: ConversationContext | None = None,
        conversation_id: str | None = None,
    ):
        self.session_id = session_id
        self.conversation_id = conversation_id or str(uuid4())
        self.personas = personas
        self.trip_context = trip_context
        self.current_plan: list[PlanStop] = current_plan or []
        self.user_preferences = user_preferences
        self.messages: list[ChatMessage] = []
        self.accepted_stops: list[dict] = []
        self.created_at = time.time()
        self.last_active = time.time()

        # Conversation context — the grounding source of truth
        self.conversation_context = conversation_context or ConversationContext()

        # Sync context from trip_context if conversation_context is empty
        if trip_context and not self.conversation_context.destination:
            self._sync_context_from_trip(trip_context)

        # Persona rotation
        self._persona_index = 0
        self.last_responding_persona: ChatPersona | None = None
        self.last_persona_reply: str | None = None

    def _sync_context_from_trip(self, trip_context: TripContext) -> None:
        """Initialize conversation context from TripContext if not already set."""
        if trip_context.destination:
            self.conversation_context.destination = trip_context.destination
        if trip_context.region:
            self.conversation_context.state = trip_context.region
        if trip_context.trip_id:
            self.conversation_context.trip_id = trip_context.trip_id
        if trip_context.existing_stops:
            self.conversation_context.collected_places = list(trip_context.existing_stops)
        if trip_context.interests:
            self.conversation_context.itinerary_summary = list(trip_context.interests)
        # Sync persona IDs from session personas
        self.conversation_context.selected_persona_ids = [p.value for p in self.personas]

    def get_next_persona(self) -> ChatPersona:
        """Get the next persona in the rotation (round-robin)."""
        persona = self.personas[self._persona_index]
        self._persona_index = (self._persona_index + 1) % len(self.personas)
        return persona

    def peek_next_persona(self) -> ChatPersona:
        """See which persona is up next without advancing the rotation."""
        return self.personas[self._persona_index]

    def record_persona_response(self, persona: ChatPersona, reply: str) -> None:
        """Track which persona last responded and what they said."""
        self.last_responding_persona = persona
        self.last_persona_reply = reply

    def add_user_message(self, content: str) -> None:
        self.messages.append(ChatMessage(role=ChatRole.USER, content=content))
        self.last_active = time.time()

    def add_assistant_message(self, content: str, persona: ChatPersona | None = None) -> None:
        self.messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=content, persona=persona))
        self.last_active = time.time()

    def accept_stop(self, stop_data: dict) -> None:
        """Track an accepted stop suggestion."""
        self.accepted_stops.append(stop_data)

        plan_stop = PlanStop(
            name=stop_data.get("name", ""),
            day=stop_data.get("day"),
            time=stop_data.get("time"),
            duration_minutes=stop_data.get("duration_minutes"),
            category=stop_data.get("category"),
        )
        self.current_plan.append(plan_stop)

        # Update conversation context collected places
        name = stop_data.get("name", "")
        if name and name not in self.conversation_context.collected_places:
            self.conversation_context.collected_places.append(name)

        # Update trip_context.existing_stops
        if self.trip_context:
            if name and name not in self.trip_context.existing_stops:
                self.trip_context.existing_stops.append(name)

        self.last_active = time.time()

    def update_destination(self, new_destination: str, new_state: str | None = None, new_country: str | None = None) -> ContextUpdates:
        """Update the active destination. Returns what changed."""
        old_destination = self.conversation_context.destination
        updates = ContextUpdates()

        if new_destination and new_destination != old_destination:
            self.conversation_context.destination = new_destination
            updates.destination_changed = True

            # Also sync to trip_context
            if self.trip_context:
                self.trip_context.destination = new_destination

            logger.info(
                "Destination changed",
                extra={
                    "session_id": self.session_id,
                    "old": old_destination,
                    "new": new_destination,
                },
            )

        if new_state is not None:
            self.conversation_context.state = new_state
            if self.trip_context:
                self.trip_context.region = new_state

        if new_country is not None:
            self.conversation_context.country = new_country

        self.last_active = time.time()
        return updates

    def update_current_stop(self, stop_id: str | None, stop_name: str | None) -> ContextUpdates:
        """Update the current stop focus."""
        updates = ContextUpdates()
        old_stop = self.conversation_context.current_stop_name

        if stop_name != old_stop:
            self.conversation_context.current_stop_id = stop_id
            self.conversation_context.current_stop_name = stop_name
            updates.current_stop_changed = True

        self.last_active = time.time()
        return updates

    def update_context_from_client(self, ctx: ConversationContext) -> ContextUpdates:
        """Merge client-provided context updates into the session.

        The client may send updated context (e.g., user navigated to a different stop).
        We merge non-null fields but track what actually changed.
        """
        updates = ContextUpdates()

        if ctx.destination and ctx.destination != self.conversation_context.destination:
            self.conversation_context.destination = ctx.destination
            updates.destination_changed = True
        if ctx.state:
            self.conversation_context.state = ctx.state
        if ctx.country:
            self.conversation_context.country = ctx.country
        if ctx.current_stop_id or ctx.current_stop_name:
            if ctx.current_stop_name != self.conversation_context.current_stop_name:
                updates.current_stop_changed = True
            self.conversation_context.current_stop_id = ctx.current_stop_id
            self.conversation_context.current_stop_name = ctx.current_stop_name
        if ctx.selected_persona_ids:
            old_personas = self.conversation_context.selected_persona_ids
            if set(ctx.selected_persona_ids) != set(old_personas):
                updates.persona_changed = True
            self.conversation_context.selected_persona_ids = ctx.selected_persona_ids
        if ctx.trip_id:
            self.conversation_context.trip_id = ctx.trip_id
        if ctx.trip_name:
            self.conversation_context.trip_name = ctx.trip_name
        if ctx.collected_places:
            self.conversation_context.collected_places = ctx.collected_places
        if ctx.itinerary_summary:
            self.conversation_context.itinerary_summary = ctx.itinerary_summary
        if ctx.traveler_preferences:
            self.conversation_context.traveler_preferences = ctx.traveler_preferences

        self.last_active = time.time()
        return updates

    def get_resolved_context(self) -> ResolvedContext:
        """Build the resolved context snapshot to return to the frontend."""
        return ResolvedContext(
            destination=self.conversation_context.destination,
            state=self.conversation_context.state,
            country=self.conversation_context.country,
            current_stop_id=self.conversation_context.current_stop_id,
            current_stop_name=self.conversation_context.current_stop_name,
            selected_persona_ids=self.conversation_context.selected_persona_ids,
        )

    def update_trip_context(self, context: TripContext) -> None:
        self.trip_context = context
        self._sync_context_from_trip(context)
        self.last_active = time.time()

    def update_preferences(self, preferences: UserPreferences) -> None:
        self.user_preferences = preferences
        self.last_active = time.time()

    def update_current_plan(self, plan: list[PlanStop]) -> None:
        self.current_plan = plan
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS

    def get_history(self, max_messages: int = 40) -> list[ChatMessage]:
        """Return recent history, trimmed to avoid token overflow."""
        return self.messages[-max_messages:]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "personas": [p.value for p in self.personas],
            "trip_context": self.trip_context.model_dump() if self.trip_context else None,
            "conversation_context": self.conversation_context.model_dump(),
            "current_plan": [s.model_dump() for s in self.current_plan],
            "user_preferences": self.user_preferences.model_dump() if self.user_preferences else None,
            "message_count": len(self.messages),
            "accepted_stops": self.accepted_stops,
            "next_persona": self.peek_next_persona().value,
            "created_at": self.created_at,
            "last_active": self.last_active,
        }


class SessionStore:
    """In-memory store for active chat sessions."""

    def __init__(self):
        self._sessions: dict[str, ChatSession] = {}

    def create_session(
        self,
        personas: list[ChatPersona],
        trip_context: TripContext | None = None,
        current_plan: list[PlanStop] | None = None,
        user_preferences: UserPreferences | None = None,
        conversation_context: ConversationContext | None = None,
        conversation_id: str | None = None,
    ) -> ChatSession:
        """Create a new chat session."""
        self._cleanup_expired()

        session_id = str(uuid4())
        session = ChatSession(
            session_id=session_id,
            personas=personas,
            trip_context=trip_context,
            current_plan=current_plan,
            user_preferences=user_preferences,
            conversation_context=conversation_context,
            conversation_id=conversation_id,
        )
        self._sessions[session_id] = session

        logger.info("Chat session created", extra={
            "session_id": session_id,
            "conversation_id": session.conversation_id,
            "personas": [p.value for p in personas],
            "destination": session.conversation_context.destination,
            "has_preferences": user_preferences is not None,
            "plan_stops": len(current_plan) if current_plan else 0,
        })

        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        """Get a session by ID. Returns None if expired or not found."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired():
            del self._sessions[session_id]
            return None
        return session

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _cleanup_expired(self) -> None:
        """Remove expired sessions periodically."""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired()]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("Cleaned expired sessions", extra={"count": len(expired)})


# Global singleton
session_store = SessionStore()
