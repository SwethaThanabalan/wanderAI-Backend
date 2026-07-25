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


async def chat_with_multiple_personas(request: "MultiChatRequest") -> "MultiChatResponse":
    """Query multiple personas in parallel, then consolidate their responses.

    Each persona answers independently, then a consolidation pass merges
    their suggestions into a unified recommendation.
    """
    import asyncio

    from app.chat.models import MultiChatRequest, MultiChatResponse, PersonaReply

    client = get_openai_client()

    # Run all personas in parallel
    async def _get_persona_reply(persona: ChatPersona) -> PersonaReply:
        system_prompt = _build_system_prompt(persona, request.trip_context)
        messages = _build_messages(system_prompt, request.conversation_history, request.message)

        response = await client.responses.create(
            model="gpt-4o",
            input=messages,
        )

        output_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text

        if not output_text:
            output_text = "No response generated."

        parsed = _parse_response(output_text, persona)

        return PersonaReply(
            persona=persona,
            reply=parsed.reply,
            suggestions=parsed.suggestions,
            trip_updates=parsed.trip_updates,
        )

    # Execute all persona queries in parallel
    tasks = [_get_persona_reply(p) for p in request.personas]
    persona_replies = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out errors
    successful_replies: list[PersonaReply] = []
    for reply in persona_replies:
        if isinstance(reply, PersonaReply):
            successful_replies.append(reply)
        else:
            logger.warning("Persona reply failed", extra={"error": str(reply)})

    # Merge all suggestions and trip updates
    all_suggestions: list[str] = []
    all_trip_updates: list[TripUpdate] = []
    for reply in successful_replies:
        all_suggestions.extend(reply.suggestions)
        all_trip_updates.extend(reply.trip_updates)

    # Consolidation pass — merge all persona perspectives into one summary
    consolidated = await _consolidate_replies(
        user_message=request.message,
        persona_replies=successful_replies,
        trip_context=request.trip_context,
    )

    logger.info(
        "Multi-persona chat completed",
        extra={
            "personas_queried": len(request.personas),
            "personas_succeeded": len(successful_replies),
            "total_suggestions": len(all_suggestions),
            "total_updates": len(all_trip_updates),
        },
    )

    return MultiChatResponse(
        persona_replies=successful_replies,
        consolidated=consolidated,
        all_suggestions=all_suggestions,
        all_trip_updates=all_trip_updates,
    )


async def _consolidate_replies(
    user_message: str,
    persona_replies: list,
    trip_context: "TripContext | None",
) -> str:
    """Use the LLM to merge multiple persona perspectives into one cohesive answer."""
    client = get_openai_client()

    # Build a summary of each persona's take
    persona_summaries = []
    for reply in persona_replies:
        persona_summaries.append(
            f"**{reply.persona.value.upper()}** says:\n{reply.reply}"
        )

    combined_input = "\n\n---\n\n".join(persona_summaries)

    consolidation_prompt = """\
You are the WanderAI Trip Consolidator. Multiple travel experts just gave their \
perspectives on a traveler's question. Your job is to weave their answers into ONE \
cohesive, fun, actionable response.

RULES:
- Combine the best insights from each expert into a flowing narrative
- Highlight where experts AGREE (strong recommendations)
- Note where they DISAGREE and let the user decide
- Keep each expert's personality flavor in brief attribution ("The Foodie insists...", "Our Geologist points out...")
- Be concise but complete — don't repeat everything, synthesize
- End with a clear priority order if there are many suggestions
- Keep the tone fun, warm, and actionable

Do NOT just list each persona's answer sequentially. WEAVE them together."""

    context_note = ""
    if trip_context and trip_context.destination:
        context_note = f"\n\nTrip: {trip_context.destination}"
        if trip_context.start_date:
            context_note += f" ({trip_context.start_date} to {trip_context.end_date or '...'})"

    messages = [
        {"role": "system", "content": consolidation_prompt},
        {"role": "user", "content": f"The traveler asked: \"{user_message}\"{context_note}\n\nHere's what each expert said:\n\n{combined_input}"},
    ]

    try:
        response = await client.responses.create(
            model="gpt-4o",
            input=messages,
        )

        output_text = ""
        for item in response.output:
            if hasattr(item, "content"):
                for content_block in item.content:
                    if hasattr(content_block, "text"):
                        output_text += content_block.text

        return output_text or "Here's what the team thinks — check each persona's reply above for details."

    except Exception as e:
        logger.warning("Consolidation failed, returning fallback", extra={"error": str(e)})
        # Fallback: simple concatenation
        return "Here's what each expert recommends — see their individual replies for the full picture."
