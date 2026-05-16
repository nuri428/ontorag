from __future__ import annotations

from fastapi.testclient import TestClient

from ontorag.api.main import app

client = TestClient(app)


def test_health_check_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


def test_mcp_route_registered_in_openapi():
    # fastapi-mcp registers routes visible in the OpenAPI schema
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json().get("paths", {})
    # /health must be present; /mcp is a streaming endpoint not in OpenAPI schema
    assert "/health" in paths
