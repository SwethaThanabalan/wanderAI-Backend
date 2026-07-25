"""Chat API routes for trip planning with persona experts."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_request_id
from app.chat.models import (
    AcceptSuggestionRequest,
    ChatPersona,
    ChatRequest,
    ChatResponse,
    ContextUpdates,
    ConversationContext,
    CreateSessionRequest,
    CreateSessionResponse,
    MultiChatRequest,
    MultiChatResponse,
    ResolvedContext,
    SessionInfoResponse,
    SessionMessageRequest,
    SessionMessageResponse,
)
from app.chat.personas import PERSONA_CONFIGS
from app.chat.service import chat_single_persona, chat_with_multiple_personas, chat_with_persona, detect_destination_change
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
    - planner: Alex the Planner — logistics and itinerary flow
    - photographer: Maya the Photographer — visual and photography advice
    - historian: Prof. Raj the Historian — history and culture
    - geologist: Dr. Sam the Geologist — geological and natural landscapes
    - foodie: Priya the Foodie — culinary experiences and restaurants
    - storyteller: Ghost the Storyteller — stories, legends, and mysteries

    The response includes:
    - reply: The persona's conversational response (expressive, with emojis)
    - suggested_stops: LocationCard objects the UI can render as cards
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
    """Chat with multiple persona experts at once (group conversation).

    Personas interact with each other and the user in a natural group chat.

    Response includes:
    - persona_replies: Each persona's individual response
    - consolidated: The full group conversation
    - all_suggestions: Combined suggestions
    - all_trip_updates: Combined trip modifications
    - suggested_stops: Location data for mentioned places
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
    """List available persona experts with their character details."""
    personas_list = []
    for key, config in PERSONA_CONFIGS.items():
        personas_list.append({
            "id": key,
            "name": config.display_name,
            "emoji": config.emoji,
            "description": config.system_prompt.split("\n\n")[1] if "\n\n" in config.system_prompt else config.system_prompt[:150],
            "data_sources": config.data_sources,
            "icon": _persona_icon(key),
        })
    return {"personas": personas_list}


def _persona_icon(persona_id: str) -> str:
    """Map persona ID to iOS SF Symbol icon name."""
    icons = {
        "planner": "map",
        "photographer": "camera",
        "historian": "book",
        "geologist": "mountain.2",
        "foodie": "fork.knife",
        "storyteller": "text.book.closed",
    }
    return icons.get(persona_id, "person")


# --- Session-based endpoints ---


@chat_router.post("/sessions", response_model=CreateSessionResponse)
async def create_chat_session(
    request: CreateSessionRequest,
    request_id: str = Depends(get_request_id),
):
    """Create a new chat session with full conversation context.

    The server maintains conversation history from here on. Each message
    will get a response from ONE persona (rotated round-robin).

    Include:
    - personas: which expert personas to include in the session
    - trip_context: destination, dates, travelers info
    - current_plan: the user's existing trip plan (stops already scheduled)
    - user_preferences: travel style, dietary needs, pace, budget, etc.
    - context: structured ConversationContext for grounding (destination, state, trip, stops)

    The context anchors all follow-up questions to the active destination.
    """
    # Validate persona IDs in context match available personas
    if request.context and request.context.selected_persona_ids:
        valid_ids = {p.value for p in ChatPersona}
        invalid = [p for p in request.context.selected_persona_ids if p not in valid_ids]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid persona IDs in context: {invalid}",
            )

    session = session_store.create_session(
        personas=request.personas,
        trip_context=request.trip_context,
        current_plan=request.current_plan,
        user_preferences=request.user_preferences,
        conversation_context=request.context,
    )

    logger.info(
        "Session created",
        extra={
            "request_id": request_id,
            "session_id": session.session_id,
            "conversation_id": session.conversation_id,
            "personas": [p.value for p in request.personas],
            "destination": session.conversation_context.destination,
            "has_preferences": request.user_preferences is not None,
            "plan_stops": len(request.current_plan),
        },
    )

    return CreateSessionResponse(
        session_id=session.session_id,
        conversation_id=session.conversation_id,
        personas=session.personas,
        trip_context=session.trip_context,
        current_plan=session.current_plan,
        user_preferences=session.user_preferences,
        resolved_context=session.get_resolved_context(),
    )


@chat_router.post("/sessions/{session_id}/message", response_model=SessionMessageResponse)
async def send_session_message(
    session_id: str,
    request: SessionMessageRequest,
    request_id: str = Depends(get_request_id),
):
    """Send a message within a session. Gets a single persona response.

    The responding persona rotates each message so you hear from different
    experts throughout the conversation. Each persona responds in their
    unique character with emojis and personality.

    Context grounding ensures follow-up questions stay anchored to the
    active destination. If the user explicitly requests a new destination,
    the context is updated and returned in contextUpdates.

    Response includes:
    - persona: which persona responded this time
    - reply: the persona's expressive response
    - suggested_stops: LocationCard objects for any places mentioned (for UI cards)
    - trip_updates: structured changes if the user accepted something
    - resolved_context: current destination/stop/persona state
    - context_updates: flags for what changed (destination_changed, etc.)
    """
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")

    # If client sends updated context, merge it
    context_updates = ContextUpdates()
    if request.context:
        context_updates = session.update_context_from_client(request.context)

    # Detect if the user is explicitly switching destinations
    new_dest = detect_destination_change(
        request.message, session.conversation_context.destination
    )
    if new_dest:
        dest_updates = session.update_destination(new_dest)
        context_updates.destination_changed = dest_updates.destination_changed

    # Add user message to history
    session.add_user_message(request.message)

    # Get the next persona in the rotation
    persona = session.get_next_persona()

    try:
        reply, location_cards, trip_updates = await chat_single_persona(
            message=request.message,
            persona=persona,
            conversation_history=session.get_history(),
            trip_context=session.trip_context,
            current_plan=session.current_plan,
            user_preferences=session.user_preferences,
            last_responding_persona=session.last_responding_persona,
            last_persona_reply=session.last_persona_reply,
            all_personas=session.personas,
            conversation_context=session.conversation_context,
        )

        # Store the assistant response and track for next persona's context
        session.add_assistant_message(reply, persona=persona)
        session.record_persona_response(persona, reply)

        return SessionMessageResponse(
            session_id=session_id,
            conversation_id=session.conversation_id,
            persona=persona,
            reply=reply,
            suggested_stops=location_cards,
            trip_updates=trip_updates,
            resolved_context=session.get_resolved_context(),
            context_updates=context_updates,
            recommendations=location_cards,
        )

    except Exception as e:
        logger.error(
            "Session message failed",
            extra={"session_id": session_id, "persona": persona.value, "error": str(e)},
        )
        raise HTTPException(status_code=500, detail="Chat service temporarily unavailable.")


@chat_router.post("/sessions/{session_id}/accept")
async def accept_suggestion(
    session_id: str,
    request: AcceptSuggestionRequest,
    request_id: str = Depends(get_request_id),
):
    """Accept a suggestion. Updates trip context and current plan for future messages."""
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
        "current_plan": [s.model_dump() for s in session.current_plan],
        "current_stops": session.trip_context.existing_stops if session.trip_context else [],
    }


@chat_router.get("/sessions/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str):
    """Get current session state including plan, preferences, context, and next persona."""
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return SessionInfoResponse(
        session_id=session.session_id,
        conversation_id=session.conversation_id,
        personas=[p.value for p in session.personas],
        trip_context=session.trip_context,
        current_plan=session.current_plan,
        user_preferences=session.user_preferences,
        resolved_context=session.get_resolved_context(),
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


@chat_router.post("/sessions/{session_id}/generate-plan")
async def generate_plan_from_session(
    session_id: str,
    request_id: str = Depends(get_request_id),
):
    """Generate a full, detailed trip plan from the session's accepted stops.

    Unlike /build-trip (which just formats accepted stops into the trip schema),
    this endpoint:
    1. Organizes stops by geographic proximity into optimal day groups
    2. Fetches detailed information for each stop (descriptions, history, tips)
    3. Fetches high-quality images for each location
    4. Estimates driving times and distances between stops
    5. Generates day titles and summaries
    6. Produces a complete wanderAI.trip document with full detail

    This is the "finalize my trip" endpoint — call it when the user is done
    chatting and wants their complete, polished trip plan.
    """
    session = session_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    if not session.accepted_stops:
        raise HTTPException(status_code=400, detail="No stops accepted yet. Accept some suggestions first.")

    from app.chat.trip_planner import generate_trip_plan

    try:
        trip_document = await generate_trip_plan(
            accepted_stops=session.accepted_stops,
            trip_context=session.trip_context,
            current_plan=session.current_plan,
            user_preferences=session.user_preferences,
        )

        logger.info(
            "Trip plan generated from session",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "stops_count": len(session.accepted_stops),
            },
        )

        return trip_document

    except Exception as e:
        logger.error(
            "Trip plan generation failed",
            extra={"session_id": session_id, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Trip plan generation failed. Please try again.",
        )


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
    """
    return build_trip_json(request)
