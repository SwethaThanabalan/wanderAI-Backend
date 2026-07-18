"""Tests for health, root, and documentation endpoints."""


def test_health_returns_healthy(client):
    """GET /health should return status healthy with version and environment."""
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "environment" in data


def test_root_returns_info(client):
    """GET / should return app info with links."""
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "WanderAI Backend"
    assert data["status"] == "healthy"
    assert data["docs"] == "/docs"
    assert data["health"] == "/health"


def test_docs_returns_200(client):
    """GET /docs should return the Swagger UI page."""
    response = client.get("/docs")

    assert response.status_code == 200


def test_redoc_returns_200(client):
    """GET /redoc should return the ReDoc page."""
    response = client.get("/redoc")

    assert response.status_code == 200


def test_openapi_json_returns_200(client):
    """GET /openapi.json should return the OpenAPI schema."""
    response = client.get("/openapi.json")

    assert response.status_code == 200
    data = response.json()
    assert "paths" in data
    assert "/health" in data["paths"]
