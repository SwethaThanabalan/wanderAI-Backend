"""Tests for the trip planner chat API."""

from unittest.mock import AsyncMock, patch

import pytest


class TestChatEndpoints:
    """Tests for chat API endpoints."""

    def test_list_personas(self, client):
        """GET /v1/chat/personas should return all available personas."""
        response = client.get("/v1/chat/personas")

        assert response.status_code == 200
        data = response.json()
        assert "personas" in data
        personas = data["personas"]
        assert len(personas) == 6

        ids = [p["id"] for p in personas]
        assert "planner" in ids
        assert "photographer" in ids
        assert "historian" in ids
        assert "geologist" in ids
        assert "foodie" in ids
        assert "storyteller" in ids

        # Each persona should have required fields
        for persona in personas:
            assert "id" in persona
            assert "name" in persona
            assert "description" in persona
            assert "icon" in persona

    def test_chat_requires_message(self, client):
        """POST /v1/chat with empty message should return 422."""
        response = client.post("/v1/chat", json={"message": ""})
        assert response.status_code == 422

    def test_chat_rejects_invalid_persona(self, client):
        """POST /v1/chat with invalid persona should return 422."""
        response = client.post("/v1/chat", json={
            "message": "hello",
            "persona": "astronaut",
        })
        assert response.status_code == 422

    @patch("app.chat.service.get_openai_client")
    def test_chat_accepts_valid_request(self, mock_client_fn, client):
        """POST /v1/chat with valid request should call OpenAI and return response."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        # Mock the OpenAI response
        mock_content = AsyncMock()
        mock_content.text = "Great choice! I'd recommend visiting the market early morning."
        mock_item = AsyncMock()
        mock_item.content = [mock_content]
        mock_response = AsyncMock()
        mock_response.output = [mock_item]
        mock_client.responses.create = AsyncMock(return_value=mock_response)

        response = client.post("/v1/chat", json={
            "message": "I want to visit Seattle for 3 days",
            "persona": "foodie",
            "trip_context": {
                "destination": "Seattle",
                "region": "Washington",
                "travelers": 2,
            },
        })

        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert data["persona"] == "foodie"
        assert "suggestions" in data
        assert "trip_updates" in data

    @patch("app.chat.service.get_openai_client")
    def test_chat_with_conversation_history(self, mock_client_fn, client):
        """POST /v1/chat should accept conversation history."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        mock_content = AsyncMock()
        mock_content.text = "Based on our earlier chat, I'd add a sunset stop."
        mock_item = AsyncMock()
        mock_item.content = [mock_content]
        mock_response = AsyncMock()
        mock_response.output = [mock_item]
        mock_client.responses.create = AsyncMock(return_value=mock_response)

        response = client.post("/v1/chat", json={
            "message": "What about the evening?",
            "persona": "photographer",
            "conversation_history": [
                {"role": "user", "content": "Plan my day in Olympic NP"},
                {"role": "assistant", "content": "Start at Hurricane Ridge at dawn."},
            ],
        })

        assert response.status_code == 200
        data = response.json()
        assert data["persona"] == "photographer"


class TestBuildTrip:
    """Tests for POST /v1/chat/build-trip."""

    def test_build_trip_returns_wanderai_format(self, client):
        """Should return a valid wanderAI.trip format document."""
        response = client.post("/v1/chat/build-trip", json={
            "name": "Olympic Adventure",
            "destination": "Olympic National Park",
            "start_date": "2026-08-10",
            "end_date": "2026-08-13",
            "days_count": 3,
            "travelers": 2,
            "interests": ["hiking", "photography"],
            "accepted_stops": [
                {
                    "name": "Hurricane Ridge",
                    "day": 1,
                    "sequence": 1,
                    "time": "06:00",
                    "duration_minutes": 180,
                    "category": "hiking",
                    "latitude": 47.9692,
                    "longitude": -123.4988,
                },
                {
                    "name": "Lake Crescent",
                    "day": 1,
                    "sequence": 2,
                    "time": "12:00",
                    "category": "scenic",
                },
                {
                    "name": "Hoh Rainforest",
                    "day": 2,
                    "sequence": 1,
                    "time": "08:00",
                    "duration_minutes": 240,
                    "category": "hiking",
                    "highlights": ["Hall of Mosses trail"],
                },
            ],
        })

        assert response.status_code == 200
        data = response.json()

        # Verify wanderAI.trip format
        assert data["format"] == "wanderAI.trip"
        assert data["formatVersion"] == "1.0.0"
        assert "generatedAt" in data
        assert "trip" in data

        trip = data["trip"]
        assert trip["name"] == "Olympic Adventure"
        assert trip["primaryDestination"] == "Olympic National Park"
        assert trip["startDate"] == "2026-08-10"
        assert trip["endDate"] == "2026-08-13"
        assert len(trip["days"]) == 3

        # Day 1 should have 2 stops
        day1 = trip["days"][0]
        assert day1["dayNumber"] == 1
        assert day1["date"] == "2026-08-10"
        assert len(day1["stops"]) == 2
        assert day1["stops"][0]["name"] == "Hurricane Ridge"
        assert day1["stops"][0]["mapReference"]["latitude"] == 47.9692
        assert day1["stops"][1]["name"] == "Lake Crescent"

        # Day 2 should have 1 stop
        day2 = trip["days"][1]
        assert day2["dayNumber"] == 2
        assert len(day2["stops"]) == 1
        assert day2["stops"][0]["name"] == "Hoh Rainforest"
        assert "Hall of Mosses trail" in day2["stops"][0]["highlights"]

        # Day 3 should exist but be empty
        day3 = trip["days"][2]
        assert day3["dayNumber"] == 3
        assert len(day3["stops"]) == 0

    def test_build_trip_requires_stops(self, client):
        """Should reject request with no accepted stops."""
        response = client.post("/v1/chat/build-trip", json={
            "name": "Empty Trip",
            "destination": "Nowhere",
            "accepted_stops": [],
        })
        assert response.status_code == 422

    def test_build_trip_requires_name(self, client):
        """Should reject request with empty name."""
        response = client.post("/v1/chat/build-trip", json={
            "name": "",
            "destination": "Seattle",
            "accepted_stops": [{"name": "Pike Place"}],
        })
        assert response.status_code == 422
