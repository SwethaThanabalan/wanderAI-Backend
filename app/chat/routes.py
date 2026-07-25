"""Chat API routes for trip planning with persona experts."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_request_id
from app.chat.models import ChatPersona, ChatRequest, ChatResponse, MultiChatRequest, MultiChatResponse
from app.chat.service import chat_with_persona, chat_with_multiple_personas
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
