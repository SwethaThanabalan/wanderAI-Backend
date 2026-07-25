"""Models for the trip planner chat API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

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


class UserPreferences(BaseModel):
    """User preferences that shape how personas respond."""

    travel_style: str | None = None
    pace: str | None = None
    dietary_restrictions: list[str] = Field(default_factory=list)
    accessibility_needs: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    budget_level: str | None = None
    group_type: str | None = None
    fitness_level: str | None = None
    photography_skill: str | None = None


class TravelerPreferences(BaseModel):
    """Traveler-specific preferences sent from the iOS app."""

    food_preferences: list[str] = Field(default_factory=list)
    accessibility_needs: list[str] = Field(default_factory=list)
    travel_style: list[str] = Field(default_factory=list)
    mobility: str | None = None
    traveling_with_dog: bool = False
    traveling_with_kids: bool = False
    traveling_with_elders: bool = False


class TripDates(BaseModel):
    """Trip date range."""

    start: str | None = None  # ISO date
    end: str | None = None    # ISO date


class ConversationContext(BaseModel):
    """Structured context that grounds the AI conversation to a specific trip/destination.

    This is the source of truth for what the AI should reference when answering
    follow-up questions. It prevents the model from drifting to unrelated locations.
    """

    trip_id: str | None = None
    trip_name: str | None = None
    country: str | None = None
    state: str | None = None
    destination: str | None = None
    current_stop_id: str | None = None
    current_stop_name: str | None = None
    selected_persona_ids: list[str] = Field(default_factory=list)
    trip_dates: TripDates | None = None
    traveler_preferences: TravelerPreferences | None = None
    collected_places: list[str] = Field(default_factory=list)
    itinerary_summary: list[str] = Field(default_factory=list)
    recent_conversation: list[dict] = Field(default_factory=list)


class ResolvedContext(BaseModel):
    """The resolved context after processing a message.

    Returned to the frontend so it can keep its local state aligned.
    """

    destination: str | None = None
    state: str | None = None
    country: str | None = None
    current_stop_id: str | None = None
    current_stop_name: str | None = None
    selected_persona_ids: list[str] = Field(default_factory=list)


class ContextUpdates(BaseModel):
    """Flags indicating what changed in the context after processing a message."""

    destination_changed: bool = False
    current_stop_changed: bool = False
    persona_changed: bool = False


class PlanStop(BaseModel):
    """A stop in the user's current trip plan."""

    name: str
    day: int | None = None
    time: str | None = None
    duration_minutes: int | None = None
    category: str | None = None
    notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class TripContext(BaseModel):
    """Trip context to ground the conversation."""

    trip_id: str | None = None
    destination: str | None = None
    region: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    travelers: int | None = None
    interests: list[str] = Field(default_factory=list)
    existing_stops: list[str] = Field(default_factory=list)


class LocationCard(BaseModel):
    """Structured location data for the UI to render as a card."""

    name: str
    description: str | None = None
    category: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    day: int | None = None
    time: str | None = None
    duration_minutes: int | None = None
    highlights: list[str] = Field(default_factory=list)
    image_url: str | None = None
    rating: float | None = None
    price_level: str | None = None
    source_url: str | None = None
    address: str | None = None


class TripUpdate(BaseModel):
    """A suggested modification to the trip."""

    action: str
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
    suggested_stops: list[LocationCard] = Field(default_factory=list)


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


# --- Session-based models (with conversation context) ---


class CreateSessionRequest(BaseModel):
    """Request to create a new chat session with full conversation context."""

    personas: list[ChatPersona] = Field(min_length=1, max_length=6)
    trip_context: TripContext | None = None
    current_plan: list[PlanStop] = Field(default_factory=list)
    user_preferences: UserPreferences | None = None
    context: ConversationContext | None = None


class CreateSessionResponse(BaseModel):
    """Response after creating a session."""

    session_id: str
    conversation_id: str
    personas: list[ChatPersona]
    trip_context: TripContext | None = None
    current_plan: list[PlanStop] = Field(default_factory=list)
    user_preferences: UserPreferences | None = None
    resolved_context: ResolvedContext | None = None


class SessionMessageRequest(BaseModel):
    """Send a message within an existing session.

    Optionally include updated context if the client has newer info.
    """

    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    context: ConversationContext | None = None


class SessionMessageResponse(BaseModel):
    """Response from a session message — single persona reply with context tracking."""

    session_id: str
    conversation_id: str
    persona: ChatPersona
    reply: str
    suggested_stops: list[LocationCard] = Field(default_factory=list)
    trip_updates: list[TripUpdate] = Field(default_factory=list)
    resolved_context: ResolvedContext
    context_updates: ContextUpdates
    recommendations: list[LocationCard] = Field(default_factory=list)


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
    conversation_id: str
    personas: list[str]
    trip_context: TripContext | None = None
    current_plan: list[PlanStop] = Field(default_factory=list)
    user_preferences: UserPreferences | None = None
    resolved_context: ResolvedContext | None = None
    message_count: int
    accepted_stops: list[dict] = Field(default_factory=list)
