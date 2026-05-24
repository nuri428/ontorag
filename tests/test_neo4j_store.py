"""Unit tests for Neo4jStore — mock the driver/session.

Tests: shorten/expand mapping, pattern_to_cypher, factory wiring, mode detection.
No live Neo4j required for these tests.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ontorag.core.cypher import (
    _is_literal,
    _is_var,
    _parse_literal_value,
    pattern_to_cypher,
)
from ontorag.stores.base import PatternFilter, PatternQuery, PatternTriple


# ── pattern_to_cypher ─────────────────────────────────────────────────────────


class TestPatternToCypher:
    """Tests for the Cypher DSL translator."""

    def _shorten(self, uri: str) -> str:
        """Minimal shorten stub: pk:Pokemon → pk__Pokemon, full URI → prefix__local."""
        prefixes = {
            "http://example.org/pokemon#": "pk",
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
            "http://www.w3.org/2000/01/rdf-schema#": "rdfs",
            "http://www.w3.org/2002/07/owl#": "owl",
        }
        for ns, prefix in prefixes.items():
            if uri.startswith(ns):
                return f"{prefix}__{uri[len(ns):]}"
        # Already prefixed name like rdf:type
        if ":" in uri and "://" not in uri and not uri.startswith("?") and not uri.startswith("<"):
            p, l = uri.split(":", 1)
            ns_map = {
                "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                "owl": "http://www.w3.org/2002/07/owl#",
                "pk": "http://example.org/pokemon#",
            }
            if p in ns_map:
                return f"{p}__{l}"
        # Angle-bracketed URI
        if uri.startswith("<") and uri.endswith(">"):
            inner = uri[1:-1]
            return self._shorten(inner)
        return uri

    def test_simple_rdf_type_pattern(self) -> None:
        """rdf:type pattern should produce a label-based node match."""
        query = PatternQuery(
            select=["?inst"],
            where=[
                PatternTriple(
                    s="?inst",
                    p="rdf:type",
                    o="<http://example.org/pokemon#Pokemon>",
                )
            ],
        )
        cypher, params = pattern_to_cypher(query, shorten_fn=self._shorten)
        assert "pk__Pokemon" in cypher
        assert "?inst" not in cypher  # SPARQL variables stripped to Cypher vars

    def test_variable_predicate(self) -> None:
        """Variable predicate should produce a rel-less MATCH."""
        query = PatternQuery(
            select=["?s", "?o"],
            where=[
                PatternTriple(s="?s", p="?p", o="?o"),
            ],
        )
        cypher, params = pattern_to_cypher(query, shorten_fn=self._shorten)
        assert "RETURN" in cypher
        assert "LIMIT" in cypher

    def test_with_filter(self) -> None:
        """Filter condition should appear in WHERE clause."""
        query = PatternQuery(
            select=["?inst"],
            where=[PatternTriple(s="?inst", p="rdf:type", o="<http://example.org/pokemon#Pokemon>")],
            filters=[PatternFilter(var="?inst", op=">", value=0)],
        )
        cypher, params = pattern_to_cypher(query, shorten_fn=self._shorten)
        assert "WHERE" in cypher
        assert "inst" in cypher

    def test_distinct_flag(self) -> None:
        """distinct=True should insert DISTINCT keyword in RETURN clause."""
        query = PatternQuery(
            select=["?inst"],
            where=[PatternTriple(s="?inst", p="rdf:type", o="<http://example.org/pokemon#Pokemon>")],
            distinct=True,
        )
        cypher, _ = pattern_to_cypher(query, shorten_fn=self._shorten)
        assert "DISTINCT" in cypher

    def test_limit_and_offset(self) -> None:
        """LIMIT and SKIP clauses should be present."""
        query = PatternQuery(
            select=["?inst"],
            where=[PatternTriple(s="?inst", p="rdf:type", o="<http://example.org/pokemon#Pokemon>")],
            limit=42,
            offset=10,
        )
        cypher, _ = pattern_to_cypher(query, shorten_fn=self._shorten)
        assert "LIMIT 42" in cypher
        assert "SKIP 10" in cypher

    def test_returns_params_dict(self) -> None:
        """Returned params dict should be a dict (possibly empty)."""
        query = PatternQuery(
            select=["?x"],
            where=[PatternTriple(s="?x", p="rdf:type", o="<http://example.org/pokemon#Move>")],
        )
        _, params = pattern_to_cypher(query, shorten_fn=self._shorten)
        assert isinstance(params, dict)

    def test_object_relationship_pattern(self) -> None:
        """Object-property triple should produce a relationship match."""
        query = PatternQuery(
            select=["?inst", "?type"],
            where=[
                PatternTriple(
                    s="?inst",
                    p="<http://example.org/pokemon#hasType>",
                    o="?type",
                )
            ],
        )
        cypher, _ = pattern_to_cypher(query, shorten_fn=self._shorten)
        assert "pk__hasType" in cypher


# ── _is_var / _is_literal / _parse_literal_value ─────────────────────────────


class TestTermHelpers:
    """Unit tests for term classification helpers."""

    def test_is_var_positive(self) -> None:
        assert _is_var("?foo") is True
        assert _is_var("?someVar123") is True

    def test_is_var_negative(self) -> None:
        assert _is_var("rdf:type") is False
        assert _is_var("<http://foo>") is False
        assert _is_var('"hello"') is False

    def test_is_literal_string(self) -> None:
        assert _is_literal('"hello"') is True
        assert _is_literal('"hello"@en') is True

    def test_is_literal_numeric(self) -> None:
        assert _is_literal("42") is True
        assert _is_literal("3.14") is True
        assert _is_literal("-1") is True

    def test_is_literal_boolean(self) -> None:
        assert _is_literal("true") is True
        assert _is_literal("false") is True

    def test_is_literal_negative(self) -> None:
        assert _is_literal("?var") is False
        assert _is_literal("<http://foo>") is False

    def test_parse_string_literal(self) -> None:
        assert _parse_literal_value('"hello"') == "hello"

    def test_parse_numeric_literal(self) -> None:
        assert _parse_literal_value("42") == 42
        assert _parse_literal_value("3.14") == 3.14

    def test_parse_boolean(self) -> None:
        assert _parse_literal_value("true") is True
        assert _parse_literal_value("false") is False


# ── _safe_rel — Cypher identifier injection guard (review #2) ──────────────────


class TestSafeRel:
    """Regression tests for the Cypher rel-type/label/prop-key injection guard."""

    def test_accepts_valid_shortened(self) -> None:
        from ontorag.core.cypher import _safe_rel  # noqa: PLC0415

        assert _safe_rel("pk__hasType") == "pk__hasType"
        assert _safe_rel("rdf__type") == "rdf__type"
        assert _safe_rel("pk__national_dex") == "pk__national_dex"
        # local parts with dots/hyphens are valid n10s local names
        assert _safe_rel("pk__has.type-x") == "pk__has.type-x"

    def test_rejects_backtick(self) -> None:
        """A value containing a backtick must raise ValueError, not interpolate."""
        from ontorag.core.cypher import _safe_rel  # noqa: PLC0415

        with pytest.raises(ValueError, match="Unsafe Cypher identifier"):
            _safe_rel("pk__x`]->() DETACH DELETE (n) //")

    def test_rejects_missing_double_underscore(self) -> None:
        from ontorag.core.cypher import _safe_rel  # noqa: PLC0415

        with pytest.raises(ValueError):
            _safe_rel("noprefix")

    def test_rejects_whitespace_and_braces(self) -> None:
        from ontorag.core.cypher import _safe_rel  # noqa: PLC0415

        for bad in ("pk__a b", "pk__a{b}", "pk__a)b", "pk__a'b"):
            with pytest.raises(ValueError):
                _safe_rel(bad)


# ── Shorten / expand via Neo4jStore instance ──────────────────────────────────


class TestNeo4jStoreMapping:
    """Unit tests for the shorten/expand URI mapping in Neo4jStore."""

    def _make_store(self) -> "Neo4jStore":
        """Create a Neo4jStore with a mocked driver and a pre-seeded prefix map."""
        import neo4j  # noqa: PLC0415
        from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

        original_driver = neo4j.AsyncGraphDatabase.driver
        mock_driver = MagicMock()
        neo4j.AsyncGraphDatabase.driver = MagicMock(return_value=mock_driver)
        try:
            store = Neo4jStore(
                uri="bolt://localhost:7687",
                user="neo4j",
                password="test",
            )
        finally:
            neo4j.AsyncGraphDatabase.driver = original_driver

        # Seed prefix map directly (skip DB call)
        store._prefix_to_ns = {
            "pk": "http://example.org/pokemon#",
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "owl": "http://www.w3.org/2002/07/owl#",
        }
        store._ns_to_prefix = {v: k for k, v in store._prefix_to_ns.items()}
        store._prefix_map_loaded = True
        return store

    def test_shorten_full_uri(self) -> None:
        store = self._make_store()
        assert store._shorten("http://example.org/pokemon#Pokemon") == "pk__Pokemon"

    def test_shorten_rdf_type(self) -> None:
        store = self._make_store()
        assert store._shorten("http://www.w3.org/1999/02/22-rdf-syntax-ns#type") == "rdf__type"

    def test_shorten_unknown_uri_identity(self) -> None:
        store = self._make_store()
        result = store._shorten("http://unknown.example.org/foo")
        assert result == "http://unknown.example.org/foo"

    def test_expand_shortened(self) -> None:
        store = self._make_store()
        assert store._expand("pk__Pokemon") == "http://example.org/pokemon#Pokemon"

    def test_expand_rdf_type(self) -> None:
        store = self._make_store()
        assert store._expand("rdf__type") == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

    def test_expand_unknown_identity(self) -> None:
        store = self._make_store()
        result = store._expand("unknown__Foo")
        assert result == "unknown__Foo"

    def test_shorten_prefixed_name(self) -> None:
        store = self._make_store()
        result = store._shorten_prefixed("pk:Pokemon")
        assert result == "pk__Pokemon"

    def test_shorten_angle_bracketed_uri(self) -> None:
        store = self._make_store()
        result = store._shorten_prefixed("<http://example.org/pokemon#Pokemon>")
        assert result == "pk__Pokemon"

    @pytest.mark.asyncio
    async def test_aggregate_backtick_predicate_raises(self) -> None:
        """Review #2: a predicate shortening to a backtick value raises, not executes.

        _safe_rel validation must fire BEFORE any Cypher is sent to the DB.
        We assert ValueError and that _run was never awaited.
        """
        store = self._make_store()
        store._run = AsyncMock()
        store._ensure_prefix_map = AsyncMock()

        # An unknown URI containing a backtick → _shorten_prefixed returns it
        # verbatim → _safe_rel must reject it.
        evil_pred = "http://x#a`]->() DETACH DELETE (n) //"
        with pytest.raises(ValueError, match="Unsafe Cypher identifier"):
            await store.aggregate(
                "http://example.org/pokemon#Pokemon", evil_pred
            )
        store._run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_find_related_backtick_predicate_raises(self) -> None:
        """Review #2: find_related rejects a backtick-bearing predicate."""
        store = self._make_store()
        store._run = AsyncMock()
        store._ensure_prefix_map = AsyncMock()

        evil_pred = "http://x#p`]-() //"
        with pytest.raises(ValueError, match="Unsafe Cypher identifier"):
            await store.find_related(
                "http://example.org/pokemon#Pokemon",
                evil_pred,
                "http://example.org/pokemon#Trainer",
            )
        store._run.assert_not_awaited()


# ── Factory wiring ────────────────────────────────────────────────────────────


class TestFactory:
    """Unit tests for create_store factory with GRAPH_STORE=neo4j."""

    def test_fuseki_backend_is_default(self) -> None:
        """Without GRAPH_STORE env var, factory returns FusekiStore."""
        from ontorag.stores.factory import create_store  # noqa: PLC0415
        from ontorag.stores.fuseki import FusekiStore  # noqa: PLC0415

        env_backup = os.environ.pop("GRAPH_STORE", None)
        try:
            store = create_store()
            assert isinstance(store, FusekiStore)
        finally:
            if env_backup is not None:
                os.environ["GRAPH_STORE"] = env_backup

    def test_neo4j_backend_returns_neo4j_store(self) -> None:
        """GRAPH_STORE=neo4j returns Neo4jStore (with mocked driver)."""
        import neo4j  # noqa: PLC0415
        from ontorag.stores.factory import create_store  # noqa: PLC0415
        from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

        original_driver = neo4j.AsyncGraphDatabase.driver
        neo4j.AsyncGraphDatabase.driver = MagicMock(return_value=MagicMock())
        os.environ["GRAPH_STORE"] = "neo4j"
        os.environ.setdefault("NEO4J_PASSWORD", "test")
        try:
            store = create_store()
            assert isinstance(store, Neo4jStore)
        finally:
            del os.environ["GRAPH_STORE"]
            neo4j.AsyncGraphDatabase.driver = original_driver

    def test_unknown_backend_raises(self) -> None:
        """Unknown GRAPH_STORE value raises ValueError."""
        from ontorag.stores.factory import create_store  # noqa: PLC0415

        os.environ["GRAPH_STORE"] = "invalid_backend_xyz"
        try:
            with pytest.raises(ValueError, match="Unknown GRAPH_STORE"):
                create_store()
        finally:
            del os.environ["GRAPH_STORE"]
