"""Chat service for trip planning with persona experts."""

import json
from typing import Any

from app.chat.models import (
    ChatMessage,
    ChatPersona,
    ChatRequest,
    ChatResponse,
    ChatRole,
    TripContext,
    TripUpdate,
)
from app.chat.personas import PERSONA_PROMPTS
from app.core.logging import get_logger
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


def _build_system_prompt(persona: ChatPersona, trip_context: TripContext | None) -> str:
    """Build the full system prompt including persona and trip context."""
    base_prompt = PERSONA_PROMPTS.get(persona.value, PERSONA_PROMPTS["planner"])

    # Add response format instructions
    format_instructions = """

RESPONSE FORMAT:
Respond naturally in conversation. At the end of your reply, if you have specific \
trip suggestions, add them in a JSON block like this:

```suggestions
["suggestion 1", "suggestion 2"]
```

If you're suggesting actual trip modifications (adding/removing stops, reordering), add:

```trip_updates
[{"action": "add_stop", "description": "Add morning visit to Pike Place Market", "data": {"name": "Pike Place Market", "time": "8:00 AM", "duration": "2 hours"}}]
```

Valid actions: add_stop, remove_stop, reorder, change_duration, add_note

Only include these blocks when you have concrete suggestions. Most replies will just be conversational."""

    # Add trip context if available
    context_section = ""
    if trip_context:
        context_parts = []
        if trip_context.destination:
            context_parts.append(f"Destination: {trip_context.destination}")
        if trip_context.region:
            context_parts.append(f"Region: {trip_context.region}")
        if trip_context.start_date:
            context_parts.append(f"Start date: {trip_context.start_date}")
        if trip_context.end_date:
            context_parts.append(f"End date: {trip_context.end_date}")
        if trip_context.travelers:
            context_parts.append(f"Travelers: {trip_context.travelers}")
        if trip_context.interests:
            context_parts.append(f"Interests: {', '.join(trip_context.interests)}")
        if trip_context.existing_stops:
            context_parts.append(f"Current itinerary stops: {', '.join(trip_context.existing_stops)}")

        if context_parts:
            context_section = f"\n\nCURRENT TRIP CONTEXT:\n" + "\n".join(context_parts)

    return base_prompt + format_instructions + context_section


def _build_messages(
    system_prompt: str,
    conversation_history: list[ChatMessage],
    current_message: str,
) -> list[dict[str, str]]:
    """Build the OpenAI messages array from conversation history."""
    messages = [{"role": "system", "content": system_prompt}]

    for msg in conversation_history:
        messages.append({
            "role": msg.role.value,
            "content": msg.content,
        })

    messages.append({"role": "user", "content": current_message})

    return messages


def _parse_response(raw_text: str, persona: ChatPersona) -> ChatResponse:
    """Parse the assistant reply, extracting suggestions and trip updates."""
    reply = raw_text
    suggestions: list[str] = []
    trip_updates: list[TripUpdate] = []

    # Extract suggestions block
    if "```suggestions" in raw_text:
        parts = raw_text.split("```suggestions")
        reply = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            suggestions = json.loads(json_block)
        except (json.JSONDecodeError, IndexError):
            pass

    # Extract trip_updates block
    if "```trip_updates" in reply:
        parts = reply.split("```trip_updates")
        reply = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            updates_raw = json.loads(json_block)
            trip_updates = [TripUpdate(**u) for u in updates_raw]
        except (json.JSONDecodeError, IndexError):
            pass
    elif "```trip_updates" in raw_text:
        try:
            json_block = raw_text.split("```trip_updates")[1].split("```")[0].strip()
            updates_raw = json.loads(json_block)
            trip_updates = [TripUpdate(**u) for u in updates_raw]
        except (json.JSONDecodeError, IndexError):
            pass

    return ChatResponse(
        reply=reply,
        persona=persona,
        suggestions=suggestions,
        trip_updates=trip_updates,
    )


async def chat_with_persona(request: ChatRequest) -> ChatResponse:
    """Send a message to a persona expert and get trip planning advice.

    The conversation is stateless on the server — the client sends
    the full conversation history each time.
    """
    client = get_openai_client()

    system_prompt = _build_system_prompt(request.persona, request.trip_context)
    messages = _build_messages(system_prompt, request.conversation_history, request.message)

    try:
        response = await client.responses.create(
            model="gpt-4o",
            input=messages,
        )

        # Extract text from response
        output_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text

        if not output_text:
            output_text = "I'm sorry, I didn't generate a response. Could you try rephrasing?"

        result = _parse_response(output_text, request.persona)

        logger.info(
            "Chat response generated",
            extra={
                "persona": request.persona.value,
                "suggestions_count": len(result.suggestions),
                "updates_count": len(result.trip_updates),
            },
        )

        return result

    except Exception as e:
        logger.error("Chat service failed", extra={"error": str(e), "persona": request.persona.value})
        raise
