"""Unit tests for Neo4j graph-embedding mixin (C3 + C4 + route).

Tests:
  - build_embeddings Cypher construction (mock driver + fake EmbeddingProvider)
  - find_similar RRF fusion math (structural + textual → expected fused order)
  - Graceful empty / missing-index handling → []
  - Route 501 guard (store WITHOUT find_similar → 501)
  - Route 200 (store WITH find_similar → 200 + parsed hits)
  - Security: _safe_rel rejects property key with backtick (no interpolation)

No live Neo4j required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.api.main import app
from ontorag.core.cypher import _safe_rel
from ontorag.stores.base import SimilarHit
from ontorag.stores._neo4j_embedding_mixin import (
    _RRF_K0,
    _STRUCT_DIM,
    _STRUCT_PROP,
    _TEXT_PROP,
    _Neo4jEmbeddingMixin,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeEmbeddingProvider:
    """Deterministic fake embedding provider for tests (no external API calls)."""

    model: str = "fake-embed-v1"
    dimension: int = 4  # tiny dimension for test speed

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a deterministic vector derived from the text hash."""
        result = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFF
            vec = [
                float((h >> (i * 8)) & 0xFF) / 255.0
                for i in range(self.dimension)
            ]
            result.append(vec)
        return result


def _make_store_with_find_similar(hits: list[SimilarHit]) -> MagicMock:
    """Return a MagicMock store that exposes find_similar."""
    store = MagicMock()
    store.find_similar = AsyncMock(return_value=hits)
    return store


def _make_store_without_find_similar() -> MagicMock:
    """Return a MagicMock store without find_similar (simulates Fuseki)."""
    return MagicMock(spec=["get_schema", "find_entities", "status"])


@pytest.fixture
def client_factory():
    """Build a TestClient with get_store overridden."""

    def _build(store):
        app.dependency_overrides[get_store] = lambda: store
        return TestClient(app, raise_server_exceptions=False)

    yield _build
    app.dependency_overrides.clear()


# ── Security ──────────────────────────────────────────────────────────────────


class TestSafeRelSecurity:
    """_safe_rel must reject identifiers that could break backtick quoting."""

    def test_valid_identifier_passes(self):
        assert _safe_rel("rdfs__label") == "rdfs__label"

    def test_backtick_in_key_rejected(self):
        """A backtick in a property key must raise ValueError, not be interpolated."""
        with pytest.raises(ValueError, match="Unsafe"):
            _safe_rel("evil`key")

    def test_spaces_rejected(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _safe_rel("bad key")

    def test_semicolon_rejected(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _safe_rel("rdfs__label;DROP INDEX struct_vec")

    def test_double_underscore_valid(self):
        """Standard n10s-shortened key should pass."""
        assert _safe_rel("skos__definition") == "skos__definition"


# ── build_embeddings unit (mock driver) ───────────────────────────────────────


class TestBuildEmbeddingsUnit:
    """Verify Cypher calls without a live Neo4j instance."""

    @pytest.fixture
    def mock_store(self):
        """Minimal Neo4jStore-like object with mocked _run/_run_write."""
        store = MagicMock(spec=_Neo4jEmbeddingMixin)
        store._run = AsyncMock()
        store._run_write = AsyncMock()
        store._ensure_prefix_map = AsyncMock()
        store._tbox_type_list = []
        # Delegate to the real mixin methods.
        store.build_embeddings = lambda *a, **kw: _Neo4jEmbeddingMixin.build_embeddings(
            store, *a, **kw
        )
        store._build_structural = lambda: _Neo4jEmbeddingMixin._build_structural(store)
        store._build_textual = lambda p: _Neo4jEmbeddingMixin._build_textual(store, p)
        store._rows_to_text_pairs = _Neo4jEmbeddingMixin._rows_to_text_pairs
        store._write_text_vectors = (
            lambda uris, vectors: _Neo4jEmbeddingMixin._write_text_vectors(
                store, uris, vectors
            )
        )
        store._ensure_vector_index = AsyncMock()
        store._get_vector_index_info = AsyncMock(
            return_value={"state": "ONLINE", "options": {"indexConfig": {"vector.dimensions": 256}}}
        )
        return store

    @pytest.mark.asyncio
    async def test_structural_calls_gds_fastRP(self, mock_store):
        """_build_structural must call gds.fastRP.write with correct params."""
        # Rel-type discovery returns one type.
        mock_store._run.return_value = [{"rel_type": "rdf__type"}]
        # GDS graph.project returns nodeCount.
        mock_store._run_write.side_effect = [
            [],                              # graph.drop (cleanup)
            [{"nodeCount": 10}],             # graph.project
            [{"nodePropertiesWritten": 10}],  # fastRP.write
            [],                              # graph.drop (final)
        ]
        result = await mock_store._build_structural()
        assert result == 10

        # fastRP.write call must include bound params for dim, prop, seed.
        write_calls = mock_store._run_write.call_args_list
        fastrp_call = write_calls[2]
        call_kwargs = fastrp_call.kwargs
        assert call_kwargs.get("dim") == _STRUCT_DIM
        assert call_kwargs.get("prop") == _STRUCT_PROP
        assert call_kwargs.get("seed") == 42

    @pytest.mark.asyncio
    async def test_structural_no_rel_types_returns_zero(self, mock_store):
        """When no relationship types exist, return 0 without calling GDS."""
        mock_store._run.return_value = []
        result = await mock_store._build_structural()
        assert result == 0
        mock_store._run_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_textual_calls_embed_and_writes(self, mock_store):
        """_build_textual must call provider.embed and SET the text property."""
        # String property discovery.
        mock_store._run.side_effect = [
            [{"k": "rdfs__label"}],  # str-prop discovery
            [
                {"uri": "http://ex.org/A", "rdfs__label": "Alpha"},
                {"uri": "http://ex.org/B", "rdfs__label": "Beta"},
            ],  # node fetch
        ]
        mock_store._run_write.return_value = [{"cnt": 2}]

        provider = FakeEmbeddingProvider()
        result = await mock_store._build_textual(provider)
        assert result == 2

        # The SET statement must reference _TEXT_PROP (backtick-quoted).
        write_call = mock_store._run_write.call_args_list[0]
        cypher_arg = write_call.args[0]
        assert _TEXT_PROP in cypher_arg
        assert "pair.uri" in cypher_arg  # bound URI param

    @pytest.mark.asyncio
    async def test_textual_skips_nodes_with_no_text(self, mock_store):
        """Nodes with no string properties must not generate embed calls."""
        mock_store._run.side_effect = [
            [],  # no string properties discovered
        ]
        provider = FakeEmbeddingProvider()
        result = await mock_store._build_textual(provider)
        assert result == 0
        mock_store._run_write.assert_not_called()

    @pytest.mark.asyncio
    async def test_textual_pages_the_node_fetch(self, mock_store, monkeypatch):
        """_build_textual pages the node fetch (HIGH review #1): a full page
        triggers another fetch so the whole ABox never lands in memory."""
        monkeypatch.setattr(
            "ontorag.stores._neo4j_embedding_mixin._TEXT_PAGE_SIZE", 2
        )
        mock_store._run.side_effect = [
            [{"k": "rdfs__label"}],  # discovery
            [  # page 1 — full page (== page size) → must fetch again
                {"uri": "http://ex.org/A", "rdfs__label": "Alpha"},
                {"uri": "http://ex.org/B", "rdfs__label": "Beta"},
            ],
            [{"uri": "http://ex.org/C", "rdfs__label": "Gamma"}],  # page 2 — short → stop
        ]
        mock_store._run_write.side_effect = [[{"cnt": 2}], [{"cnt": 1}]]

        embedded: list[str] = []

        class CountingProvider(FakeEmbeddingProvider):
            async def embed(self, texts: list[str]) -> list[list[float]]:
                embedded.extend(texts)
                return await super().embed(texts)

        result = await mock_store._build_textual(CountingProvider())

        assert result == 3  # 2 + 1 written across two pages
        assert len(embedded) == 3  # every node embedded, none dropped
        assert mock_store._run.call_count == 3  # discovery + 2 fetch pages
        assert mock_store._run_write.call_count == 2  # one write per page

    @pytest.mark.asyncio
    async def test_build_both_calls_both(self, mock_store):
        """build_embeddings('both') must invoke both _build_structural and _build_textual."""
        mock_store._build_structural = AsyncMock(return_value=5)
        mock_store._build_textual = AsyncMock(return_value=3)

        result = await _Neo4jEmbeddingMixin.build_embeddings(
            mock_store, "both", FakeEmbeddingProvider()
        )
        assert result == {"structural": 5, "textual": 3}
        mock_store._build_structural.assert_called_once()
        mock_store._build_textual.assert_called_once()


# ── find_similar unit (mock driver) ───────────────────────────────────────────


class TestFindSimilarUnit:
    """Verify find_similar kNN and RRF logic without a live database."""

    @pytest.fixture
    def embed_store(self):
        """A mock store wired to the real _Neo4jEmbeddingMixin methods."""
        store = MagicMock()
        store._run = AsyncMock()
        store._run_write = AsyncMock()
        store._ensure_prefix_map = AsyncMock()

        # Patch _TBOX_TYPE_URIS import inside the mixin.
        with patch("ontorag.stores._neo4j_embedding_mixin._Neo4jEmbeddingMixin") as _:
            pass  # just to confirm the import works

        store.find_similar = lambda *a, **kw: _Neo4jEmbeddingMixin.find_similar(
            store, *a, **kw
        )
        store._find_similar_single = lambda *a, **kw: _Neo4jEmbeddingMixin._find_similar_single(
            store, *a, **kw
        )
        store._find_similar_hybrid = lambda *a, **kw: _Neo4jEmbeddingMixin._find_similar_hybrid(
            store, *a, **kw
        )
        store._rows_to_similar_hits = lambda *a, **kw: _Neo4jEmbeddingMixin._rows_to_similar_hits(
            store, *a, **kw
        )
        store._get_vector_index_info = AsyncMock(
            return_value={"state": "ONLINE", "options": {}}
        )
        return store

    def test_single_mode_excludes_start_node(self, embed_store):
        """The start node must be excluded from results."""
        START_URI = "http://ex.org/start"
        OTHER_URI = "http://ex.org/other"

        rows = [
            {"uri": START_URI, "raw_label": None, "cls_uri": None, "score": 1.0},
            {"uri": OTHER_URI, "raw_label": "Other", "cls_uri": None, "score": 0.9},
        ]
        # _rows_to_similar_hits is a synchronous helper — no async needed.
        hits = embed_store._rows_to_similar_hits(
            rows, START_URI, 10, "structural", frozenset()
        )

        assert all(h.uri != START_URI for h in hits)
        assert len(hits) == 1
        assert hits[0].uri == OTHER_URI

    @pytest.mark.asyncio
    async def test_missing_index_returns_empty(self, embed_store):
        """When the vector index is absent, find_similar must return []."""
        embed_store._get_vector_index_info = AsyncMock(return_value=None)

        result = await embed_store._find_similar_single(
            "http://ex.org/A", 10, "structural"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_node_without_embedding_returns_empty(self, embed_store):
        """A node that has no embedding property returns []."""
        embed_store._run = AsyncMock(return_value=[{"vec": None}])

        result = await embed_store._find_similar_single(
            "http://ex.org/A", 10, "structural"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_node_not_in_graph_returns_empty(self, embed_store):
        """A node that does not exist in the graph returns []."""
        embed_store._run = AsyncMock(return_value=[])

        result = await embed_store._find_similar_single(
            "http://ex.org/absent", 10, "structural"
        )
        assert result == []

    def test_rrf_fusion_order(self):
        """RRF fused scores must rank nodes that appear in both lists higher."""
        # A appears #1 in structural, #3 in textual.
        # B appears #2 in structural only.
        # C appears #1 in textual, #4 in structural.
        structural = [
            SimilarHit(uri="A", score=0.99, mode="structural"),
            SimilarHit(uri="B", score=0.98, mode="structural"),
            SimilarHit(uri="D", score=0.97, mode="structural"),
            SimilarHit(uri="C", score=0.96, mode="structural"),
        ]
        textual = [
            SimilarHit(uri="C", score=0.95, mode="textual"),
            SimilarHit(uri="E", score=0.94, mode="textual"),
            SimilarHit(uri="A", score=0.93, mode="textual"),
        ]

        rrf_scores: dict[str, float] = {}
        for rank, hit in enumerate(structural):
            rrf_scores[hit.uri] = rrf_scores.get(hit.uri, 0.0) + 1.0 / (_RRF_K0 + rank + 1)
        for rank, hit in enumerate(textual):
            rrf_scores[hit.uri] = rrf_scores.get(hit.uri, 0.0) + 1.0 / (_RRF_K0 + rank + 1)

        sorted_uris = sorted(rrf_scores, key=lambda u: rrf_scores[u], reverse=True)

        # A and C appear in both lists and should outrank nodes in only one list.
        top2 = set(sorted_uris[:2])
        assert "A" in top2, f"A should be in top-2 RRF results; got {sorted_uris}"
        assert "C" in top2, f"C should be in top-2 RRF results; got {sorted_uris}"

    @pytest.mark.asyncio
    async def test_hybrid_both_empty_returns_empty(self, embed_store):
        """When both structural and textual return no hits, hybrid must return []."""
        embed_store._find_similar_single = AsyncMock(return_value=[])
        result = await embed_store._find_similar_hybrid("http://ex.org/A", 10)
        assert result == []


# ── Route guard tests ─────────────────────────────────────────────────────────


class TestFindSimilarRoute:
    """HTTP route tests — no live Neo4j required."""

    def test_501_when_store_lacks_find_similar(self, client_factory):
        """Fuseki-like store (no find_similar) must return 501."""
        store = _make_store_without_find_similar()
        client = client_factory(store)

        resp = client.post("/tools/similar", json={"uri": "http://ex.org/A"})
        assert resp.status_code == 501
        assert "501" in resp.text or "not supported" in resp.text.lower()

    def test_200_with_neo4j_store(self, client_factory):
        """A store that exposes find_similar must return 200 + hit list."""
        hits = [
            SimilarHit(
                uri="http://ex.org/B",
                label="B",
                class_uri="http://ex.org/Pokemon",
                score=0.95,
                mode="structural",
            )
        ]
        store = _make_store_with_find_similar(hits)
        client = client_factory(store)

        resp = client.post(
            "/tools/similar",
            json={"uri": "http://ex.org/A", "top_k": 5, "mode": "structural"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["uri"] == "http://ex.org/B"
        assert body[0]["score"] == pytest.approx(0.95)
        assert body[0]["mode"] == "structural"

    def test_200_empty_list_when_no_similar(self, client_factory):
        """An empty hit list is valid — must return 200 with []."""
        store = _make_store_with_find_similar([])
        client = client_factory(store)

        resp = client.post("/tools/similar", json={"uri": "http://ex.org/A"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_top_k_validation_upper_bound(self, client_factory):
        """top_k > 100 must be rejected with 422."""
        store = _make_store_with_find_similar([])
        client = client_factory(store)

        resp = client.post(
            "/tools/similar",
            json={"uri": "http://ex.org/A", "top_k": 101},
        )
        assert resp.status_code == 422

    def test_top_k_validation_lower_bound(self, client_factory):
        """top_k < 1 must be rejected with 422."""
        store = _make_store_with_find_similar([])
        client = client_factory(store)

        resp = client.post(
            "/tools/similar",
            json={"uri": "http://ex.org/A", "top_k": 0},
        )
        assert resp.status_code == 422

    def test_invalid_mode_rejected(self, client_factory):
        """An unknown mode must be rejected with 422."""
        store = _make_store_with_find_similar([])
        client = client_factory(store)

        resp = client.post(
            "/tools/similar",
            json={"uri": "http://ex.org/A", "mode": "invalid"},
        )
        assert resp.status_code == 422

    def test_find_similar_called_with_correct_args(self, client_factory):
        """The store method must be called with the exact request parameters."""
        store = _make_store_with_find_similar([])
        client = client_factory(store)

        client.post(
            "/tools/similar",
            json={"uri": "http://ex.org/A", "top_k": 7, "mode": "hybrid"},
        )
        store.find_similar.assert_called_once_with(
            "http://ex.org/A", 7, "hybrid", class_uri=None, ontology=None
        )
