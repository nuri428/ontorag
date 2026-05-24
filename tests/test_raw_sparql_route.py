from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.api.main import app


@pytest.fixture
def client_factory():
    """Yield a helper that builds a TestClient with get_store overridden."""

    def _build(store):
        app.dependency_overrides[get_store] = lambda: store
        return TestClient(app, raise_server_exceptions=False)

    yield _build
    app.dependency_overrides.clear()


def test_raw_sparql_runs_on_sparql_backend(client_factory):
    """A store exposing _sparql_select returns parsed rows (Fuseki path)."""
    store = MagicMock()
    store._sparql_select = AsyncMock(
        return_value={"results": {"bindings": [{"s": {"value": "urn:x"}}]}}
    )
    client = client_factory(store)

    resp = client.post("/tools/query/sparql", json={"sparql": "SELECT * WHERE {?s ?p ?o}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["s"]
    assert body["rows"] == [{"s": "urn:x"}]
    assert body["total"] == 1


def test_raw_sparql_returns_501_on_non_sparql_backend(client_factory):
    """A backend without _sparql_select (e.g. Neo4j) yields 501, not a 500."""
    store = MagicMock(spec=["get_schema"])  # no _sparql_select attribute
    client = client_factory(store)

    resp = client.post("/tools/query/sparql", json={"sparql": "SELECT * WHERE {?s ?p ?o}"})

    assert resp.status_code == 501
    assert "not supported" in resp.json()["detail"].lower()
