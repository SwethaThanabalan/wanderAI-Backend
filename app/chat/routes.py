"""Chat API routes for trip planning with persona experts."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_request_id
from app.chat.models import (
    ChatPersona,
    ChatRequest,
    ChatResponse,
    MultiChatRequest,
    MultiChatResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    AcceptSuggestionRequest,
    SessionInfoResponse,
)
from app.chat.service import chat_with_persona, chat_with_multiple_personas
from app.chat.session_store import session_store
from app.chat.trip_builder import BuildTripRequest, build_trip_json
from app.core.logging import get_logger

logger = get_logger(__name__)

chat_router = APIRouter(prefix="/v1/chat", tags=["chat"])


@chat_router.post("", response_model=ChatResponse)
async def send_chat_message(
    request: ChatRequest,
    request_id: str = Depends(get_request_id),
):
    """Chat with a persona expert to plan or update a trip.

    The conversation is stateless on the server. Send the full
    conversation_history with each request to maintain context.

    Available personas:
    - planner: General trip planning expert (default)
    - photographer: Visual/photography trip advice
    - historian: Historical and cultural trip advice
    - geologist: Geological and natural landscape advice
    - foodie: Culinary and food experience advice
    - storyteller: Stories, legends, and dramatic experiences

    The response may include:
    - reply: The persona's conversational response
    - suggestions: Quick actionable suggestions as a list
    - trip_updates: Structured trip modifications the app can apply
    """
    logger.info(
        "Chat message received",
        extra={
            "request_id": request_id,
            "persona": request.persona.value,
            "has_trip_context": request.trip_context is not None,
            "history_length": len(request.conversation_history),
        },
    )

    try:
        response = await chat_with_persona(request)
        return response
    except Exception as e:
        logger.error("Chat failed", extra={"error": str(e), "persona": request.persona.value})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chat service temporarily unavailable.",
        )


@chat_router.post("/multi", response_model=MultiChatResponse)
async def send_multi_persona_message(
    request: MultiChatRequest,
    request_id: str = Depends(get_request_id),
):
    """Chat with multiple persona experts at once.

    Sends the same message to all selected personas in parallel.
    Each persona gives their unique take, then the responses are
    consolidated into one cohesive recommendation.

    Response includes:
    - persona_replies: Each persona's individual response
    - consolidated: A merged, synthesized summary of all perspectives
    - all_suggestions: Combined suggestions from all personas
    - all_trip_updates: Combined trip modifications from all personas
    """
    logger.info(
        "Multi-persona chat received",
        extra={
            "request_id": request_id,
            "personas": [p.value for p in request.personas],
            "has_trip_context": request.trip_context is not None,
        },
    )

    try:
        response = await chat_with_multiple_personas(request)
        return response
    except Exception as e:
        logger.error("Multi-persona chat failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Chat service temporarily unavailable.",
        )


@chat_router.get("/personas")
async def list_personas():
    """List available persona experts with descriptions."""
    return {
        "personas": [
            {
                "id": "planner",
                "name": "Trip Planner",
                "description": "General travel planning expert. Helps with logistics, timing, and itinerary flow.",
                "icon": "map",
            },
            {
                "id": "photographer",
                "name": "Photographer",
                "description": "Visual expert. Best viewpoints, lighting times, and photogenic hidden spots.",
                "icon": "camera",
            },
            {
                "id": "historian",
                "name": "Historian",
                "description": "History and culture expert. Dramatic stories, heritage sites, and cultural context.",
                "icon": "book",
            },
            {
                "id": "geologist",
                "name": "Geologist",
                "description": "Earth science expert. Rock formations, landscapes, and millions of years of drama.",
                "icon": "mountain.2",
            },
            {
                "id": "foodie",
                "name": "Foodie",
                "description": "Culinary expert. Local restaurants, markets, seasonal dishes, and food experiences.",
                "icon": "fork.knife",
            },
            {
                "id": "storyteller",
                "name": "Storyteller",
                "description": "Narrative expert. Legends, ghost stories, mysteries, and unforgettable tales.",
                "icon": "text.book.closed",
            },
        ]
    }


# --- Session-based endpoints ---


@chat_router.post("/sessions", response_model=CreateSessionResponse)
async def create_chat_session(
    request: CreateSessionRequest,
    request_id: str = Depends(get_request_id),
):
    """Create a new chat session. The server maintains history from here on."""
    session = session_store.create_session(
        personas=request.personas,
        trip_context=request.trip_context,
    )
    return CreateSessionResponse(
        session_id=session.session_id,
        personas=session.personas,
        trip_context=session.trip_context,
    )


@chat_router.post("/sessions/{session_id}/message", response_model=SessionMessageResponse)
async def send_session_message(
    session_id: str,
    request: SessionMessageRequest,
    request_id: str = Depends(get_request_id),
):
    """Send a message within a session. Server remembers everything."""
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    session.add_user_message(request.message)

    from app.chat.models import MultiChatRequest
    multi_request = MultiChatRequest(
        message=request.message,
        personas=session.personas,
        trip_context=session.trip_context,
        conversation_history=session.get_history(),
    )

    try:
        response = await chat_with_multiple_personas(multi_request)
        session.add_assistant_message(response.consolidated)
        return SessionMessageResponse(
            session_id=session_id,
            consolidated=response.consolidated,
            persona_replies=response.persona_replies,
            all_suggestions=response.all_suggestions,
            all_trip_updates=response.all_trip_updates,
            suggested_stops=response.suggested_stops,
        )
    except Exception as e:
        logger.error("Session message failed", extra={"session_id": session_id, "error": str(e)})
        raise HTTPException(status_code=500, detail="Chat service temporarily unavailable.")


@chat_router.post("/sessions/{session_id}/accept")
async def accept_suggestion(
    session_id: str,
    request: AcceptSuggestionRequest,
    request_id: str = Depends(get_request_id),
):
    """Accept a suggestion. Updates trip context for future messages."""
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    stop_data = {
        "name": request.stop_name,
        "day": request.day,
        "time": request.time,
        "duration_minutes": request.duration_minutes,
        "category": request.category,
    }
    session.accept_stop(stop_data)

    return {
        "status": "accepted",
        "stop": stop_data,
        "total_accepted": len(session.accepted_stops),
        "current_stops": session.trip_context.existing_stops if session.trip_context else [],
    }


@chat_router.get("/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str):
    """Get current session state."""
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return SessionInfoResponse(
        session_id=session.session_id,
        personas=[p.value for p in session.personas],
        trip_context=session.trip_context,
        message_count=len(session.messages),
        accepted_stops=session.accepted_stops,
    )


@chat_router.post("/sessions/{session_id}/build-trip")
async def build_trip_from_session(session_id: str):
    """Build wanderAI.trip JSON from the session's accepted stops."""
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    if not session.accepted_stops:
        raise HTTPException(status_code=400, detail="No stops accepted yet.")

    from app.chat.trip_builder import AcceptedStop, BuildTripRequest
    accepted = [AcceptedStop(**s) for s in session.accepted_stops]

    build_request = BuildTripRequest(
        name=f"Trip to {session.trip_context.destination}" if session.trip_context and session.trip_context.destination else "My Trip",
        destination=session.trip_context.destination or "Unknown",
        start_date=session.trip_context.start_date if session.trip_context else None,
        end_date=session.trip_context.end_date if session.trip_context else None,
        days_count=max(s.day for s in accepted),
        travelers=session.trip_context.travelers if session.trip_context else None,
        interests=session.trip_context.interests if session.trip_context else [],
        accepted_stops=accepted,
    )
    return build_trip_json(build_request)


@chat_router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a chat session."""
    session_store.delete_session(session_id)
    return {"status": "deleted"}


@chat_router.post("/build-trip")
async def build_trip(
    request: BuildTripRequest,
    request_id: str = Depends(get_request_id),
):
    """Build a complete wanderAI.trip JSON from accepted suggestions.

    After the user has chatted with persona experts and accepted stops,
    call this endpoint with all accepted stops to get the final trip
    document in wanderAI.trip format, ready for import into the iOS app.

    Example request:
    ```json
    {
      "name": "Olympic Adventure",
      "destination": "Olympic National Park",
      "start_date": "2026-08-10",
      "end_date": "2026-08-13",
      "days_count": 3,
      "travelers": 2,
      "interests": ["hiking", "photography", "food"],
      "accepted_stops": [
        {
          "name": "Hurricane Ridge",
          "day": 1,
          "sequence": 1,
          "time": "06:00",
          "duration_minutes": 180,
          "category": "hiking",
          "description": "Stunning alpine views with wildflower meadows",
          "highlights": ["360-degree mountain views", "Wildflower meadows in August"],
          "latitude": 47.9692,
          "longitude": -123.4988
        },
        {
          "name": "Lake Crescent Lodge",
          "day": 1,
          "sequence": 2,
          "time": "12:30",
          "duration_minutes": 90,
          "category": "food",
          "description": "Historic lodge restaurant on the lake",
          "highlights": ["Fresh salmon", "Lakeside dining"],
          "latitude": 48.0560,
          "longitude": -123.7910
        }
      ]
    }
    ```

    Returns a complete wanderAI.trip format JSON document.
    """
    logger.info(
        "Building trip JSON",
        extra={
            "request_id": request_id,
            "destination": request.destination,
            "stops_count": len(request.accepted_stops),
        },
    )

    try:
        trip_json = build_trip_json(request)
        return trip_json
    except Exception as e:
        logger.error("Trip build failed", extra={"error": str(e)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to build trip document.",
        )
