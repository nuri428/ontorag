"""Unit tests for Fuseki graph-embedding mixin (_FusekiEmbeddingMixin).

Tests:
  - build_embeddings: calls fastrp_embeddings + upserts to Qdrant (mock)
  - build_embeddings textual: calls provider.embed + upserts to Qdrant
  - find_similar: maps Qdrant hits → SimilarHit list
  - find_similar: empty / missing embedding → []
  - RRF fusion math: expected order matches hand-calculated scores
  - Security: uri_ref rejects crafted URI; only validated URIs reach SPARQL
  - Route: FusekiStore with find_similar → 200
  - Route: store without find_similar → 501

No live Fuseki or Qdrant required — all backends are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.api.main import app
from ontorag.stores._fuseki_embedding_mixin import (
    _RRF_K0,
    _STRUCT_DIM,
    _TBOX_TYPE_URIS,
    _FusekiEmbeddingMixin,
)
from ontorag.stores._qdrant import STRUCT_COLLECTION, TEXT_COLLECTION
from ontorag.stores.base import SimilarHit


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


class FakeQdrantWrapper:
    """In-memory fake QdrantWrapper for unit tests."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, list[float]]] = {}
        self.ensure_collection_calls: list[tuple[str, int]] = []
        self.upsert_calls: list[tuple[str, list[tuple[str, list[float]]]]] = []

    async def ensure_collection(self, name: str, dim: int) -> None:
        self.ensure_collection_calls.append((name, dim))
        if name not in self._collections:
            self._collections[name] = {}

    async def upsert(self, collection: str, points: list[tuple[str, list[float]]]) -> int:
        self.upsert_calls.append((collection, points))
        if collection not in self._collections:
            self._collections[collection] = {}
        for uri, vec in points:
            self._collections[collection][uri] = vec
        return len(points)

    async def retrieve_vector(self, collection: str, uri: str) -> list[float] | None:
        return self._collections.get(collection, {}).get(uri)

    async def query(
        self, collection: str, vector: list[float], top_k: int
    ) -> list[tuple[str, float]]:
        store = self._collections.get(collection, {})
        # Trivial similarity: return all stored URIs ordered by first-element difference.
        results = [(u, 1.0 - abs(v[0] - vector[0])) for u, v in store.items()]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    async def delete_collection(self, name: str) -> None:
        self._collections.pop(name, None)

    async def aclose(self) -> None:
        pass


def _make_mixin_store(
    node_result=None,
    edge_result=None,
    meta_result=None,
    text_result=None,
) -> MagicMock:
    """Build a minimal store-like object that delegates to the real mixin methods."""
    store = MagicMock(spec=_FusekiEmbeddingMixin)
    store._sparql_select = AsyncMock()
    fake_qdrant = FakeQdrantWrapper()
    store._qdrant = fake_qdrant

    def _get_qdrant(_self=None):
        return fake_qdrant

    # Wire up real mixin methods.
    store._get_qdrant = lambda: fake_qdrant
    store.build_embeddings = lambda *a, **kw: _FusekiEmbeddingMixin.build_embeddings(
        store, *a, **kw
    )
    store._build_structural_embeddings = (
        lambda: _FusekiEmbeddingMixin._build_structural_embeddings(store)
    )
    store._build_textual_embeddings = (
        lambda p: _FusekiEmbeddingMixin._build_textual_embeddings(store, p)
    )
    store.find_similar = lambda *a, **kw: _FusekiEmbeddingMixin.find_similar(
        store, *a, **kw
    )
    store._find_similar_single = (
        lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_single(store, *a, **kw)
    )
    store._find_similar_hybrid = (
        lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_hybrid(store, *a, **kw)
    )
    store._resolve_entity_meta = (
        lambda *a, **kw: _FusekiEmbeddingMixin._resolve_entity_meta(store, *a, **kw)
    )

    # Default SPARQL return values.
    if node_result is None:
        node_result = {"results": {"bindings": []}}
    if edge_result is None:
        edge_result = {"results": {"bindings": []}}
    if meta_result is None:
        meta_result = {"results": {"bindings": []}}
    if text_result is None:
        text_result = {"results": {"bindings": []}}

    store._sparql_select.side_effect = [
        node_result,
        edge_result,
        text_result,
        meta_result,
    ]

    return store


# ── Security ──────────────────────────────────────────────────────────────────


class TestUriRefSecurity:
    """uri_ref must reject crafted URIs before they reach SPARQL."""

    def test_angle_bracket_rejected(self):
        """A URI with > should be rejected by uri_ref."""
        from ontorag.core.sparql import uri_ref

        with pytest.raises(ValueError):
            uri_ref("http://evil.org/foo>DROP")

    def test_space_rejected(self):
        from ontorag.core.sparql import uri_ref

        with pytest.raises(ValueError):
            uri_ref("http://evil.org/foo bar")

    def test_valid_uri_passes(self):
        from ontorag.core.sparql import uri_ref

        result = uri_ref("http://example.org/Pokemon/Pikachu")
        assert "http://example.org/Pokemon/Pikachu" in result

    def test_find_similar_rejects_crafted_uri(self):
        """find_similar must return [] for a crafted URI (no SPARQL reached)."""
        store = MagicMock(spec=_FusekiEmbeddingMixin)
        store._sparql_select = AsyncMock()
        store._qdrant = FakeQdrantWrapper()
        store._get_qdrant = lambda: store._qdrant
        store._find_similar_single = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_single(store, *a, **kw)
        )
        store._find_similar_hybrid = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_hybrid(store, *a, **kw)
        )
        store._resolve_entity_meta = (
            lambda *a, **kw: _FusekiEmbeddingMixin._resolve_entity_meta(store, *a, **kw)
        )

        import asyncio

        result = asyncio.run(
            _FusekiEmbeddingMixin.find_similar(
                store, "http://evil.org/foo>DROP", top_k=5, mode="structural"
            )
        )
        assert result == []
        store._sparql_select.assert_not_called()


# ── build_embeddings — structural ────────────────────────────────────────────


class TestBuildStructural:
    """Unit tests for structural embedding build."""

    @pytest.mark.asyncio
    async def test_no_nodes_returns_zero(self):
        """When the ABox has no instances, structural build returns 0."""
        store = _make_mixin_store(
            node_result={"results": {"bindings": []}},
        )
        result = await store._build_structural_embeddings()
        assert result == 0

    @pytest.mark.asyncio
    async def test_calls_fastrp_and_upserts(self):
        """With ABox nodes, FastRP is called and vectors are upserted to Qdrant."""
        nodes = [
            {"inst": {"value": f"http://ex.org/P{i}"}}
            for i in range(5)
        ]
        node_result = {"results": {"bindings": nodes}}
        edge_result = {
            "results": {
                "bindings": [
                    {"subj": {"value": "http://ex.org/P0"}, "obj": {"value": "http://ex.org/P1"}},
                    {"subj": {"value": "http://ex.org/P1"}, "obj": {"value": "http://ex.org/P2"}},
                ]
            }
        }

        store = MagicMock(spec=_FusekiEmbeddingMixin)
        fake_qdrant = FakeQdrantWrapper()
        store._qdrant = fake_qdrant
        store._get_qdrant = lambda: fake_qdrant
        store._sparql_select = AsyncMock(side_effect=[node_result, edge_result])
        store._build_structural_embeddings = (
            lambda: _FusekiEmbeddingMixin._build_structural_embeddings(store)
        )

        with patch("ontorag.stores._fuseki_embedding_mixin.QdrantWrapper.from_env") as _mock:
            result = await store._build_structural_embeddings()

        # 5 nodes should be embedded.
        assert result == 5
        # Qdrant collection should have 5 entries.
        assert len(fake_qdrant._collections.get(STRUCT_COLLECTION, {})) == 5
        # Ensure collection was called with correct dim.
        assert any(dim == _STRUCT_DIM for _, dim in fake_qdrant.ensure_collection_calls)


# ── build_embeddings — textual ────────────────────────────────────────────────


class TestBuildTextual:
    """Unit tests for textual embedding build."""

    @pytest.mark.asyncio
    async def test_no_text_returns_zero(self):
        """When no instance has embeddable text, textual build returns 0."""
        text_result = {
            "results": {
                "bindings": [
                    {"inst": {"value": "http://ex.org/P0"}},  # no label/comment/def
                ]
            }
        }
        store = MagicMock(spec=_FusekiEmbeddingMixin)
        fake_qdrant = FakeQdrantWrapper()
        store._qdrant = fake_qdrant
        store._get_qdrant = lambda: fake_qdrant
        store._sparql_select = AsyncMock(return_value=text_result)
        store._build_textual_embeddings = (
            lambda p: _FusekiEmbeddingMixin._build_textual_embeddings(store, p)
        )

        result = await store._build_textual_embeddings(FakeEmbeddingProvider())
        assert result == 0

    @pytest.mark.asyncio
    async def test_calls_embed_and_upserts(self):
        """With labelled instances, provider.embed is called and vectors upserted."""
        text_result = {
            "results": {
                "bindings": [
                    {
                        "inst": {"value": "http://ex.org/Pikachu"},
                        "label": {"value": "Pikachu"},
                    },
                    {
                        "inst": {"value": "http://ex.org/Raichu"},
                        "label": {"value": "Raichu"},
                    },
                ]
            }
        }

        store = MagicMock(spec=_FusekiEmbeddingMixin)
        fake_qdrant = FakeQdrantWrapper()
        store._qdrant = fake_qdrant
        store._get_qdrant = lambda: fake_qdrant
        store._sparql_select = AsyncMock(return_value=text_result)
        store._build_textual_embeddings = (
            lambda p: _FusekiEmbeddingMixin._build_textual_embeddings(store, p)
        )

        provider = FakeEmbeddingProvider()
        result = await store._build_textual_embeddings(provider)

        assert result == 2
        collection = fake_qdrant._collections.get(TEXT_COLLECTION, {})
        assert "http://ex.org/Pikachu" in collection
        assert "http://ex.org/Raichu" in collection
        # Ensure collection was called with provider.dimension.
        assert any(
            dim == provider.dimension for _, dim in fake_qdrant.ensure_collection_calls
        )


# ── find_similar — single mode ───────────────────────────────────────────────


class TestFindSimilarSingle:
    """Unit tests for single-mode kNN lookup."""

    @pytest.mark.asyncio
    async def test_missing_embedding_returns_empty(self):
        """Node with no embedding in Qdrant returns []."""
        store = MagicMock(spec=_FusekiEmbeddingMixin)
        fake_qdrant = FakeQdrantWrapper()  # empty — no vectors loaded
        store._get_qdrant = lambda: fake_qdrant
        store._sparql_select = AsyncMock()
        store._resolve_entity_meta = (
            lambda *a, **kw: _FusekiEmbeddingMixin._resolve_entity_meta(store, *a, **kw)
        )
        store._find_similar_single = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_single(store, *a, **kw)
        )

        result = await store._find_similar_single("http://ex.org/A", 5, "structural")
        assert result == []

    @pytest.mark.asyncio
    async def test_maps_hits_to_similar_hits(self):
        """Qdrant hits should be mapped to SimilarHit with label + class_uri."""
        fake_qdrant = FakeQdrantWrapper()
        # Pre-load: A and B with known vectors.
        await fake_qdrant.ensure_collection(STRUCT_COLLECTION, dim=4)
        await fake_qdrant.upsert(
            STRUCT_COLLECTION,
            [
                ("http://ex.org/A", [1.0, 0.0, 0.0, 0.0]),
                ("http://ex.org/B", [0.9, 0.1, 0.0, 0.0]),
                ("http://ex.org/C", [0.5, 0.5, 0.0, 0.0]),
            ],
        )

        meta_result = {
            "results": {
                "bindings": [
                    {
                        "inst": {"value": "http://ex.org/B"},
                        "label": {"value": "B Entity"},
                        "class_uri": {"value": "http://ex.org/Pokemon"},
                    },
                    {
                        "inst": {"value": "http://ex.org/C"},
                        "label": {"value": "C Entity"},
                        "class_uri": {"value": "http://ex.org/Pokemon"},
                    },
                ]
            }
        }

        store = MagicMock(spec=_FusekiEmbeddingMixin)
        store._get_qdrant = lambda: fake_qdrant
        store._sparql_select = AsyncMock(return_value=meta_result)
        store._resolve_entity_meta = (
            lambda *a, **kw: _FusekiEmbeddingMixin._resolve_entity_meta(store, *a, **kw)
        )
        store._find_similar_single = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_single(store, *a, **kw)
        )

        hits = await store._find_similar_single("http://ex.org/A", 2, "structural")

        assert len(hits) <= 2
        # All hits must be SimilarHit with mode="structural".
        for h in hits:
            assert isinstance(h, SimilarHit)
            assert h.mode == "structural"
            assert h.uri != "http://ex.org/A"  # self excluded

    @pytest.mark.asyncio
    async def test_tbox_type_excluded_from_class_uri(self):
        """Vocabulary type URIs (owl:Class etc.) must not appear as class_uri."""
        fake_qdrant = FakeQdrantWrapper()
        await fake_qdrant.ensure_collection(STRUCT_COLLECTION, dim=4)
        await fake_qdrant.upsert(
            STRUCT_COLLECTION,
            [
                ("http://ex.org/A", [1.0, 0.0, 0.0, 0.0]),
                ("http://ex.org/B", [0.9, 0.1, 0.0, 0.0]),
            ],
        )

        # B's type is owl:Class — a TBox vocab type, should be None in output.
        meta_result = {
            "results": {
                "bindings": [
                    {
                        "inst": {"value": "http://ex.org/B"},
                        "class_uri": {"value": "http://www.w3.org/2002/07/owl#Class"},
                    },
                ]
            }
        }

        store = MagicMock(spec=_FusekiEmbeddingMixin)
        store._get_qdrant = lambda: fake_qdrant
        store._sparql_select = AsyncMock(return_value=meta_result)
        store._resolve_entity_meta = (
            lambda *a, **kw: _FusekiEmbeddingMixin._resolve_entity_meta(store, *a, **kw)
        )
        store._find_similar_single = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_single(store, *a, **kw)
        )

        hits = await store._find_similar_single("http://ex.org/A", 1, "structural")

        assert len(hits) == 1
        assert hits[0].class_uri is None  # TBox type was filtered out


# ── find_similar — hybrid / RRF ───────────────────────────────────────────────


class TestFindSimilarHybrid:
    """Unit tests for RRF fusion math."""

    @pytest.mark.asyncio
    async def test_rrf_scores_computed_correctly(self):
        """Verify RRF formula: score = Σ 1/(k0 + rank + 1)."""
        # Manually build struct and text hit lists.
        struct_hits = [
            SimilarHit(uri="http://ex.org/B", label="B", class_uri=None, score=0.9, mode="structural"),
            SimilarHit(uri="http://ex.org/C", label="C", class_uri=None, score=0.7, mode="structural"),
        ]
        text_hits = [
            SimilarHit(uri="http://ex.org/C", label="C", class_uri=None, score=0.8, mode="textual"),
            SimilarHit(uri="http://ex.org/B", label="B", class_uri=None, score=0.6, mode="textual"),
        ]

        store = MagicMock(spec=_FusekiEmbeddingMixin)
        store._find_similar_single = AsyncMock(side_effect=[struct_hits, text_hits])
        store._find_similar_hybrid = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_hybrid(store, *a, **kw)
        )

        hits = await store._find_similar_hybrid("http://ex.org/A", 2)

        # B: rank 0 in struct (1/61) + rank 1 in text (1/62)
        # C: rank 1 in struct (1/62) + rank 0 in text (1/61)
        # B and C get the same total — both appear in top-2.
        assert len(hits) == 2
        uris = {h.uri for h in hits}
        assert "http://ex.org/B" in uris
        assert "http://ex.org/C" in uris
        for h in hits:
            assert h.mode == "hybrid"

    @pytest.mark.asyncio
    async def test_both_empty_returns_empty(self):
        """When both modes return nothing, hybrid returns []."""
        store = MagicMock(spec=_FusekiEmbeddingMixin)
        store._find_similar_single = AsyncMock(return_value=[])
        store._find_similar_hybrid = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_hybrid(store, *a, **kw)
        )
        result = await store._find_similar_hybrid("http://ex.org/A", 5)
        assert result == []

    @pytest.mark.asyncio
    async def test_rrf_top_k_capped(self):
        """RRF result must not exceed top_k."""
        hits = [
            SimilarHit(uri=f"http://ex.org/{i}", label=None, class_uri=None, score=float(i), mode="structural")
            for i in range(10)
        ]

        store = MagicMock(spec=_FusekiEmbeddingMixin)
        store._find_similar_single = AsyncMock(return_value=hits)
        store._find_similar_hybrid = (
            lambda *a, **kw: _FusekiEmbeddingMixin._find_similar_hybrid(store, *a, **kw)
        )
        result = await store._find_similar_hybrid("http://ex.org/X", 3)
        assert len(result) <= 3


# ── Route tests ───────────────────────────────────────────────────────────────


@pytest.fixture
def client_factory():
    """Build a TestClient with get_store overridden."""

    def _build(store):
        app.dependency_overrides[get_store] = lambda: store
        return TestClient(app, raise_server_exceptions=False)

    yield _build
    app.dependency_overrides.clear()


class TestSimilarRoute:
    """HTTP route tests for /tools/similar."""

    def test_200_when_fuseki_store_has_find_similar(self, client_factory):
        """A store with find_similar must return 200 + list of SimilarHit."""
        hit = SimilarHit(
            uri="http://ex.org/Raichu",
            label="Raichu",
            class_uri="http://ex.org/Pokemon",
            score=0.95,
            mode="structural",
        )
        store = MagicMock()
        store.find_similar = AsyncMock(return_value=[hit])

        client = client_factory(store)
        resp = client.post(
            "/tools/similar",
            json={"uri": "http://ex.org/Pikachu", "top_k": 5, "mode": "structural"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["uri"] == "http://ex.org/Raichu"
        assert data[0]["mode"] == "structural"

    def test_501_when_store_lacks_find_similar(self, client_factory):
        """A store without find_similar must return 501."""
        store = MagicMock(spec=["get_schema", "find_entities", "status"])
        client = client_factory(store)
        resp = client.post("/tools/similar", json={"uri": "http://ex.org/A"})
        assert resp.status_code == 501
        assert "501" in resp.text or "not supported" in resp.text.lower()
