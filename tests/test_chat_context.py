"""Tests for conversation context grounding.

Verifies that follow-up messages stay anchored to the active destination
and that explicit destination switches are detected correctly.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")


@pytest.fixture
def client():
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


def _create_session_with_context(client, destination="North Cascades National Park", state="Washington", personas=None):
    """Helper: create a session with destination context."""
    return client.post("/v1/chat/sessions", json={
        "personas": personas or ["photographer"],
        "trip_context": {
            "destination": destination,
            "region": state,
            "travelers": 2,
            "interests": ["photography", "hiking"],
        },
        "context": {
            "destination": destination,
            "state": state,
            "country": "United States",
            "trip_name": "Washington Adventure",
            "selected_persona_ids": personas or ["photographer"],
            "collected_places": ["Maple Pass Loop", "Mazama Store"],
        },
    })


def _mock_openai_response(text):
    """Create a mock OpenAI response with the given text."""
    mock_content = AsyncMock()
    mock_content.text = text
    mock_item = AsyncMock()
    mock_item.content = [mock_content]
    mock_response = AsyncMock()
    mock_response.output = [mock_item]
    return mock_response


class TestContextRetention:
    """Test A: Follow-up messages stay in the active destination."""

    @patch("app.chat.service.get_openai_client")
    def test_followup_stays_in_destination(self, mock_client_fn, client):
        """After a North Cascades itinerary, 'Best photo spots' stays in North Cascades."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        # Create session with North Cascades context
        create_resp = _create_session_with_context(client)
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]

        # Verify resolved context
        data = create_resp.json()
        assert data["resolved_context"]["destination"] == "North Cascades National Park"
        assert data["resolved_context"]["state"] == "Washington"
        assert data["conversation_id"] is not None

        # Send first message
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "Here's a 3-day plan for North Cascades! Day 1: Maple Pass Loop..."
        ))
        resp1 = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Create a 3-day plan for North Cascades in Washington",
        })
        assert resp1.status_code == 200
        assert resp1.json()["resolved_context"]["destination"] == "North Cascades National Park"

        # Send follow-up — should stay in North Cascades
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "📸 Oh you want the GOOD spots?? Maple Pass Loop at golden hour is unreal..."
        ))
        resp2 = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Best photo spots",
        })
        assert resp2.status_code == 200
        result = resp2.json()

        # Context must still be North Cascades
        assert result["resolved_context"]["destination"] == "North Cascades National Park"
        assert result["resolved_context"]["state"] == "Washington"
        assert result["context_updates"]["destination_changed"] is False

    @patch("app.chat.service.get_openai_client")
    def test_sunrise_followup_retains_context(self, mock_client_fn, client):
        """Test B: 'What about sunrise?' stays in North Cascades."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        create_resp = _create_session_with_context(client)
        session_id = create_resp.json()["session_id"]

        # First message about photo spots
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "Maple Pass Loop and Cascade Pass are incredible for photography!"
        ))
        client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Best photo spots",
        })

        # Follow-up about sunrise
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "Sunrise at Cascade Pass is MAGICAL 📸✨ The light hits the peaks around 6:15am..."
        ))
        resp = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "What about sunrise?",
        })
        assert resp.status_code == 200
        result = resp.json()

        # Context unchanged
        assert result["resolved_context"]["destination"] == "North Cascades National Park"
        assert result["context_updates"]["destination_changed"] is False


class TestDestinationSwitch:
    """Test C: Explicit destination changes are detected."""

    @patch("app.chat.service.get_openai_client")
    def test_explicit_switch_to_boston(self, mock_client_fn, client):
        """'Now show me photo spots in Boston' should change destination."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        create_resp = _create_session_with_context(client)
        session_id = create_resp.json()["session_id"]

        # Explicit switch
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "Boston has some great spots! The Public Garden at golden hour..."
        ))
        resp = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Now show me photo spots in Boston",
        })
        assert resp.status_code == 200
        result = resp.json()

        # Destination should have changed
        assert result["context_updates"]["destination_changed"] is True
        assert result["resolved_context"]["destination"] == "Boston"

    @patch("app.chat.service.get_openai_client")
    def test_explicit_switch_with_find_pattern(self, mock_client_fn, client):
        """'Find restaurants in Tokyo' should switch destination."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        create_resp = _create_session_with_context(client)
        session_id = create_resp.json()["session_id"]

        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "Tokyo's food scene is INSANE 🍜..."
        ))
        resp = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Find restaurants in Tokyo",
        })
        result = resp.json()

        assert result["context_updates"]["destination_changed"] is True
        assert result["resolved_context"]["destination"] == "Tokyo"


class TestMissingContext:
    """Test D: No destination context should prompt clarification."""

    @patch("app.chat.service.get_openai_client")
    def test_no_destination_asks_for_clarification(self, mock_client_fn, client):
        """Without destination context, model should ask where."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        # Create session WITHOUT destination
        create_resp = client.post("/v1/chat/sessions", json={
            "personas": ["photographer"],
        })
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]

        # The resolved context should have no destination
        assert create_resp.json()["resolved_context"]["destination"] is None

        # Send an ambiguous message
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "Which destination are you thinking about? 📸 I'd love to help find spots!"
        ))
        resp = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Best photo spots",
        })
        assert resp.status_code == 200
        # Destination still None — model was told to ask
        assert resp.json()["resolved_context"]["destination"] is None
        assert resp.json()["context_updates"]["destination_changed"] is False


class TestPersonaChange:
    """Test E: Persona switch preserves location context."""

    @patch("app.chat.service.get_openai_client")
    def test_persona_switch_keeps_destination(self, mock_client_fn, client):
        """Switching from foodie to photographer keeps North Cascades."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        # Create session with multiple personas (foodie first, then photographer)
        create_resp = _create_session_with_context(
            client,
            personas=["foodie", "photographer"],
        )
        session_id = create_resp.json()["session_id"]

        # First message goes to foodie
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "The Mazama Store has incredible pastries 🍜🔥..."
        ))
        resp1 = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Where should we eat?",
        })
        assert resp1.json()["persona"] == "foodie"
        assert resp1.json()["resolved_context"]["destination"] == "North Cascades National Park"

        # Second message goes to photographer (rotation)
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "After eating at Mazama Store, the morning light on the Methow Valley is INSANE 📸..."
        ))
        resp2 = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Best photo spots nearby?",
        })
        assert resp2.json()["persona"] == "photographer"
        # Destination still North Cascades after persona rotation
        assert resp2.json()["resolved_context"]["destination"] == "North Cascades National Park"
        assert resp2.json()["context_updates"]["destination_changed"] is False

    @patch("app.chat.service.get_openai_client")
    def test_client_persona_update_keeps_destination(self, mock_client_fn, client):
        """Client sending updated persona IDs doesn't reset destination."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        create_resp = _create_session_with_context(client, personas=["foodie", "photographer"])
        session_id = create_resp.json()["session_id"]

        # Client sends context update with changed persona selection
        mock_client.responses.create = AsyncMock(return_value=_mock_openai_response(
            "Let me tell you about the history of this area 📜..."
        ))
        resp = client.post(f"/v1/chat/sessions/{session_id}/message", json={
            "message": "Tell me about history here",
            "context": {
                "destination": "North Cascades National Park",
                "state": "Washington",
                "selected_persona_ids": ["historian"],
            },
        })
        assert resp.status_code == 200
        result = resp.json()

        # Destination preserved
        assert result["resolved_context"]["destination"] == "North Cascades National Park"
        assert result["context_updates"]["destination_changed"] is False
        # Persona change detected
        assert result["context_updates"]["persona_changed"] is True


class TestContextDetection:
    """Unit tests for detect_destination_change function."""

    def test_ambiguous_does_not_switch(self):
        """Short follow-ups should NOT trigger a switch."""
        from app.chat.service import detect_destination_change

        assert detect_destination_change("Best photo spots", "North Cascades") is None
        assert detect_destination_change("What about sunrise?", "North Cascades") is None
        assert detect_destination_change("Anything nearby?", "North Cascades") is None
        assert detect_destination_change("Is this dog friendly?", "North Cascades") is None
        assert detect_destination_change("How long does that take?", "North Cascades") is None
        assert detect_destination_change("Add the second one", "North Cascades") is None

    def test_explicit_switch_detected(self):
        """Explicit destination mentions should trigger a switch."""
        from app.chat.service import detect_destination_change

        assert detect_destination_change("Show me photo spots in Boston", "North Cascades") == "Boston"
        assert detect_destination_change("Find restaurants in Tokyo", "North Cascades") == "Tokyo"
        assert detect_destination_change("Now plan for Paris", "North Cascades") == "Paris"
        assert detect_destination_change("Switch to Yellowstone", "North Cascades") == "Yellowstone"

    def test_same_destination_no_switch(self):
        """Mentioning the current destination should NOT trigger a switch."""
        from app.chat.service import detect_destination_change

        assert detect_destination_change("Show me photo spots in North Cascades", "North Cascades") is None

    def test_no_destination_set(self):
        """With no current destination, nothing should switch."""
        from app.chat.service import detect_destination_change

        assert detect_destination_change("Best photo spots", None) is None


class TestSessionCreation:
    """Test session creation with context."""

    def test_create_session_with_full_context(self, client):
        """Session creation with context returns resolved_context and conversation_id."""
        resp = _create_session_with_context(client)
        assert resp.status_code == 200
        data = resp.json()

        assert "session_id" in data
        assert "conversation_id" in data
        assert data["resolved_context"]["destination"] == "North Cascades National Park"
        assert data["resolved_context"]["state"] == "Washington"
        assert data["resolved_context"]["country"] == "United States"
        assert "photographer" in data["resolved_context"]["selected_persona_ids"]

    def test_create_session_without_context_uses_trip_context(self, client):
        """Session with trip_context but no explicit context still grounds."""
        resp = client.post("/v1/chat/sessions", json={
            "personas": ["planner"],
            "trip_context": {
                "destination": "Olympic National Park",
                "region": "Washington",
            },
        })
        assert resp.status_code == 200
        data = resp.json()

        # Should have synced from trip_context
        assert data["resolved_context"]["destination"] == "Olympic National Park"
        assert data["resolved_context"]["state"] == "Washington"

    def test_create_session_rejects_invalid_persona_in_context(self, client):
        """Invalid persona IDs in context should be rejected."""
        resp = client.post("/v1/chat/sessions", json={
            "personas": ["photographer"],
            "context": {
                "destination": "Seattle",
                "selected_persona_ids": ["astronaut"],
            },
        })
        assert resp.status_code == 422

    def test_session_info_returns_context(self, client):
        """GET /sessions/{id} should return resolved_context."""
        create_resp = _create_session_with_context(client)
        session_id = create_resp.json()["session_id"]

        info_resp = client.get(f"/v1/chat/sessions/{session_id}")
        assert info_resp.status_code == 200
        data = info_resp.json()

        assert data["conversation_id"] is not None
        assert data["resolved_context"]["destination"] == "North Cascades National Park"
