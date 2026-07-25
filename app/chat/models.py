"""Models for the trip planner chat API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ChatPersona(StrEnum):
    """Available persona experts for trip planning chat."""

    PLANNER = "planner"
    PHOTOGRAPHER = "photographer"
    HISTORIAN = "historian"
    GEOLOGIST = "geologist"
    FOODIE = "foodie"
    STORYTELLER = "storyteller"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class TripContext(BaseModel):
    """Optional trip context to ground the conversation."""

    trip_id: str | None = None
    destination: str | None = None
    region: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    travelers: int | None = None
    interests: list[str] = Field(default_factory=list)
    existing_stops: list[str] = Field(default_factory=list)


class TripUpdate(BaseModel):
    """A suggested modification to the trip."""

    action: str  # "add_stop", "remove_stop", "reorder", "change_duration", "add_note"
    description: str
    data: dict = Field(default_factory=dict)


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    role: ChatRole
    content: str
    persona: ChatPersona | None = None


class ChatRequest(BaseModel):
    """Request body for POST /v1/chat."""

    message: str = Field(min_length=1, max_length=2000)
    persona: ChatPersona = ChatPersona.PLANNER
    trip_context: TripContext | None = None
    conversation_history: list[ChatMessage] = Field(default_factory=list, max_length=50)


class ChatResponse(BaseModel):
    """Response from the chat endpoint."""

    reply: str
    persona: ChatPersona
    suggestions: list[str] = Field(default_factory=list)
    trip_updates: list[TripUpdate] = Field(default_factory=list)
    suggested_stops: list[dict] = Field(default_factory=list)


# --- Multi-persona models ---


class MultiChatRequest(BaseModel):
    """Request body for POST /v1/chat/multi — query multiple personas at once."""

    message: str = Field(min_length=1, max_length=2000)
    personas: list[ChatPersona] = Field(min_length=1, max_length=6)
    trip_context: TripContext | None = None
    conversation_history: list[ChatMessage] = Field(default_factory=list, max_length=50)


class PersonaReply(BaseModel):
    """One persona's individual response."""

    persona: ChatPersona
    reply: str
    suggestions: list[str] = Field(default_factory=list)
    trip_updates: list[TripUpdate] = Field(default_factory=list)


class MultiChatResponse(BaseModel):
    """Response from multi-persona chat — each persona's take + consolidated result."""

    persona_replies: list[PersonaReply]
    consolidated: str
    all_suggestions: list[str] = Field(default_factory=list)
    all_trip_updates: list[TripUpdate] = Field(default_factory=list)
    suggested_stops: list[dict] = Field(default_factory=list)


# --- Session-based models ---


class CreateSessionRequest(BaseModel):
    """Request to create a new chat session."""

    personas: list[ChatPersona] = Field(min_length=1, max_length=6)
    trip_context: TripContext | None = None


class CreateSessionResponse(BaseModel):
    """Response after creating a session."""

    session_id: str
    personas: list[ChatPersona]
    trip_context: TripContext | None = None


class SessionMessageRequest(BaseModel):
    """Send a message within an existing session."""

    message: str = Field(min_length=1, max_length=2000)


class SessionMessageResponse(BaseModel):
    """Response from a session message."""

    session_id: str
    consolidated: str
    persona_replies: list[PersonaReply] = Field(default_factory=list)
    all_suggestions: list[str] = Field(default_factory=list)
    all_trip_updates: list[TripUpdate] = Field(default_factory=list)
    suggested_stops: list[dict] = Field(default_factory=list)


class AcceptSuggestionRequest(BaseModel):
    """Accept a suggestion and add it to the session's trip plan."""

    stop_name: str
    day: int = 1
    time: str | None = None
    duration_minutes: int | None = None
    category: str | None = None


class SessionInfoResponse(BaseModel):
    """Current session state."""

    session_id: str
    personas: list[str]
    trip_context: TripContext | None = None
    message_count: int
    accepted_stops: list[dict] = Field(default_factory=list)
