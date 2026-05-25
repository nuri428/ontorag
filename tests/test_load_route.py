from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.api.main import app
from ontorag.stores.base import LoadResult


@pytest.fixture
def client_with_store():
    """TestClient with get_store overridden by a mock; yields (client, store)."""
    store = MagicMock()
    store.load_rdf = AsyncMock(
        return_value=LoadResult(
            triples_loaded=3, source="x.ttl", mode="data", ontology="foaf"
        )
    )
    app.dependency_overrides[get_store] = lambda: store
    yield TestClient(app, raise_server_exceptions=False), store
    app.dependency_overrides.clear()


def _post(client, data):
    return client.post(
        "/load",
        files={"file": ("x.ttl", b"<urn:a> <urn:b> <urn:c> .", "text/turtle")},
        data=data,
    )


def test_load_route_forwards_ontology(client_with_store):
    """The /load form's ontology field must reach store.load_rdf (regression:
    API uploads previously always landed in the default graph)."""
    client, store = client_with_store

    resp = _post(client, {"mode": "data", "ontology": "foaf"})

    assert resp.status_code == 200
    assert resp.json()["ontology"] == "foaf"
    _, kwargs = store.load_rdf.call_args
    assert kwargs.get("ontology") == "foaf"


def test_load_route_defaults_ontology_to_none(client_with_store):
    """Omitting ontology forwards None (backward-compatible default graph)."""
    client, store = client_with_store

    resp = _post(client, {"mode": "data"})

    assert resp.status_code == 200
    _, kwargs = store.load_rdf.call_args
    assert kwargs.get("ontology") is None
