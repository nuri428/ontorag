"""Unit tests for Fuseki jena-text full-text search mixin.

Tests:
- _escape_lucene_query_for_sparql: safe escaping of SPARQL-breaking characters.
- search_text SPARQL construction via mock _sparql_select.
- SearchHit mapping, deduplication, and vocabulary-type filtering.
- Empty / no-match handling.
- Route 200 dispatch for FusekiStore (store WITH search_text → 200).
- Injection safety: query string must never appear raw in the SPARQL template.

No live Fuseki required for these tests.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.api.main import app
from ontorag.stores._fuseki_search_mixin import (
    _FusekiSearchMixin,
    _escape_lucene_query_for_sparql,
)
from ontorag.stores.base import SearchHit

_NS = "http://example.org/pokemon#"
_D = "http://example.org/pokemon/data#"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client_factory():
    """Build a TestClient with get_store overridden."""

    def _build(store):
        app.dependency_overrides[get_store] = lambda: store
        return TestClient(app, raise_server_exceptions=False)

    yield _build
    app.dependency_overrides.clear()


def _make_fuseki_store(with_search: bool = True) -> MagicMock:
    """Return a MagicMock FusekiStore, optionally with search_text."""
    if with_search:
        store = MagicMock()
        store.search_text = AsyncMock(return_value=[])
        return store
    # spec restricts the mock — search_text absent.
    return MagicMock(spec=["get_schema", "find_entities", "status"])


def _make_mixin() -> _FusekiSearchMixin:
    """Construct a bare _FusekiSearchMixin instance for unit testing."""
    mixin = _FusekiSearchMixin()
    return mixin


def _sparql_result(*bindings: dict) -> dict:
    """Wrap raw binding dicts into the SPARQL JSON result format."""
    return {"results": {"bindings": list(bindings)}}


def _binding(uri: str, score: float, label: str | None = None, type_uri: str | None = None) -> dict:
    """Build a single SPARQL result binding row."""
    b: dict = {
        "inst": {"type": "uri", "value": uri},
        "score": {"type": "literal", "datatype": "http://www.w3.org/2001/XMLSchema#float", "value": str(score)},
    }
    if label is not None:
        b["label"] = {"type": "literal", "value": label}
    if type_uri is not None:
        b["type"] = {"type": "uri", "value": type_uri}
    return b


# ── _escape_lucene_query_for_sparql ──────────────────────────────────────────


class TestEscapeLuceneQueryForSparql:
    """Unit tests for the SPARQL string escaping helper."""

    def test_plain_string_unchanged(self):
        """Plain strings with no special chars pass through unchanged."""
        assert _escape_lucene_query_for_sparql("Pikachu") == "Pikachu"

    def test_korean_label_unchanged(self):
        """Korean Unicode strings require no SPARQL escaping."""
        assert _escape_lucene_query_for_sparql("피카츄") == "피카츄"

    def test_double_quote_escaped(self):
        """Double-quote breaks SPARQL string literal — must be escaped."""
        result = _escape_lucene_query_for_sparql('say "hello"')
        assert '"' not in result or '\\"' in result
        assert "\\\"" in result

    def test_backslash_escaped(self):
        """Backslash must be escaped (and processed first to avoid double-escape)."""
        result = _escape_lucene_query_for_sparql("path\\file")
        assert "\\\\" in result

    def test_newline_escaped(self):
        """Newline must be escaped to \\n."""
        result = _escape_lucene_query_for_sparql("line1\nline2")
        assert "\n" not in result
        assert "\\n" in result

    def test_tab_escaped(self):
        """Tab must be escaped to \\t."""
        result = _escape_lucene_query_for_sparql("col1\tcol2")
        assert "\t" not in result
        assert "\\t" in result

    def test_control_chars_stripped(self):
        """ASCII control characters (0x00-0x08 etc.) are stripped silently."""
        result = _escape_lucene_query_for_sparql("ok\x00nul\x07bel")
        assert "\x00" not in result
        assert "\x07" not in result
        assert "oknulbel" in result

    def test_lucene_wildcards_preserved(self):
        """Lucene wildcard * and ? are not escaped — they are intentional."""
        result = _escape_lucene_query_for_sparql("pika*")
        assert "*" in result

    def test_lucene_boolean_operators_preserved(self):
        """Lucene operators OR, AND, NOT, +, - are preserved."""
        result = _escape_lucene_query_for_sparql("Pikachu OR Raichu")
        assert "OR" in result

    def test_empty_after_stripping_raises(self):
        """A string that is empty after control-char stripping raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            _escape_lucene_query_for_sparql("\x00\x01\x02")

    def test_whitespace_only_raises(self):
        """A query of pure whitespace raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            _escape_lucene_query_for_sparql("   ")


# ── search_text: SPARQL construction and injection safety ────────────────────


class TestSearchTextSparqlConstruction:
    """Verify the SPARQL string is built safely and the query is embedded escaped."""

    def test_query_embedded_as_literal_not_raw(self):
        """Injection attempt must appear escaped in the SPARQL string, not raw."""
        injection = 'evil") UNION { SELECT * WHERE { ?s ?p ?o } } #'
        captured: list[str] = []

        async def fake_sparql(sparql: str) -> dict:
            captured.append(sparql)
            return _sparql_result()

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        asyncio.run(mixin.search_text(injection, limit=5))

        assert captured, "SPARQL was not called"
        # The raw injection string must NOT appear literally in the SPARQL.
        raw_injection = 'evil")'
        for stmt in captured:
            assert raw_injection not in stmt, (
                f"Raw injection found in SPARQL: {stmt!r}"
            )

    def test_query_string_present_in_sparql(self):
        """The (escaped) query term must appear somewhere in the SPARQL."""
        captured: list[str] = []

        async def fake_sparql(sparql: str) -> dict:
            captured.append(sparql)
            return _sparql_result()

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        asyncio.run(mixin.search_text("피카츄", limit=5))

        assert any("피카츄" in s for s in captured)

    def test_class_uri_included_when_provided(self):
        """When class_uri is given, the SPARQL should contain the class filter."""
        class_uri = f"{_NS}Pokemon"
        captured: list[str] = []

        async def fake_sparql(sparql: str) -> dict:
            captured.append(sparql)
            return _sparql_result()

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        asyncio.run(mixin.search_text("피카츄", class_uri=class_uri, limit=5))

        assert any(class_uri in s for s in captured), (
            f"class_uri not found in SPARQL: {captured}"
        )

    def test_internal_limit_is_over_fetched(self):
        """The SPARQL LIMIT should be larger than the caller's limit for deduplication."""
        captured: list[str] = []

        async def fake_sparql(sparql: str) -> dict:
            captured.append(sparql)
            return _sparql_result()

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        asyncio.run(mixin.search_text("x", limit=5))

        # The embedded SPARQL LIMIT should be >= 5 (ideally much larger).
        import re
        for stmt in captured:
            m = re.search(r"LIMIT\s+(\d+)", stmt)
            if m:
                assert int(m.group(1)) > 5, (
                    f"Expected over-fetch (LIMIT > 5) but got LIMIT {m.group(1)}"
                )
                break

    def test_unsafe_class_uri_returns_empty(self):
        """A class_uri containing <> injection chars is rejected safely.

        uri_ref() raises ValueError which is caught by the defensive wrapper,
        so search_text returns [] instead of propagating the error or
        injecting the string into the SPARQL query.
        """
        captured: list[str] = []

        async def fake_sparql(sparql: str) -> dict:
            captured.append(sparql)
            return _sparql_result()

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        # Must NOT raise — defensively returns empty.
        hits = asyncio.run(
            mixin.search_text("x", class_uri="http://evil.com/><script>")
        )

        assert hits == [], "Expected [] for invalid class_uri"
        # The SPARQL must never have been called with the unsafe URI.
        for stmt in captured:
            assert "<script>" not in stmt, (
                f"Injection string found in SPARQL: {stmt!r}"
            )


# ── search_text: SearchHit mapping ───────────────────────────────────────────


class TestSearchTextHitMapping:
    """Verify correct SearchHit construction from SPARQL rows."""

    def test_basic_hit_mapping(self):
        """A single result row maps to a SearchHit with correct fields."""
        row = _binding(f"{_D}Pikachu", 2.38, label="피카츄", type_uri=f"{_NS}Pokemon")

        async def fake_sparql(sparql: str) -> dict:
            return _sparql_result(row)

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("피카츄"))

        assert len(hits) == 1
        h = hits[0]
        assert h.uri == f"{_D}Pikachu"
        assert h.label == "피카츄"
        assert h.class_uri == f"{_NS}Pokemon"
        assert h.score == pytest.approx(2.38, rel=1e-3)

    def test_no_match_returns_empty(self):
        """No rows → empty list (never raises)."""
        async def fake_sparql(sparql: str) -> dict:
            return _sparql_result()

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("nonexistentxyz"))

        assert hits == []

    def test_deduplication_by_uri(self):
        """Multiple rows for the same URI are deduplicated to one hit."""
        uri = f"{_D}Pikachu"
        rows = [
            _binding(uri, 2.38, label="피카츄", type_uri=f"{_NS}Pokemon"),
            _binding(uri, 2.38, label="피카츄", type_uri=f"{_NS}LegendaryPokemon"),
        ]

        async def fake_sparql(sparql: str) -> dict:
            return _sparql_result(*rows)

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("피카츄"))

        assert [h.uri for h in hits].count(uri) == 1

    def test_ordered_by_score_descending(self):
        """Results are sorted by score, highest first."""
        rows = [
            _binding(f"{_D}A", 1.0, label="A"),
            _binding(f"{_D}B", 3.0, label="B"),
            _binding(f"{_D}C", 2.0, label="C"),
        ]

        async def fake_sparql(sparql: str) -> dict:
            return _sparql_result(*rows)

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("x"))

        assert [h.uri for h in hits] == [f"{_D}B", f"{_D}C", f"{_D}A"]

    def test_limit_applied_after_dedup(self):
        """Returned hits are sliced to the caller's limit after deduplication."""
        rows = [_binding(f"{_D}{c}", float(i), label=c) for i, c in enumerate("ABCDE", 1)]

        async def fake_sparql(sparql: str) -> dict:
            return _sparql_result(*rows)

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("x", limit=3))

        assert len(hits) == 3

    def test_vocab_type_excluded_from_class_uri(self):
        """OWL/RDFS vocabulary types are not reported as the instance's class_uri."""
        owl_class = "http://www.w3.org/2002/07/owl#Class"
        real_class = f"{_NS}Pokemon"
        uri = f"{_D}Pikachu"
        rows = [
            _binding(uri, 2.0, label="피카츄", type_uri=owl_class),
            _binding(uri, 2.0, label="피카츄", type_uri=real_class),
        ]

        async def fake_sparql(sparql: str) -> dict:
            return _sparql_result(*rows)

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("피카츄"))

        assert len(hits) == 1
        assert hits[0].class_uri == real_class

    def test_class_uri_none_when_only_vocab_type(self):
        """When the only rdf:type is a vocab type, class_uri is None."""
        owl_class = "http://www.w3.org/2002/07/owl#Class"
        uri = f"{_NS}SomeOntologyClass"
        rows = [_binding(uri, 1.0, label="label", type_uri=owl_class)]

        async def fake_sparql(sparql: str) -> dict:
            return _sparql_result(*rows)

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("x"))

        assert len(hits) == 1
        assert hits[0].class_uri is None

    def test_sparql_error_returns_empty(self):
        """If _sparql_select raises, search_text returns [] instead of propagating."""
        async def fake_sparql(sparql: str) -> dict:
            raise RuntimeError("simulated Fuseki error")

        mixin = _make_mixin()
        mixin._sparql_select = fake_sparql  # type: ignore[assignment]

        hits = asyncio.run(mixin.search_text("피카츄"))

        assert hits == []


# ── Route dispatch: FusekiStore with search_text → 200 ───────────────────────


class TestSearchTextRouteDispatch:
    """Verify the MCP route dispatches correctly to a Fuseki-like store."""

    def test_route_returns_200_for_fuseki_store_with_search_text(self, client_factory):
        """A store with search_text (Fuseki or Neo4j) returns 200."""
        hit = SearchHit(
            uri=f"{_D}Pikachu",
            label="피카츄",
            class_uri=f"{_NS}Pokemon",
            score=2.38,
        )
        store = _make_fuseki_store(with_search=True)
        store.search_text = AsyncMock(return_value=[hit])
        client = client_factory(store)

        resp = client.post("/tools/search/text", json={"query": "피카츄"})

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["uri"] == f"{_D}Pikachu"
        assert body[0]["label"] == "피카츄"
        assert body[0]["score"] == pytest.approx(2.38, rel=1e-3)

    def test_route_returns_501_for_store_without_search_text(self, client_factory):
        """A store without search_text still returns 501."""
        store = _make_fuseki_store(with_search=False)
        client = client_factory(store)

        resp = client.post("/tools/search/text", json={"query": "x"})

        assert resp.status_code == 501

    def test_route_passes_class_uri_to_search_text(self, client_factory):
        """class_uri in the request body is forwarded to store.search_text."""
        store = _make_fuseki_store(with_search=True)
        store.search_text = AsyncMock(return_value=[])
        client = client_factory(store)

        class_uri = f"{_NS}Pokemon"
        resp = client.post(
            "/tools/search/text",
            json={"query": "피카츄", "class_uri": class_uri, "limit": 10},
        )

        assert resp.status_code == 200
        store.search_text.assert_awaited_once_with(
            "피카츄", class_uri, 10, ontology=None
        )

    def test_route_returns_empty_list_on_no_match(self, client_factory):
        """No matches → 200 with empty JSON array."""
        store = _make_fuseki_store(with_search=True)
        store.search_text = AsyncMock(return_value=[])
        client = client_factory(store)

        resp = client.post("/tools/search/text", json={"query": "xyz_no_match"})

        assert resp.status_code == 200
        assert resp.json() == []
