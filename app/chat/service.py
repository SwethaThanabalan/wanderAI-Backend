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

CONVERSATION RULES:
- Keep responses SHORT. 2-4 sentences max.
- Always end with a question to the user.
- Offer ONE idea at a time, not a list.
- Be conversational — like texting a friend, not writing a travel guide.
- DO NOT plan their whole trip in one message. Suggest one thing, ask if they like it.

WHENEVER YOU MENTION A SPECIFIC PLACE, include a stops block at the end with full details:

```stops
[{"name": "Hurricane Ridge", "day": 1, "time": "06:00", "duration_minutes": 180, "category": "hiking", "description": "Alpine meadows with 360-degree mountain views", "latitude": 47.9692, "longitude": -123.4988, "highlights": ["Sunrise views", "Wildflower meadows"]}]
```

ALWAYS include this with real coordinates when suggesting a named location.
If just chatting without a specific place suggestion, skip it.

If the user clearly ACCEPTS a suggestion, also add:

```trip_updates
[{"action": "add_stop", "description": "...", "data": {"name": "...", "time": "...", "duration_minutes": 120}}]
```

Only add trip_updates on a clear YES from the user."""

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
    """Parse the assistant reply, extracting suggestions, trip updates, and stops."""
    reply = raw_text
    suggestions: list[str] = []
    trip_updates: list[TripUpdate] = []
    suggested_stops: list[dict] = []

    # Extract stops block
    if "```stops" in raw_text:
        parts = raw_text.split("```stops")
        reply = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            suggested_stops = json.loads(json_block)
        except (json.JSONDecodeError, IndexError):
            pass

    # Extract suggestions block
    if "```suggestions" in reply:
        parts = reply.split("```suggestions")
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

    return ChatResponse(
        reply=reply,
        persona=persona,
        suggestions=suggestions,
        trip_updates=trip_updates,
        suggested_stops=suggested_stops,
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
        persona_replies, all_suggestions, all_trip_updates, suggested_stops = _parse_group_conversation(
            output_text, request.personas
        )

        logger.info(
            "Multi-persona group chat completed",
            extra={
                "personas": persona_names,
                "total_suggestions": len(all_suggestions),
                "total_updates": len(all_trip_updates),
                "suggested_stops": len(suggested_stops),
            },
        )

        return MultiChatResponse(
            persona_replies=persona_replies,
            consolidated=output_text,
            all_suggestions=all_suggestions,
            all_trip_updates=all_trip_updates,
            suggested_stops=suggested_stops,
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
You are generating a SHORT, natural group chat between travel expert personas \
helping a user plan their trip.

ACTIVE PERSONAS:
{active_personas}

CRITICAL RULES — READ CAREFULLY:

1. OUTPUT EXACTLY 2 MESSAGES TOTAL. Not 3, not 5, not 10. Just 2 lines of dialogue.
2. One persona speaks, then another reacts or adds to it. That's it.
3. The LAST message MUST end with a question to the user.
4. Keep each message SHORT — 1-3 sentences max. Like a real text conversation.
5. DO NOT dump information. Reveal ONE thing at a time.
6. DO NOT list multiple suggestions. Offer ONE idea and ask if they're interested.
7. Make it feel like friends texting, not experts presenting.

INTERACTION STYLE:
- Persona 1 says something specific (one idea, one reaction, one question)
- Persona 2 reacts to it OR adds a quick thought, then asks the user something
- That's the whole response. Stop there. Wait for the user.

GOOD EXAMPLE:
[photographer] Okay so if you're doing Olympic in August — Hurricane Ridge at sunrise. The light is unreal. Are you a morning person though?
[foodie] If you ARE dragging yourself up at 5am, there's a coffee spot in Port Angeles that makes it worth it. Want me to tell you about it or should we figure out your first day vibe first?

BAD EXAMPLE (too much):
[photographer] Here's what I'd do for day 1... *proceeds to list 8 things*
[historian] And here's my take... *another 8 things*
[foodie] Don't forget... *more stuff*

NEVER DO:
- More than 2 persona messages per response
- Long paragraphs
- Lists of suggestions
- Planning the whole trip at once
- Answering without asking something back

FORMAT:
[persona_name] Short message here.
[persona_name] Short reaction + question to user.

That's it. Two lines. Always end asking the user something.

WHENEVER YOU MENTION A SPECIFIC PLACE, you MUST include a stops block at the end:

```stops
[{{"name": "Hurricane Ridge", "day": 1, "time": "06:00", "duration_minutes": 180, "category": "hiking", "description": "Alpine meadows with 360-degree mountain views", "latitude": 47.9692, "longitude": -123.4988, "highlights": ["Sunrise views", "Wildflower meadows"]}}]
```

ALWAYS include this block if you suggest a specific named location. Include real coordinates.
If you're just chatting without suggesting a specific place, skip the stops block.{context_section}"""


def _parse_group_conversation(
    raw_text: str, personas: list["ChatPersona"]
) -> tuple[list, list[str], list["TripUpdate"], list[dict]]:
    """Parse the group conversation text into persona replies and extracted data."""
    from app.chat.models import PersonaReply

    suggestions: list[str] = []
    trip_updates: list[TripUpdate] = []
    suggested_stops: list[dict] = []
    conversation_text = raw_text

    # Extract stops block
    if "```stops" in raw_text:
        parts = raw_text.split("```stops")
        conversation_text = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            suggested_stops = json.loads(json_block)
        except (json.JSONDecodeError, IndexError):
            pass

    # Extract suggestions block
    if "```suggestions" in conversation_text:
        parts = conversation_text.split("```suggestions")
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
            suggestions=[],
            trip_updates=[],
        ))

    return persona_replies, suggestions, trip_updates, suggested_stops


async def _consolidate_replies(
    user_message: str,
    persona_replies: list,
    trip_context: "TripContext | None",
) -> str:
    """Fallback consolidation — not used in group conversation mode."""
    return "See the group conversation above."
