"""Shared test configuration and fixtures."""

import os

import pytest
from fastapi.testclient import TestClient

# Set test environment before importing app
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
