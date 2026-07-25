"""In-memory chat session store.

Maintains conversation history and trip context per session so the
client doesn't need to resend everything on each message.

Sessions expire after 2 hours of inactivity.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from app.chat.models import ChatMessage, ChatPersona, ChatRole, TripContext, TripUpdate
from app.core.logging import get_logger

logger = get_logger(__name__)

# Sessions expire after 2 hours
SESSION_TTL_SECONDS = 2 * 60 * 60


class ChatSession:
    """A single chat session with conversation history and context."""

    def __init__(
        self,
        session_id: str,
        personas: list[ChatPersona],
        trip_context: TripContext | None = None,
    ):
        self.session_id = session_id
        self.personas = personas
        self.trip_context = trip_context
        self.messages: list[ChatMessage] = []
        self.accepted_stops: list[dict] = []
        self.created_at = time.time()
        self.last_active = time.time()

    def add_user_message(self, content: str) -> None:
        self.messages.append(ChatMessage(role=ChatRole.USER, content=content))
        self.last_active = time.time()

    def add_assistant_message(self, content: str, persona: ChatPersona | None = None) -> None:
        self.messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=content, persona=persona))
        self.last_active = time.time()

    def accept_stop(self, stop_data: dict) -> None:
        """Track an accepted stop suggestion."""
        self.accepted_stops.append(stop_data)
        # Also update trip_context.existing_stops
        if self.trip_context:
            name = stop_data.get("name", "")
            if name and name not in self.trip_context.existing_stops:
                self.trip_context.existing_stops.append(name)
        self.last_active = time.time()

    def update_trip_context(self, context: TripContext) -> None:
        self.trip_context = context
        self.last_active = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS

    def get_history(self, max_messages: int = 40) -> list[ChatMessage]:
        """Return recent history, trimmed to avoid token overflow."""
        return self.messages[-max_messages:]

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "personas": [p.value for p in self.personas],
            "trip_context": self.trip_context.model_dump() if self.trip_context else None,
            "message_count": len(self.messages),
            "accepted_stops": self.accepted_stops,
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
    ) -> ChatSession:
        """Create a new chat session."""
        self._cleanup_expired()

        session_id = str(uuid4())
        session = ChatSession(
            session_id=session_id,
            personas=personas,
            trip_context=trip_context,
        )
        self._sessions[session_id] = session

        logger.info("Chat session created", extra={
            "session_id": session_id,
            "personas": [p.value for p in personas],
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
