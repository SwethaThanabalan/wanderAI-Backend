"""Tests for the health check endpoint."""


def test_health_returns_ok(client):
    """GET /health should return status ok."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
