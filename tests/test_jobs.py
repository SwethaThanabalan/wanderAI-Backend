"""Tests for podcast job API endpoints."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest


VALID_JOB_REQUEST = {
    "trip_id": "11111111-1111-1111-1111-111111111111",
    "stop_id": "22222222-2222-2222-2222-222222222222",
    "destination_name": "Lake Crescent",
    "region": "Olympic National Park, Washington",
    "visit_date": "2026-07-30",
    "episode_minutes": 8,
    "personas": ["photographer", "historian"],
}


class TestCreatePodcastJob:
    """Tests for POST /v1/podcast-jobs."""

    @patch("app.workflows.podcast_generation.supabase_service")
    @patch("app.api.routes.supabase_service")
    def test_create_job_returns_202(self, mock_route_supabase, mock_workflow_supabase, client):
        """Valid request should return 202 Accepted."""
        job_id = str(uuid4())
        mock_route_supabase.create_research_job.return_value = {"id": job_id}
        # Mock the workflow's supabase calls so background task doesn't fail
        mock_workflow_supabase.get_research_job.return_value = {
            "id": job_id,
            "status": "completed",
        }

        response = client.post("/v1/podcast-jobs", json=VALID_JOB_REQUEST)

        assert response.status_code == 202
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"

    def test_create_job_missing_destination(self, client):
        """Missing destination_name should return 422."""
        request = {**VALID_JOB_REQUEST}
        del request["destination_name"]

        response = client.post("/v1/podcast-jobs", json=request)

        assert response.status_code == 422

    def test_create_job_empty_destination(self, client):
        """Empty destination_name should return 422."""
        request = {**VALID_JOB_REQUEST, "destination_name": ""}

        response = client.post("/v1/podcast-jobs", json=request)

        assert response.status_code == 422

    def test_create_job_invalid_episode_minutes_too_low(self, client):
        """Episode minutes below 3 should return 422."""
        request = {**VALID_JOB_REQUEST, "episode_minutes": 1}

        response = client.post("/v1/podcast-jobs", json=request)

        assert response.status_code == 422

    def test_create_job_invalid_episode_minutes_too_high(self, client):
        """Episode minutes above 20 should return 422."""
        request = {**VALID_JOB_REQUEST, "episode_minutes": 25}

        response = client.post("/v1/podcast-jobs", json=request)

        assert response.status_code == 422

    def test_create_job_invalid_persona(self, client):
        """Unsupported persona should return 422."""
        request = {**VALID_JOB_REQUEST, "personas": ["astronaut"]}

        response = client.post("/v1/podcast-jobs", json=request)

        assert response.status_code == 422

    def test_create_job_invalid_trip_id(self, client):
        """Non-UUID trip_id should return 422."""
        request = {**VALID_JOB_REQUEST, "trip_id": "not-a-uuid"}

        response = client.post("/v1/podcast-jobs", json=request)

        assert response.status_code == 422


class TestGetPodcastJob:
    """Tests for GET /v1/podcast-jobs/{job_id}."""

    @patch("app.api.routes.supabase_service")
    def test_get_job_found(self, mock_supabase, client):
        """Existing job should return 200 with job details."""
        job_id = str(uuid4())
        mock_supabase.get_research_job.return_value = {
            "id": job_id,
            "status": "queued",
            "destination_name": "Lake Crescent",
            "region": "Olympic National Park, Washington",
            "personas": ["photographer", "historian"],
            "episode_minutes": 8,
            "user_id": None,
            "error_message": None,
            "result": None,
            "created_at": "2026-07-01T00:00:00+00:00",
            "started_at": None,
            "completed_at": None,
        }

        response = client.get(f"/v1/podcast-jobs/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"
        assert data["destination_name"] == "Lake Crescent"

    @patch("app.api.routes.supabase_service")
    def test_get_job_not_found(self, mock_supabase, client):
        """Non-existent job should return 404."""
        mock_supabase.get_research_job.return_value = None

        job_id = str(uuid4())
        response = client.get(f"/v1/podcast-jobs/{job_id}")

        assert response.status_code == 404

    def test_get_job_invalid_id(self, client):
        """Invalid UUID should return 422."""
        response = client.get("/v1/podcast-jobs/not-a-uuid")

        assert response.status_code == 422
