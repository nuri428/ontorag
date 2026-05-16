from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ontorag.stores.base import TraversalDirection
from ontorag.stores.fuseki import FusekiStore


@pytest.fixture
def store() -> FusekiStore:
    return FusekiStore("http://localhost:3030", "test", "admin", "admin")


_EMPTY = {"results": {"bindings": []}}


def _edges_result(*triples: tuple[str, str, str]) -> dict:
    return {
        "results": {
            "bindings": [
                {"src": {"value": s}, "pred": {"value": p}, "tgt": {"value": t}}
                for s, p, t in triples
            ]
        }
    }


# ── traverse ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_traverse_returns_start_node(store):
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=_EMPTY)):
        result = await store.traverse("http://example.org/alice", max_depth=1)

    assert result.start_uri == "http://example.org/alice"
    assert any(n["uri"] == "http://example.org/alice" for n in result.nodes)


@pytest.mark.asyncio
async def test_traverse_follows_outgoing_edges(store):
    alice = "http://example.org/alice"
    bob = "http://example.org/bob"
    knows = "http://xmlns.com/foaf/0.1/knows"

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(return_value=_edges_result((alice, knows, bob))),
    ):
        result = await store.traverse(alice, max_depth=1)

    uris = {n["uri"] for n in result.nodes}
    assert bob in uris
    assert any(e["from"] == alice and e["to"] == bob for e in result.edges)


@pytest.mark.asyncio
async def test_traverse_depth_limit_respected(store):
    """max_depth=1 should not recurse further."""
    alice = "http://example.org/alice"
    bob = "http://example.org/bob"
    carol = "http://example.org/carol"
    knows = "http://xmlns.com/foaf/0.1/knows"

    calls: list[str] = []

    async def capture(sparql: str) -> dict:
        calls.append(sparql)
        if alice in sparql and len(calls) == 1:
            return _edges_result((alice, knows, bob))
        return _EMPTY  # depth 2 finds nothing since max_depth=1

    with patch.object(store, "_sparql_select", new=AsyncMock(side_effect=capture)):
        result = await store.traverse(alice, max_depth=1)

    assert carol not in {n["uri"] for n in result.nodes}


# ── find_path ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_path_returns_empty_when_no_path(store):
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=_EMPTY)):
        result = await store.find_path("http://example.org/alice", "http://example.org/ghost")

    assert result.nodes == []
    assert result.edges == []
    assert result.depth_reached == 0


@pytest.mark.asyncio
async def test_find_path_finds_direct_connection(store):
    alice = "http://example.org/alice"
    bob = "http://example.org/bob"
    knows = "http://xmlns.com/foaf/0.1/knows"

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(return_value=_edges_result((alice, knows, bob))),
    ):
        result = await store.find_path(alice, bob)

    assert result.start_uri == alice
    assert result.end_uri == bob
    assert any(n["uri"] == bob for n in result.nodes)
    assert result.depth_reached == 1


# ── find_related ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_related_returns_pairs(store):
    related_result = {
        "results": {
            "bindings": [
                {
                    "a": {"value": "http://example.org/alice"},
                    "aLabel": {"value": "Alice"},
                    "b": {"value": "http://example.org/bob"},
                    "bLabel": {"value": "Bob"},
                }
            ]
        }
    }
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=related_result)):
        results = await store.find_related(
            class_uri_a="http://xmlns.com/foaf/0.1/Person",
            predicate="http://xmlns.com/foaf/0.1/knows",
            class_uri_b="http://xmlns.com/foaf/0.1/Person",
        )

    assert len(results) == 1
    assert results[0]["entity_a"]["uri"] == "http://example.org/alice"
    assert results[0]["entity_b"]["label"] == "Bob"


@pytest.mark.asyncio
async def test_find_related_empty_when_no_connections(store):
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=_EMPTY)):
        results = await store.find_related(
            class_uri_a="http://example.org/A",
            predicate="http://example.org/links",
            class_uri_b="http://example.org/B",
        )
    assert results == []
