"""Unit tests for Neo4j BM25 full-text search (B2 + B3 + B4).

Tests:
- search_text Cypher construction (mock driver)
- SearchHit mapping and deduplication
- Empty / no-index handling
- Route 501 guard (store WITHOUT search_text → 501)
- Route 200 path (store WITH async search_text → 200 + parsed hits)
- Security regression: $query must be a bound parameter, not interpolated

No live Neo4j required for these tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.api.main import app
from ontorag.stores.base import SearchHit


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client_factory():
    """Build a TestClient with get_store overridden."""

    def _build(store):
        app.dependency_overrides[get_store] = lambda: store
        return TestClient(app, raise_server_exceptions=False)

    yield _build
    app.dependency_overrides.clear()


def _make_store(with_search: bool = True) -> MagicMock:
    """Return a MagicMock store, optionally with search_text attribute."""
    if with_search:
        store = MagicMock()
        store.search_text = AsyncMock(return_value=[])
        return store
    # spec restricts the mock to only have the listed attributes — search_text absent.
    return MagicMock(spec=["get_schema", "find_entities", "status"])


# ── Route 501 guard ───────────────────────────────────────────────────────────


def test_search_text_route_returns_501_for_non_neo4j_backend(client_factory):
    """Store without search_text attribute returns 501."""
    store = _make_store(with_search=False)
    client = client_factory(store)

    resp = client.post("/tools/search/text", json={"query": "Pikachu"})

    assert resp.status_code == 501
    detail = resp.json()["detail"].lower()
    assert "full-text search" in detail or "not supported" in detail


# ── Route 200 path ────────────────────────────────────────────────────────────


def test_search_text_route_returns_hits_when_available(client_factory):
    """Store WITH search_text returns 200 and a list of SearchHit dicts."""
    hit = SearchHit(
        uri="http://example.org/pokemon/data#Pikachu",
        label="Pikachu",
        class_uri="http://example.org/pokemon#Pokemon",
        score=1.5,
    )
    store = _make_store(with_search=True)
    store.search_text = AsyncMock(return_value=[hit])
    client = client_factory(store)

    resp = client.post("/tools/search/text", json={"query": "Pikachu", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["uri"] == "http://example.org/pokemon/data#Pikachu"
    assert body[0]["label"] == "Pikachu"
    assert body[0]["score"] == pytest.approx(1.5)


def test_search_text_route_passes_class_uri_and_limit(client_factory):
    """class_uri and limit from request body are forwarded to store.search_text."""
    store = _make_store(with_search=True)
    store.search_text = AsyncMock(return_value=[])
    client = client_factory(store)

    resp = client.post(
        "/tools/search/text",
        json={
            "query": "fire",
            "class_uri": "http://example.org/pokemon#Pokemon",
            "limit": 10,
        },
    )

    assert resp.status_code == 200
    store.search_text.assert_awaited_once_with(
        "fire",
        "http://example.org/pokemon#Pokemon",
        10,
    )


def test_search_text_route_empty_result(client_factory):
    """No matches returns 200 with an empty list."""
    store = _make_store(with_search=True)
    store.search_text = AsyncMock(return_value=[])
    client = client_factory(store)

    resp = client.post("/tools/search/text", json={"query": "nonexistent_xyz_12345"})

    assert resp.status_code == 200
    assert resp.json() == []


# ── Request validation ────────────────────────────────────────────────────────


def test_search_text_route_rejects_limit_out_of_bounds(client_factory):
    """limit > 200 is rejected with 422 by Pydantic validation."""
    store = _make_store(with_search=True)
    client = client_factory(store)

    resp = client.post("/tools/search/text", json={"query": "x", "limit": 999})

    assert resp.status_code == 422


def test_search_text_route_rejects_limit_zero(client_factory):
    """limit = 0 is rejected with 422."""
    store = _make_store(with_search=True)
    client = client_factory(store)

    resp = client.post("/tools/search/text", json={"query": "x", "limit": 0})

    assert resp.status_code == 422


# ── Security regression: $query is a bound parameter ─────────────────────────


def test_search_text_query_is_bound_parameter():
    """Verify $query is a parameter key, not interpolated into the Cypher string.

    This test inspects the _run call made by search_text to assert that the
    Lucene query string is passed as a named parameter (not concatenated into
    the Cypher).  An injection attempt must not appear in the Cypher template.
    """
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    crafted_query = "Pikachu') RETURN 1 UNION MATCH (n) DETACH DELETE n //"

    captured_cypher: list[str] = []
    captured_params: dict[str, object] = {}

    async def fake_run(cypher: str, **params):
        captured_cypher.append(cypher)
        captured_params.update(params)
        # Simulate index exists (return non-None) for _get_existing_index_properties.
        # Actual search returns empty list.
        return []

    # Build a minimal mixin instance that can exercise search_text.
    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]
    mixin._ensure_prefix_map = AsyncMock()

    # Patch _get_existing_index_properties to return a non-None list (index exists).
    async def fake_existing(self=None):  # noqa: ARG001
        return ["rdfs__label"]

    mixin._get_existing_index_properties = fake_existing  # type: ignore[assignment]

    asyncio.run(mixin.search_text(crafted_query, limit=5))  # type: ignore[arg-type]

    # The crafted injection string MUST appear as a parameter value, NOT in the Cypher.
    assert any(crafted_query in str(v) for v in captured_params.values()), (
        "Injection string not found in bound params — may have been dropped."
    )
    for stmt in captured_cypher:
        assert crafted_query not in stmt, (
            f"Injection string found INSIDE Cypher template: {stmt!r}"
        )
    # Confirm it was passed under the 'search_query' parameter key (renamed from 'query'
    # to avoid collision with the Neo4j driver's session.run(query=...) signature).
    assert captured_params.get("search_query") == crafted_query


# ── Mixin unit: SearchHit mapping ────────────────────────────────────────────


def test_search_hit_mapping_strips_lang_tag():
    """Labels with lang tags (keepLangTag=true) are stripped to plain text."""
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    async def fake_run(cypher: str, **params):
        # Simulate a single result row with a lang-tagged label.
        return [
            {
                "uri": "http://example.org/pokemon/data#Pikachu",
                "raw_label": ["Pikachu@en"],
                "cls_uri": "http://example.org/pokemon#Pokemon",
                "score": 2.0,
            }
        ]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]
    mixin._ensure_prefix_map = AsyncMock()

    async def fake_existing(self=None):  # noqa: ARG001
        return ["rdfs__label"]

    mixin._get_existing_index_properties = fake_existing  # type: ignore[assignment]

    hits = asyncio.run(mixin.search_text("Pikachu"))  # type: ignore[arg-type]

    assert len(hits) == 1
    assert hits[0].label == "Pikachu"
    assert hits[0].score == pytest.approx(2.0)
    assert hits[0].uri == "http://example.org/pokemon/data#Pikachu"


def test_search_text_returns_empty_when_no_index():
    """search_text returns [] when the index doesn't exist (no data loaded)."""
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    mixin = _Neo4jSearchMixin()
    mixin._run = AsyncMock(return_value=[])
    mixin._ensure_prefix_map = AsyncMock()

    async def fake_no_index(self=None):  # noqa: ARG001
        return None  # index does not exist

    mixin._get_existing_index_properties = fake_no_index  # type: ignore[assignment]

    hits = asyncio.run(mixin.search_text("Pikachu"))  # type: ignore[arg-type]

    assert hits == []
    # _run should NOT be called for the actual fulltext query when index is absent.
    mixin._run.assert_not_called()


def test_search_text_deduplicates_by_uri():
    """Multiple rows for the same URI (multi-type node) are deduplicated."""
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    pikachu_uri = "http://example.org/pokemon/data#Pikachu"

    async def fake_run(cypher: str, **params):
        # Two rows for same URI (different rdf:type targets).
        return [
            {
                "uri": pikachu_uri,
                "raw_label": "Pikachu",
                "cls_uri": "http://example.org/pokemon#Pokemon",
                "score": 1.8,
            },
            {
                "uri": pikachu_uri,
                "raw_label": "Pikachu",
                "cls_uri": "http://example.org/pokemon#LegendaryPokemon",
                "score": 1.8,
            },
        ]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]
    mixin._ensure_prefix_map = AsyncMock()

    async def fake_existing(self=None):  # noqa: ARG001
        return ["rdfs__label"]

    mixin._get_existing_index_properties = fake_existing  # type: ignore[assignment]

    hits = asyncio.run(mixin.search_text("Pikachu"))  # type: ignore[arg-type]

    uris = [h.uri for h in hits]
    assert uris.count(pikachu_uri) == 1, "Duplicate URI not deduplicated"


# ── Mixin unit: index discovery ───────────────────────────────────────────────


def test_discover_text_property_keys_filters_non_string():
    """_discover_text_property_keys skips non-string properties (e.g. ints)."""
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    async def fake_run(cypher: str, **params):
        # Simulate the discovery query returning only string keys.
        return [
            {"k": "rdfs__label"},
            {"k": "pk__name"},
        ]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]

    keys = asyncio.run(mixin._discover_text_property_keys())  # type: ignore[arg-type]

    assert "rdfs__label" in keys
    assert "pk__name" in keys
    # rdfs__label should be first.
    assert keys[0] == "rdfs__label"


def test_discover_text_property_keys_rejects_unsafe():
    """Keys that fail _safe_rel validation are silently skipped."""
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    async def fake_run(cypher: str, **params):
        # Include an unsafe key with a backtick to test rejection.
        return [
            {"k": "rdfs__label"},
            {"k": "bad`key"},
            {"k": "ok__key"},
        ]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]

    keys = asyncio.run(mixin._discover_text_property_keys())  # type: ignore[arg-type]

    assert "bad`key" not in keys
    assert "rdfs__label" in keys
    assert "ok__key" in keys


# ── Regression: HIGH #1 — POPULATING / FAILED index state ────────────────────


def _index_row(state: str) -> dict:
    """Build a fake SHOW INDEXES row for the ontorag_fulltext index."""
    return {
        "name": "ontorag_fulltext",
        "type": "FULLTEXT",
        "state": state,
        "properties": ["rdfs__label", "pk__category"],
        "id": 7,
    }


@pytest.mark.parametrize("state", ["POPULATING", "FAILED"])
def test_get_existing_index_properties_none_when_not_online(state):
    """A non-ONLINE index (POPULATING/FAILED) is treated as absent → None.

    Regression for HIGH #1: querying a POPULATING index raises in Neo4j, so
    _get_existing_index_properties must report None for any non-ONLINE state.
    """
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    async def fake_run(cypher: str, **params):
        return [_index_row(state)]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]

    props = asyncio.run(mixin._get_existing_index_properties())  # type: ignore[arg-type]

    assert props is None, f"Expected None for state={state}, got {props}"
    # The ready cache must reflect not-ready.
    assert mixin._fulltext_index_ready is False


def test_get_existing_index_properties_list_when_online():
    """An ONLINE index returns its property list and marks the ready cache."""
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    async def fake_run(cypher: str, **params):
        return [_index_row("ONLINE")]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]

    props = asyncio.run(mixin._get_existing_index_properties())  # type: ignore[arg-type]

    assert props == ["rdfs__label", "pk__category"]
    assert mixin._fulltext_index_ready is True


def test_search_text_returns_empty_when_index_populating():
    """search_text returns [] (never raises) while the index is POPULATING.

    Regression for HIGH #1: the guard now checks ONLINE state, so a populating
    index short-circuits to [] and the fulltext query is never issued.
    """
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    run_calls: list[str] = []

    async def fake_run(cypher: str, **params):
        run_calls.append(cypher)
        if cypher.strip().startswith("SHOW INDEXES"):
            return [_index_row("POPULATING")]
        # Any fulltext query against a populating index would raise in real Neo4j.
        raise AssertionError("fulltext query issued against a POPULATING index")

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]
    mixin._ensure_prefix_map = AsyncMock()

    hits = asyncio.run(mixin.search_text("Pikachu"))  # type: ignore[arg-type]

    assert hits == []
    # Only the SHOW INDEXES probe ran; the fulltext query was skipped.
    assert all(c.strip().startswith("SHOW INDEXES") for c in run_calls)


# ── Regression: HIGH #2 — LIMIT before dedup under-delivers ──────────────────


def test_search_text_limit_after_dedup_full_delivery():
    """Distinct hits up to `limit` are delivered even when nodes have N types.

    Regression for HIGH #2: with limit=2 and each node carrying 3 rdf:type
    rows, the old LIMIT-then-dedup logic returned a single distinct hit.  The
    fix over-fetches (internal cap) then slices the deduped list to `limit`,
    so two distinct hits must be returned.
    """
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    a = "http://example.org/pokemon/data#NodeA"
    b = "http://example.org/pokemon/data#NodeB"
    c = "http://example.org/pokemon/data#NodeC"

    def _rows_for(uri, score, label):
        # Three rdf:type rows per node (multi-type multiplication).
        return [
            {"uri": uri, "raw_label": label, "cls_uri": f"{uri}#T1", "score": score},
            {"uri": uri, "raw_label": label, "cls_uri": f"{uri}#T2", "score": score},
            {"uri": uri, "raw_label": label, "cls_uri": f"{uri}#T3", "score": score},
        ]

    captured_limit: dict[str, int] = {}

    async def fake_run(cypher: str, **params):
        if "limit" in params:
            captured_limit["value"] = params["limit"]
        # Highest score first (matching ORDER BY score DESC).
        return (
            _rows_for(a, 3.0, "A")
            + _rows_for(b, 2.0, "B")
            + _rows_for(c, 1.0, "C")
        )

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]
    mixin._ensure_prefix_map = AsyncMock()

    async def fake_existing(self=None):  # noqa: ARG001
        return ["rdfs__label"]

    mixin._get_existing_index_properties = fake_existing  # type: ignore[assignment]

    hits = asyncio.run(mixin.search_text("x", limit=2))  # type: ignore[arg-type]

    # Two DISTINCT hits requested → two distinct hits delivered.
    assert len(hits) == 2, f"Expected 2 distinct hits, got {len(hits)}"
    assert [h.uri for h in hits] == [a, b], "Top-2 distinct hits by score"
    # The Cypher over-fetched beyond the caller's limit (internal cap > limit).
    assert captured_limit.get("value", 0) > 2


# ── Regression: MEDIUM — class_uri must exclude vocab (TBox) types ───────────


def test_search_hit_excludes_rdfs_class_vocab_type():
    """A node typed rdfs:Class must NOT leak that vocab URI as class_uri.

    Regression for MEDIUM: the old 'owl#' string heuristic let rdfs:Class
    (and other non-OWL vocab types) through.  The fix uses _TBOX_TYPE_URIS.
    """
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    node_uri = "http://example.org/pokemon#SomeClass"
    rdfs_class = "http://www.w3.org/2000/01/rdf-schema#Class"
    real_class = "http://example.org/pokemon#Pokemon"

    async def fake_run(cypher: str, **params):
        return [
            # Vocab type first (rdfs:Class) — must be filtered out.
            {"uri": node_uri, "raw_label": "label", "cls_uri": rdfs_class, "score": 1.0},
            # A genuine ABox class second — should be reported instead.
            {"uri": node_uri, "raw_label": "label", "cls_uri": real_class, "score": 1.0},
        ]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]
    mixin._ensure_prefix_map = AsyncMock()

    async def fake_existing(self=None):  # noqa: ARG001
        return ["rdfs__label"]

    mixin._get_existing_index_properties = fake_existing  # type: ignore[assignment]

    hits = asyncio.run(mixin.search_text("x"))  # type: ignore[arg-type]

    assert len(hits) == 1
    assert hits[0].class_uri == real_class, (
        f"Expected ABox class, got vocab type leak: {hits[0].class_uri}"
    )


def test_search_hit_class_uri_none_when_only_vocab_type():
    """When the only rdf:type is a vocab type, class_uri stays None."""
    import asyncio

    from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin

    node_uri = "http://example.org/pokemon#SomeClass"
    owl_class = "http://www.w3.org/2002/07/owl#Class"

    async def fake_run(cypher: str, **params):
        return [
            {"uri": node_uri, "raw_label": "label", "cls_uri": owl_class, "score": 1.0},
        ]

    mixin = _Neo4jSearchMixin()
    mixin._run = fake_run  # type: ignore[assignment]
    mixin._ensure_prefix_map = AsyncMock()

    async def fake_existing(self=None):  # noqa: ARG001
        return ["rdfs__label"]

    mixin._get_existing_index_properties = fake_existing  # type: ignore[assignment]

    hits = asyncio.run(mixin.search_text("x"))  # type: ignore[arg-type]

    assert len(hits) == 1
    assert hits[0].class_uri is None
