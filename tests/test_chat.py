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
