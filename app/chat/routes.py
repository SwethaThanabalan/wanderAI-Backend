"""Chat API routes for trip planning with persona experts."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_request_id
from app.chat.models import ChatPersona, ChatRequest, ChatResponse
from app.chat.service import chat_with_persona
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
