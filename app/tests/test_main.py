"""Tests for FastAPI app endpoints."""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock neo4j before importing main, since it may not be installed in test env
if "neo4j" not in sys.modules:
    sys.modules["neo4j"] = MagicMock()

from fastapi.testclient import TestClient

from services._extract_config import LangExtractConfig


@pytest.fixture
def client():
    """Create a TestClient with mocked config."""
    from main import app, _get_langextract_config

    config = LangExtractConfig(
        model_id="test-model",
        model_url="https://test.example.com/v1",
        api_key_env="TEST_API_KEY",
    )
    app.dependency_overrides[_get_langextract_config] = lambda: config

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


class TestHealthEndpoint:
    """Health check endpoint returns ok status."""

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestLLMHealthEndpoint:
    """LLM health endpoint uses httpx to check provider connectivity."""

    def test_llm_health_reachable(self, client):
        """When LLM provider is reachable, returns model info."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"id": "test-model"}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_resp):
            response = client.get("/llm-health")
            assert response.status_code == 200
            data = response.json()
            assert data["llm"] == "reachable"
            assert "test-model" in data["available_models"]

    def test_llm_health_unreachable(self, client):
        """When LLM provider is unreachable, returns error info."""
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            response = client.get("/llm-health")
            assert response.status_code == 200
            data = response.json()
            assert data["llm"] == "unreachable"
            assert "Connection refused" in data["error"]

    def test_llm_health_imports_httpx(self):
        """httpx must be importable in production (not just dev dependency)."""
        import importlib

        assert importlib.util.find_spec("httpx") is not None, (
            "httpx must be a production dependency"
        )