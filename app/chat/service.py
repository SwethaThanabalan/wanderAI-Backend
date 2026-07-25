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
    """Generate a group conversation where multiple personas talk to the user AND each other.

    This is NOT independent answers consolidated — it's a live group chat
    where personas react to each other, argue, agree, build on ideas, and
    talk directly to the user as a team.
    """
    from app.chat.models import MultiChatRequest, MultiChatResponse, PersonaReply

    client = get_openai_client()

    # Build the group conversation system prompt
    persona_names = [p.value for p in request.personas]
    group_prompt = _build_group_conversation_prompt(persona_names, request.trip_context)
    messages = _build_messages(group_prompt, request.conversation_history, request.message)

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

        if not output_text:
            output_text = "The crew is speechless for once. Try asking again?"

        # Parse the group conversation into individual persona replies
        persona_replies, all_suggestions, all_trip_updates = _parse_group_conversation(
            output_text, request.personas
        )

        logger.info(
            "Multi-persona group chat completed",
            extra={
                "personas": persona_names,
                "total_suggestions": len(all_suggestions),
                "total_updates": len(all_trip_updates),
            },
        )

        return MultiChatResponse(
            persona_replies=persona_replies,
            consolidated=output_text,
            all_suggestions=all_suggestions,
            all_trip_updates=all_trip_updates,
        )

    except Exception as e:
        logger.error("Multi-persona chat failed", extra={"error": str(e)})
        raise


def _build_group_conversation_prompt(personas: list[str], trip_context: "TripContext | None") -> str:
    """Build a system prompt for a group conversation between personas."""
    from app.chat.personas import PERSONA_PROMPTS

    persona_descriptions = {
        "planner": "PLANNER — practical logistics expert, keeps everyone on track",
        "photographer": "PHOTOGRAPHER — obsessed with light and the perfect shot, dramatic about visuals",
        "historian": "HISTORIAN — drops facts like gossip, dramatic storyteller, 'well ACTUALLY...'",
        "geologist": "GEOLOGIST — giddy about rocks, British-nerdy energy, sees millions of years everywhere",
        "foodie": "FOODIE — infectious food enthusiasm, interrupts everyone to talk about eating",
        "storyteller": "STORYTELLER — dramatic narrator, loves legends and mysteries, builds suspense",
    }

    active_personas = "\n".join(
        f"- {persona_descriptions.get(p, p.upper())}" for p in personas
    )

    # Context section
    context_section = ""
    if trip_context:
        context_parts = []
        if trip_context.destination:
            context_parts.append(f"Destination: {trip_context.destination}")
        if trip_context.region:
            context_parts.append(f"Region: {trip_context.region}")
        if trip_context.start_date:
            context_parts.append(f"Dates: {trip_context.start_date} to {trip_context.end_date or '...'}")
        if trip_context.travelers:
            context_parts.append(f"Travelers: {trip_context.travelers}")
        if trip_context.interests:
            context_parts.append(f"Interests: {', '.join(trip_context.interests)}")
        if trip_context.existing_stops:
            context_parts.append(f"Current stops: {', '.join(trip_context.existing_stops)}")
        if context_parts:
            context_section = "\n\nTRIP CONTEXT:\n" + "\n".join(context_parts)

    return f"""\
You are generating a GROUP CONVERSATION between travel expert personas who are \
helping a user plan their trip. This is NOT a report — it's a lively chat.

ACTIVE PERSONAS IN THIS CONVERSATION:
{active_personas}

CONVERSATION RULES:
- Each persona speaks IN CHARACTER with their unique voice and catchphrases
- They talk TO THE USER (giving advice, asking questions, making suggestions)
- They talk TO EACH OTHER (agreeing, disagreeing, building on ideas, teasing)
- They REACT to each other: "Oh come on—", "Wait I love that idea!", "Okay but—"
- They ASK THE USER follow-up questions to understand preferences
- They ARGUE about priorities in a fun, loving way
- DO NOT just take turns giving independent answers — INTERACT and RIFF off each other
- Keep it high energy, funny, and helpful
- Include at least one moment where they disagree or debate
- End with a question back to the user to keep the conversation going

FORMAT:
Write the conversation as dialogue. Each line starts with the persona name in brackets:

[photographer] Oh you're going in August? The light is going to be INSANE—
[foodie] Okay but before we talk about light can we talk about the salmon?
[historian] You're both missing the point. Do you know what happened here in 1890—
[photographer] Here we go again...
[user question] What do you think about starting at Hurricane Ridge?

IMPORTANT RULES:
- Do NOT generate the trip for them — ASK what they want, SUGGEST options, let THEM decide
- Keep suggesting and reacting — don't dump a full itinerary
- Ask clarifying questions: "Are you morning people?", "How do you feel about crowds?"
- Each persona should pitch their favorite idea and defend it against the others
- Be conversational, not transactional

At the end, if there are concrete suggestions the user might want to accept, add:

```suggestions
["suggestion 1", "suggestion 2"]
```

```trip_updates
[{{"action": "add_stop", "description": "...", "data": {{"name": "...", "day": 1}}}}]
```

Only add these blocks if the group reached some consensus. Often the conversation \
is still exploratory and that's fine — just keep chatting.{context_section}"""


def _parse_group_conversation(
    raw_text: str, personas: list["ChatPersona"]
) -> tuple[list, list[str], list["TripUpdate"]]:
    """Parse the group conversation text into persona replies and extracted data."""
    from app.chat.models import PersonaReply

    suggestions: list[str] = []
    trip_updates: list[TripUpdate] = []
    conversation_text = raw_text

    # Extract suggestions block
    if "```suggestions" in raw_text:
        parts = raw_text.split("```suggestions")
        conversation_text = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            suggestions = json.loads(json_block)
        except (json.JSONDecodeError, IndexError):
            pass

    # Extract trip_updates block
    if "```trip_updates" in conversation_text:
        parts = conversation_text.split("```trip_updates")
        conversation_text = parts[0].strip()
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

    # Extract each persona's lines from the conversation
    persona_replies = []
    for persona in personas:
        # Collect all lines that belong to this persona
        tag = f"[{persona.value}]"
        lines = []
        for line in conversation_text.split("\n"):
            stripped = line.strip()
            if stripped.lower().startswith(tag):
                content = stripped[len(tag):].strip()
                if content:
                    lines.append(content)

        reply_text = " ".join(lines) if lines else ""
        persona_replies.append(PersonaReply(
            persona=persona,
            reply=reply_text,
            suggestions=[s for s in suggestions if persona.value in s.lower()] if suggestions else [],
            trip_updates=[],
        ))

    return persona_replies, suggestions, trip_updates


async def _consolidate_replies(
    user_message: str,
    persona_replies: list,
    trip_context: "TripContext | None",
) -> str:
    """Fallback consolidation — not used in group conversation mode."""
    return "See the group conversation above."
