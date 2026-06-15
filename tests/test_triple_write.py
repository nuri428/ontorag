"""Tests for assert_triple / retract_triple / assert_triples MCP tools.

Unit tests run with a stub store (no live Fuseki).
Integration tests are marked ``integration`` and require a running Fuseki instance.
"""

from __future__ import annotations

import pytest

from ontorag.mcp_stdio import _dispatch, _TOOLS


# ── Stub store ────────────────────────────────────────────────────────────────


class StubStore:
    """Minimal in-memory stub that records write calls."""

    def __init__(self) -> None:
        self.asserted: list[tuple] = []
        self.retracted: list[tuple] = []

    async def assert_triple(
        self, subject, predicate, obj, *, object_is_uri=False, ontology=None
    ) -> None:
        self.asserted.append((subject, predicate, obj, object_is_uri, ontology))

    async def retract_triple(
        self, subject, predicate, obj, *, object_is_uri=False, ontology=None
    ) -> None:
        self.retracted.append((subject, predicate, obj, object_is_uri, ontology))

    async def assert_triples(self, triples, *, ontology=None) -> int:
        for t in triples:
            self.asserted.append((*t, ontology))
        return len(triples)


# ── MCP tool registration ─────────────────────────────────────────────────────


def test_write_tools_registered():
    assert "assert_triple" in _TOOLS
    assert "retract_triple" in _TOOLS
    assert "assert_triples" in _TOOLS


def test_assert_triple_required_fields():
    schema = _TOOLS["assert_triple"]
    assert set(schema["required"]) == {"subject", "predicate", "object"}


def test_assert_triples_required_fields():
    schema = _TOOLS["assert_triples"]
    assert schema["required"] == ["triples"]


# ── _dispatch unit tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_assert_triple():
    store = StubStore()
    result = await _dispatch(store, "assert_triple", {
        "subject": "urn:test:s",
        "predicate": "urn:test:p",
        "object": "hello",
    })
    assert result["status"] == "asserted"
    assert result["triple"] == {"s": "urn:test:s", "p": "urn:test:p", "o": "hello"}
    assert store.asserted[0][:3] == ("urn:test:s", "urn:test:p", "hello")


@pytest.mark.asyncio
async def test_dispatch_assert_triple_uri_object():
    store = StubStore()
    await _dispatch(store, "assert_triple", {
        "subject": "urn:test:s",
        "predicate": "urn:test:p",
        "object": "urn:test:o",
        "object_is_uri": True,
    })
    _, _, _, is_uri, _ = store.asserted[0]
    assert is_uri is True


@pytest.mark.asyncio
async def test_dispatch_retract_triple():
    store = StubStore()
    result = await _dispatch(store, "retract_triple", {
        "subject": "urn:test:s",
        "predicate": "urn:test:p",
        "object": "hello",
    })
    assert result["status"] == "retracted"
    assert store.retracted[0][:3] == ("urn:test:s", "urn:test:p", "hello")


@pytest.mark.asyncio
async def test_dispatch_assert_triples():
    store = StubStore()
    result = await _dispatch(store, "assert_triples", {
        "triples": [
            {"subject": "urn:s1", "predicate": "urn:p", "object": "v1"},
            {"subject": "urn:s2", "predicate": "urn:p", "object": "urn:o2", "object_is_uri": True},
        ]
    })
    assert result["status"] == "asserted"
    assert result["count"] == 2
    assert len(store.asserted) == 2


@pytest.mark.asyncio
async def test_dispatch_assert_triples_empty():
    store = StubStore()
    result = await _dispatch(store, "assert_triples", {"triples": []})
    assert result["count"] == 0


# ── Integration tests (require live Fuseki) ───────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fuseki_assert_and_retract_roundtrip():
    """Write a triple, verify it exists, retract it, verify it's gone."""
    import httpx
    from ontorag.stores.fuseki import FusekiStore

    store = FusekiStore.from_env()
    subject = "urn:test:triple-write-roundtrip"
    predicate = "http://www.w3.org/2000/01/rdf-schema#label"
    obj = "integration test label"

    try:
        await store.assert_triple(subject, predicate, obj)

        # verify via raw SPARQL
        auth = httpx.BasicAuth("admin", "admin")
        async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:
            q = f"ASK {{ GRAPH ?g {{ <{subject}> <{predicate}> \"{obj}\" . }} }}"
            resp = await client.post(
                "http://localhost:3030/ontorag/sparql",
                data={"query": q},
                headers={"Accept": "application/sparql-results+json"},
            )
            resp.raise_for_status()
            assert resp.json()["boolean"] is True, "Triple not found after assert"

        await store.retract_triple(subject, predicate, obj)

        async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:
            q2 = f"ASK {{ GRAPH ?g {{ <{subject}> <{predicate}> \"{obj}\" . }} }}"
            resp2 = await client.post(
                "http://localhost:3030/ontorag/sparql",
                data={"query": q2},
                headers={"Accept": "application/sparql-results+json"},
            )
            resp2.raise_for_status()
            assert resp2.json()["boolean"] is False, "Triple still present after retract"
    finally:
        await store.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fuseki_assert_triples_batch():
    import httpx
    from ontorag.stores.fuseki import FusekiStore

    store = FusekiStore.from_env()
    ns = "urn:test:batch-"
    triples = [
        (f"{ns}s", "urn:test:p1", "val1", False),
        (f"{ns}s", "urn:test:p2", "val2", False),
        (f"{ns}s", "urn:test:rel", f"{ns}target", True),
    ]

    try:
        count = await store.assert_triples(triples)
        assert count == 3

        auth = httpx.BasicAuth("admin", "admin")
        async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:
            q = f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH ?g {{ <{ns}s> ?p ?o . }} }}"
            resp = await client.post(
                "http://localhost:3030/ontorag/sparql",
                data={"query": q},
                headers={"Accept": "application/sparql-results+json"},
            )
            resp.raise_for_status()
            n = int(resp.json()["results"]["bindings"][0]["n"]["value"])
            assert n >= 3
    finally:
        # Clean up test triples to avoid polluting the shared Fuseki instance.
        await store.retract_triple(f"{ns}s", "urn:test:p1", "val1")
        await store.retract_triple(f"{ns}s", "urn:test:p2", "val2")
        await store.retract_triple(f"{ns}s", "urn:test:rel", f"{ns}target", object_is_uri=True)
        await store.aclose()
