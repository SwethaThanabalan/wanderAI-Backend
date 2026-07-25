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
