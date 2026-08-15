"""Chat service for trip planning with persona experts.

Each user message gets a single persona response (rotated round-robin).
When the persona suggests a place, their data sources are searched to
enrich the response with structured location cards for the UI.

GROUNDING: The system prompt now includes explicit rules that anchor all
follow-up questions to the active destination/trip context. The model will
not silently switch destinations unless the user explicitly requests it.
"""

import json
import re
from typing import Any

from app.chat.data_sources import enrich_location_cards
from app.chat.models import (
    ChatMessage,
    ChatPersona,
    ChatRequest,
    ChatResponse,
    ChatRole,
    ContextUpdates,
    ConversationContext,
    LocationCard,
    MultiChatRequest,
    MultiChatResponse,
    PersonaReply,
    PlanStop,
    ResolvedContext,
    TripContext,
    TripUpdate,
    UserPreferences,
)
from app.chat.personas import PERSONA_CONFIGS, PERSONA_PROMPTS
from app.core.logging import get_logger
from app.services.openai_service import get_openai_client

logger = get_logger(__name__)


# --- Context Resolution ---


def detect_destination_change(message: str, current_destination: str | None, current_state: str | None = None) -> str | None:
    """Detect if the user is explicitly requesting a different destination.

    Returns the new destination name if a switch is detected, else None.
    Only triggers on clear, explicit requests like:
    - "Show me photo spots in Boston"
    - "Now plan for Tokyo"
    - "Switch to Paris"
    - "What about restaurants in NYC?"

    Does NOT trigger on ambiguous follow-ups like:
    - "Best photo spots" (stays in current destination)
    - "What about sunrise?" (stays in current destination)
    - "Create a plan for North Cascades in Washington" (Washington is the state, not a switch)
    """
    if not message:
        return None

    # Patterns that indicate explicit destination switch
    switch_patterns = [
        r"(?:show|find|suggest|recommend|search|look)\s+(?:me\s+)?(?:\w+\s+)*(?:in|for|at|near)\s+(.+?)(?:\.|$|\?|!)",
        r"(?:switch|change|move|go)\s+(?:to|over to)\s+(.+?)(?:\.|$|\?|!)",
        r"(?:now|next)\s+(?:plan|do|show|find)\s+(?:for|in)\s+(.+?)(?:\.|$|\?|!)",
        r"(?:what about|how about)\s+(?:\w+\s+)*(?:in|at|near)\s+(.+?)(?:\.|$|\?|!)",
    ]

    # Build a set of strings that are NOT new destinations (current location terms)
    known_location_terms: set[str] = set()
    if current_destination:
        known_location_terms.add(current_destination.lower())
        # Also add individual words from the destination (e.g., "North Cascades" -> "north", "cascades")
        for word in current_destination.lower().split():
            if len(word) > 3:  # skip short words like "the", "of"
                known_location_terms.add(word)
    if current_state:
        known_location_terms.add(current_state.lower())

    for pattern in switch_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            potential_destination = match.group(1).strip().rstrip("?.!")
            potential_lower = potential_destination.lower()

            # Skip if it matches the current destination or state
            if potential_lower in known_location_terms:
                continue

            # Skip if the current destination contains this text (e.g., "Washington" within context of "North Cascades, Washington")
            if current_destination and potential_lower in current_destination.lower():
                continue

            # Skip if it's clearly just a region/state qualifier of the current destination
            if current_state and potential_lower == current_state.lower():
                continue

            # Only count as a switch if it's different from current
            if current_destination and potential_lower != current_destination.lower():
                # Basic sanity: must be at least 2 chars, not just a pronoun
                if len(potential_destination) >= 2 and potential_lower not in (
                    "here", "there", "this", "that", "it", "them", "the area",
                    "nearby", "the region", "the park", "the city",
                ):
                    return potential_destination
            elif not current_destination:
                # No current destination — this isn't a "switch", it's setting one
                continue

    return None


def _build_system_prompt(
    persona: ChatPersona,
    trip_context: TripContext | None,
    current_plan: list[PlanStop] | None = None,
    user_preferences: UserPreferences | None = None,
    conversation_context: ConversationContext | None = None,
) -> str:
    """Build the full system prompt with grounding rules, persona, trip context, and preferences."""
    config = PERSONA_CONFIGS.get(persona.value)
    if config:
        base_prompt = config.system_prompt
    else:
        base_prompt = PERSONA_PROMPTS.get(persona.value, PERSONA_PROMPTS["planner"])

    # --- GROUNDING RULES (critical for context retention) ---
    grounding_section = ""
    if conversation_context and conversation_context.destination:
        dest = conversation_context.destination
        state = conversation_context.state or ""
        country = conversation_context.country or ""
        location_label = dest
        if state:
            location_label += f", {state}"
        if country and country != state:
            location_label += f", {country}"

        stop_context = ""
        if conversation_context.current_stop_name:
            stop_context = f"\n- Current stop focus: {conversation_context.current_stop_name}"

        places_context = ""
        if conversation_context.collected_places:
            places_list = ", ".join(conversation_context.collected_places[:10])
            places_context = f"\n- Collected places: {places_list}"

        grounding_section = f"""

ACTIVE TRIP CONTEXT (THIS IS YOUR SOURCE OF TRUTH):
- Destination: {location_label}
- Trip: {conversation_context.trip_name or 'Unnamed trip'}{stop_context}{places_context}

GROUNDING RULES — YOU MUST FOLLOW THESE:
1. ALL suggestions, recommendations, and answers MUST be about {dest} unless the user EXPLICITLY names a different location.
2. Short follow-up messages like "best photo spots", "what about sunrise?", "anything nearby?", "is this dog friendly?" refer to {dest} — NOT any other city or place.
3. Do NOT silently switch to another destination. If a message is ambiguous, assume it's about {dest}.
4. If the user EXPLICITLY asks about another location (e.g., "show me photo spots in Boston"), you may respond about that location, but note the destination change.
5. Resolve pronouns ("this", "that", "here", "there") against {dest} and the current stop if one is set.
6. Use the collected places and itinerary when answering to provide continuity.
7. If destination context is completely missing, ask ONE concise clarifying question instead of guessing.
8. NEVER infer a destination from your training data when structured context exists."""
    elif not (trip_context and trip_context.destination):
        # No destination at all — add a rule to ask
        grounding_section = """

GROUNDING RULES:
- The user has NOT specified a destination yet.
- If they ask a location-specific question (e.g., "best photo spots"), ask them to specify where.
- Do NOT guess a destination from your training data.
- Ask ONE concise clarifying question: "Which destination are you thinking about?"
"""

    # Add response format instructions for structured data extraction
    format_instructions = """

CRITICAL — NO REPETITION RULE:
- NEVER repeat information you have already said in this conversation.
- Check your previous messages before responding. If you already mentioned a place, fact, tip, or suggestion — do NOT say it again.
- Each response must contain NEW information, a NEW perspective, or a NEW suggestion.
- If the user asks about something you've already covered, go DEEPER with new details, a different angle, or a follow-up insight — do not rehash what you said before.
- Avoid generic filler phrases you've already used. Keep it fresh every time.

LOCATION SUGGESTIONS:
Whenever you mention or suggest a SPECIFIC named place (restaurant, viewpoint, trail, museum, etc.),
include a location block at the end of your message with the details:

```locations
[{"name": "Place Name", "description": "Your take on why this place is amazing", "category": "dining|hiking|viewpoint|museum|landmark|nature|nightlife|market|beach|park|historic_site|other", "day": 1, "time": "14:00", "duration_minutes": 90, "highlights": ["highlight 1", "highlight 2", "highlight 3"]}]
```

Include as many locations as you mention. The app will render these as cards.
You don't need coordinates — we'll look those up. But DO include your description, category, and highlights.

If the user clearly ACCEPTS a suggestion (says yes, let's do it, add that, etc.), also add:

```trip_updates
[{"action": "add_stop", "description": "Adding [place] to day [N]", "data": {"name": "...", "day": 1, "time": "...", "duration_minutes": 90}}]
```

Only add trip_updates on a clear YES from the user. Don't assume acceptance."""

    # Add user preferences context
    preferences_section = ""
    if user_preferences:
        pref_parts = []
        if user_preferences.travel_style:
            pref_parts.append(f"Travel style: {user_preferences.travel_style}")
        if user_preferences.pace:
            pref_parts.append(f"Preferred pace: {user_preferences.pace}")
        if user_preferences.budget_level:
            pref_parts.append(f"Budget: {user_preferences.budget_level}")
        if user_preferences.group_type:
            pref_parts.append(f"Group: {user_preferences.group_type}")
        if user_preferences.dietary_restrictions:
            pref_parts.append(f"Dietary restrictions: {', '.join(user_preferences.dietary_restrictions)}")
        if user_preferences.accessibility_needs:
            pref_parts.append(f"Accessibility needs: {', '.join(user_preferences.accessibility_needs)}")
        if user_preferences.interests:
            pref_parts.append(f"Interests: {', '.join(user_preferences.interests)}")
        if user_preferences.dislikes:
            pref_parts.append(f"Dislikes/avoid: {', '.join(user_preferences.dislikes)}")
        if user_preferences.fitness_level:
            pref_parts.append(f"Fitness level: {user_preferences.fitness_level}")
        if user_preferences.photography_skill:
            pref_parts.append(f"Photography skill: {user_preferences.photography_skill}")

        if pref_parts:
            preferences_section = "\n\nUSER PREFERENCES (tailor your suggestions to these):\n" + "\n".join(pref_parts)

    # Add trip context
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
            context_parts.append(f"Trip interests: {', '.join(trip_context.interests)}")
        if trip_context.existing_stops:
            context_parts.append(f"Already planned stops: {', '.join(trip_context.existing_stops)}")

        if context_parts:
            context_section = "\n\nCURRENT TRIP CONTEXT:\n" + "\n".join(context_parts)

    # Add current plan details
    plan_section = ""
    if current_plan:
        plan_lines = []
        for stop in current_plan:
            line = f"- Day {stop.day or '?'}: {stop.name}"
            if stop.time:
                line += f" at {stop.time}"
            if stop.category:
                line += f" ({stop.category})"
            plan_lines.append(line)
        if plan_lines:
            plan_section = "\n\nCURRENT TRIP PLAN (what's already scheduled):\n" + "\n".join(plan_lines)
            plan_section += "\n\nBe aware of this plan. Don't suggest things that conflict with existing timing. Build ON this plan, suggest additions or improvements."

    return base_prompt + grounding_section + format_instructions + preferences_section + context_section + plan_section


def _build_messages(
    system_prompt: str,
    conversation_history: list[ChatMessage],
    current_message: str,
) -> list[dict[str, str]]:
    """Build the OpenAI messages array from conversation history."""
    messages = [{"role": "system", "content": system_prompt}]

    for msg in conversation_history:
        role = msg.role.value
        content = msg.content
        # Tag assistant messages with persona name for context
        if msg.persona and role == "assistant":
            config = PERSONA_CONFIGS.get(msg.persona.value)
            if config:
                content = f"[{config.display_name}]: {content}"
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": current_message})

    return messages


def _extract_place_names_from_locations(locations_raw: list[dict]) -> list[str]:
    """Extract place names from parsed location blocks."""
    return [loc.get("name", "") for loc in locations_raw if loc.get("name")]


def _parse_response(raw_text: str, persona: ChatPersona) -> tuple[str, list[dict], list[TripUpdate]]:
    """Parse the assistant reply, extracting location blocks and trip updates.

    Returns (reply_text, locations_raw, trip_updates).
    """
    reply = raw_text
    locations_raw: list[dict] = []
    trip_updates: list[TripUpdate] = []

    # Extract locations block
    if "```locations" in raw_text:
        parts = raw_text.split("```locations")
        reply = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            locations_raw = json.loads(json_block)
        except (json.JSONDecodeError, IndexError):
            pass

    # Legacy: extract stops block (backward compat)
    elif "```stops" in raw_text:
        parts = raw_text.split("```stops")
        reply = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            locations_raw = json.loads(json_block)
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

    # Also clean any remaining suggestions block from legacy format
    if "```suggestions" in reply:
        parts = reply.split("```suggestions")
        reply = parts[0].strip()

    return reply, locations_raw, trip_updates


async def _enrich_and_build_location_cards(
    persona: ChatPersona,
    locations_raw: list[dict],
    destination: str | None,
) -> list[LocationCard]:
    """Take raw location data from the LLM response and enrich it via persona data sources.

    Returns structured LocationCard objects for the UI.
    """
    if not locations_raw:
        return []

    place_names = _extract_place_names_from_locations(locations_raw)

    # Search persona-specific sources for enrichment
    enriched_data = await enrich_location_cards(
        persona_id=persona.value,
        place_names=place_names,
        destination=destination,
    )

    # Merge LLM-provided data with search-enriched data
    cards: list[LocationCard] = []
    for i, loc_raw in enumerate(locations_raw):
        # Start with what the LLM provided
        card_data = {
            "name": loc_raw.get("name", "Unknown"),
            "description": loc_raw.get("description"),
            "category": loc_raw.get("category"),
            "day": loc_raw.get("day"),
            "time": loc_raw.get("time"),
            "duration_minutes": loc_raw.get("duration_minutes"),
            "highlights": loc_raw.get("highlights", []),
        }

        # Overlay enriched data from persona sources (if available)
        if i < len(enriched_data):
            enriched = enriched_data[i]
            # Use enriched coordinates if available
            if enriched.get("latitude"):
                card_data["latitude"] = enriched["latitude"]
            if enriched.get("longitude"):
                card_data["longitude"] = enriched["longitude"]
            # Use enriched fields only if LLM didn't provide them
            if not card_data["description"] and enriched.get("description"):
                card_data["description"] = enriched["description"]
            if enriched.get("rating"):
                card_data["rating"] = enriched["rating"]
            if enriched.get("price_level"):
                card_data["price_level"] = enriched["price_level"]
            if enriched.get("source_url"):
                card_data["source_url"] = enriched["source_url"]
            if enriched.get("address"):
                card_data["address"] = enriched["address"]
            if not card_data["highlights"] and enriched.get("highlights"):
                card_data["highlights"] = enriched["highlights"]
            if enriched.get("image_url"):
                card_data["image_url"] = enriched["image_url"]

        cards.append(LocationCard(**card_data))

    return cards


async def chat_with_persona(request: ChatRequest) -> ChatResponse:
    """Send a message to a persona expert and get trip planning advice.

    Stateless endpoint — client sends full conversation history each time.
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
            output_text = "Hmm, my brain glitched for a sec 🫠 Can you say that again?"

        reply, locations_raw, trip_updates = _parse_response(output_text, request.persona)

        # Enrich locations with persona-specific data sources
        destination = request.trip_context.destination if request.trip_context else None
        suggested_stops = await _enrich_and_build_location_cards(
            request.persona, locations_raw, destination
        )

        logger.info(
            "Chat response generated",
            extra={
                "persona": request.persona.value,
                "locations_count": len(suggested_stops),
                "updates_count": len(trip_updates),
            },
        )

        return ChatResponse(
            reply=reply,
            persona=request.persona,
            trip_updates=trip_updates,
            suggested_stops=suggested_stops,
        )

    except Exception as e:
        logger.error("Chat service failed", extra={"error": str(e), "persona": request.persona.value})
        raise


async def chat_single_persona(
    message: str,
    persona: ChatPersona,
    conversation_history: list[ChatMessage],
    trip_context: TripContext | None = None,
    current_plan: list[PlanStop] | None = None,
    user_preferences: UserPreferences | None = None,
    last_responding_persona: ChatPersona | None = None,
    last_persona_reply: str | None = None,
    all_personas: list[ChatPersona] | None = None,
    conversation_context: ConversationContext | None = None,
) -> tuple[str, list[LocationCard], list[TripUpdate]]:
    """Core single-persona chat function used by session-based endpoints.

    When last_responding_persona is provided, the current persona is prompted
    to react to what the previous persona said — creating a back-and-forth
    group conversation, one message at a time.

    conversation_context provides grounding rules to prevent destination drift.

    Returns (reply_text, location_cards, trip_updates).
    """
    client = get_openai_client()

    system_prompt = _build_system_prompt(
        persona, trip_context, current_plan, user_preferences, conversation_context
    )

    # Add group conversation context so personas talk to each other
    if all_personas and len(all_personas) > 1:
        other_personas = []
        for p in all_personas:
            if p != persona:
                config = PERSONA_CONFIGS.get(p.value)
                if config:
                    other_personas.append(f"{config.display_name}")
        if other_personas:
            system_prompt += f"\n\nGROUP CHAT CONTEXT:\nYou're in a group chat with: {', '.join(other_personas)} and the user."
            system_prompt += "\nYou take turns responding. React to what others said — agree, disagree, riff on their ideas, tease them, or add your perspective. This is a conversation, not isolated answers."

    # If another persona spoke before, include that as context in the user message
    effective_message = message
    if last_responding_persona and last_persona_reply and last_responding_persona != persona:
        prev_config = PERSONA_CONFIGS.get(last_responding_persona.value)
        prev_name = prev_config.display_name if prev_config else last_responding_persona.value
        effective_message = (
            f"[{prev_name} just said]: {last_persona_reply}\n\n"
            f"[User]: {message}"
        )

    messages = _build_messages(system_prompt, conversation_history, effective_message)

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
            config = PERSONA_CONFIGS.get(persona.value)
            output_text = "Oops, lost my train of thought. What were we talking about?"

        reply, locations_raw, trip_updates = _parse_response(output_text, persona)

        # Enrich locations via persona-specific data sources
        destination = trip_context.destination if trip_context else None
        location_cards = await _enrich_and_build_location_cards(
            persona, locations_raw, destination
        )

        logger.info(
            "Single persona chat completed",
            extra={
                "persona": persona.value,
                "locations_count": len(location_cards),
                "updates_count": len(trip_updates),
            },
        )

        return reply, location_cards, trip_updates

    except Exception as e:
        logger.error(
            "Single persona chat failed",
            extra={"error": str(e), "persona": persona.value},
        )
        raise


async def chat_with_multiple_personas(request: MultiChatRequest) -> MultiChatResponse:
    """Generate a group conversation where multiple personas talk to the user AND each other.

    Kept for backward compatibility with the /v1/chat/multi endpoint.
    """
    client = get_openai_client()

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
            output_text = "The crew is speechless for once 😅 Try asking again?"

        # Parse the group conversation
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


def _build_group_conversation_prompt(personas: list[str], trip_context: TripContext | None) -> str:
    """Build a system prompt for a group conversation between personas."""

    persona_descriptions = []
    for p in personas:
        config = PERSONA_CONFIGS.get(p)
        if config:
            persona_descriptions.append(f"- {config.display_name.upper()} — {config.system_prompt[:100]}...")
        else:
            persona_descriptions.append(f"- {p.upper()}")

    active_personas = "\n".join(persona_descriptions)

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
You are generating a natural group chat between travel expert personas helping a user plan their trip.
Each persona has their own unique voice, emoji style, and expertise. Make them feel REAL.

ACTIVE PERSONAS:
{active_personas}

RULES:
1. Each persona speaks in their unique voice with their signature emojis
2. They react to each other — agree, disagree, build on ideas, tease each other
3. Keep it natural and conversational, like a group chat between friends
4. End with a question to the user
5. When suggesting places, include a locations block at the end

LOCATION FORMAT (include at end if places are mentioned):
```locations
[{{"name": "Place Name", "description": "...", "category": "...", "highlights": ["...", "..."]}}]
```
{context_section}"""


def _parse_group_conversation(
    output_text: str,
    personas: list[ChatPersona],
) -> tuple[list[PersonaReply], list[str], list[TripUpdate], list[dict]]:
    """Parse group conversation output into structured components."""

    # Extract locations block first
    locations_raw: list[dict] = []
    text = output_text

    if "```locations" in text:
        parts = text.split("```locations")
        text = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            locations_raw = json.loads(json_block)
        except (json.JSONDecodeError, IndexError):
            pass

    # Legacy stops block
    if "```stops" in text:
        parts = text.split("```stops")
        text = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            locations_raw = json.loads(json_block)
        except (json.JSONDecodeError, IndexError):
            pass

    # Extract trip_updates
    trip_updates: list[TripUpdate] = []
    if "```trip_updates" in text:
        parts = text.split("```trip_updates")
        text = parts[0].strip()
        try:
            json_block = parts[1].split("```")[0].strip()
            updates_raw = json.loads(json_block)
            trip_updates = [TripUpdate(**u) for u in updates_raw]
        except (json.JSONDecodeError, IndexError):
            pass

    # Parse individual persona replies from tagged format [persona_name]
    persona_replies: list[PersonaReply] = []
    for persona in personas:
        config = PERSONA_CONFIGS.get(persona.value)
        display = config.display_name if config else persona.value
        # Look for messages tagged with this persona
        pattern = rf'\[{re.escape(display)}\][:\s]*(.*?)(?=\[|$)'
        matches = re.findall(pattern, text, re.DOTALL)
        reply_text = " ".join(m.strip() for m in matches) if matches else ""
        persona_replies.append(PersonaReply(
            persona=persona,
            reply=reply_text or text[:200],  # Fallback to first part of text
        ))

    return persona_replies, [], trip_updates, locations_raw
