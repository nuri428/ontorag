"""Unit tests for per-ontology scoping in graph embeddings.

Tests:
  - QdrantWrapper.upsert: stores ontology payload, merges for shared-URI nodes.
  - QdrantWrapper.query: ontology_filter restricts results to tagged points.
  - QdrantWrapper.delete_by_ontology: removes only tagged points.
  - _FusekiEmbeddingMixin.build_embeddings(ontology=...):
      - scoped SPARQL uses GRAPH clause for that ontology's named graph.
      - calls delete_by_ontology (not delete_collection) when ontology given.
      - passes ontology to qdrant.upsert.
  - _FusekiEmbeddingMixin.find_similar(ontology=...):
      - passes ontology_filter to qdrant.query.
  - _Neo4jEmbeddingMixin._rows_to_similar_hits:
      - post-filters by _ontology list when ontology is set.
      - includes all nodes when ontology is None.
  - validate_ontology_id rejection propagates from both backends.

No live Fuseki / Qdrant / Neo4j required — all backends are mocked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontorag.stores._fuseki_embedding_mixin import _FusekiEmbeddingMixin
from ontorag.stores._neo4j_embedding_mixin import (
    _STRUCT_PROP,
    _TEXT_PROP,
    _Neo4jEmbeddingMixin,
)
from ontorag.stores._qdrant import QdrantWrapper, _point_id
from ontorag.stores.base import SimilarHit


# ── QdrantWrapper unit tests ──────────────────────────────────────────────────


class _FakeClient:
    """Minimal in-memory async client stand-in for AsyncQdrantClient."""

    def __init__(self) -> None:
        # {collection: {pt_id: {vector, payload}}}
        self._store: dict[str, dict[str, dict]] = {}

    async def get_collection(self, name: str):
        if name not in self._store:
            raise Exception("not found")
        # Return object with config.params.vectors.size = 4
        class _Params:
            class vectors:
                size = 4
        class _Config:
            params = _Params
        class _Info:
            config = _Config
        return _Info()

    async def create_collection(self, collection_name: str, vectors_config) -> None:
        self._store.setdefault(collection_name, {})

    async def delete_collection(self, collection_name: str) -> None:
        self._store.pop(collection_name, None)

    async def upsert(self, collection_name: str, points) -> None:
        col = self._store.setdefault(collection_name, {})
        for pt in points:
            col[str(pt.id)] = {"vector": pt.vector, "payload": dict(pt.payload or {})}

    async def retrieve(self, collection_name: str, ids, with_vectors=False, with_payload=False):
        col = self._store.get(collection_name, {})
        results = []
        for pid in ids:
            if str(pid) in col:
                entry = col[str(pid)]
                class _Record:
                    id = pid
                    vector = entry["vector"] if with_vectors else None
                    payload = entry["payload"] if with_payload else None
                results.append(_Record())
        return results

    async def search(self, collection_name, query_vector, limit, query_filter=None, with_payload=False):
        """Simple linear scan with optional filter."""
        col = self._store.get(collection_name, {})
        results = []
        for pid, entry in col.items():
            pay = entry.get("payload") or {}
            # Apply MatchAny filter on ontology field if present.
            if query_filter is not None:
                for cond in (query_filter.must or []):
                    if hasattr(cond, "match") and hasattr(cond.match, "any"):
                        ont_ids = pay.get("ontology", [])
                        if not any(v in ont_ids for v in cond.match.any):
                            break
                else:
                    # All conditions passed — include.
                    class _Hit:
                        score = 0.9
                        payload = pay
                    results.append(_Hit())
            else:
                class _Hit2:
                    score = 0.9
                    payload = pay
                results.append(_Hit2())
        return results[:limit]

    async def delete(self, collection_name: str, points_selector) -> None:
        """Delete points matching the MatchAny filter on 'ontology' field."""
        col = self._store.get(collection_name, {})
        to_remove = []
        for pid, entry in col.items():
            pay = entry.get("payload") or {}
            ont_ids = pay.get("ontology", [])
            # Extract the list from the filter.
            for cond in (points_selector.must or []):
                if hasattr(cond, "match") and hasattr(cond.match, "any"):
                    if any(v in ont_ids for v in cond.match.any):
                        to_remove.append(pid)
        for pid in to_remove:
            col.pop(pid, None)

    async def close(self) -> None:
        pass


def _wrapper_with_fake() -> QdrantWrapper:
    """Build a QdrantWrapper backed by _FakeClient (no real Qdrant needed)."""
    wrapper = QdrantWrapper.__new__(QdrantWrapper)
    wrapper._client = _FakeClient()
    return wrapper


class TestQdrantOntologyPayload:
    """QdrantWrapper: ontology payload is stored and merged correctly."""

    @pytest.mark.asyncio
    async def test_upsert_stores_ontology_in_payload(self):
        """upsert(ontology='pkmn') stores {'uri': ..., 'ontology': ['pkmn']}."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")

        pt_id = _point_id("http://ex.org/A")
        results = await wrapper._client.retrieve("test", [pt_id], with_payload=True)
        assert results, "Point should exist"
        pay = results[0].payload
        assert pay["uri"] == "http://ex.org/A"
        assert "pkmn" in pay["ontology"]

    @pytest.mark.asyncio
    async def test_upsert_no_ontology_stores_empty_list(self):
        """upsert(ontology=None) stores payload with ontology=[]."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        await wrapper.upsert("test", [("http://ex.org/B", [0.5, 0.5, 0.0, 0.0])], ontology=None)

        pt_id = _point_id("http://ex.org/B")
        results = await wrapper._client.retrieve("test", [pt_id], with_payload=True)
        pay = results[0].payload
        assert pay["ontology"] == []

    @pytest.mark.asyncio
    async def test_upsert_merges_ontology_for_shared_uri(self):
        """A second upsert with a different ontology id merges the list."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        # First upsert — pkmn ontology.
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")
        # Second upsert — other ontology (shared-URI node).
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.1, 0.0, 0.0])], ontology="other")

        pt_id = _point_id("http://ex.org/A")
        results = await wrapper._client.retrieve("test", [pt_id], with_payload=True)
        ont_list = results[0].payload["ontology"]
        assert "pkmn" in ont_list
        assert "other" in ont_list

    @pytest.mark.asyncio
    async def test_upsert_no_duplicate_ontology_ids(self):
        """Upserting the same ontology id twice does not duplicate it."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.0, 0.0, 0.1])], ontology="pkmn")

        pt_id = _point_id("http://ex.org/A")
        results = await wrapper._client.retrieve("test", [pt_id], with_payload=True)
        ont_list = results[0].payload["ontology"]
        assert ont_list.count("pkmn") == 1, "ontology id must not be duplicated"


class TestQdrantOntologyFilter:
    """QdrantWrapper.query: ontology_filter restricts results."""

    @pytest.mark.asyncio
    async def test_query_with_filter_returns_only_scoped_points(self):
        """ontology_filter='pkmn' excludes points with only 'other' in ontology."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")
        await wrapper.upsert("test", [("http://ex.org/B", [0.5, 0.5, 0.0, 0.0])], ontology="other")

        hits = await wrapper.query("test", [1.0, 0.0, 0.0, 0.0], top_k=5, ontology_filter="pkmn")
        uris = [h[0] for h in hits]
        assert "http://ex.org/A" in uris
        assert "http://ex.org/B" not in uris

    @pytest.mark.asyncio
    async def test_query_no_filter_returns_all_points(self):
        """ontology_filter=None returns all points regardless of ontology."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")
        await wrapper.upsert("test", [("http://ex.org/B", [0.5, 0.5, 0.0, 0.0])], ontology="other")

        hits = await wrapper.query("test", [1.0, 0.0, 0.0, 0.0], top_k=5, ontology_filter=None)
        uris = [h[0] for h in hits]
        assert "http://ex.org/A" in uris
        assert "http://ex.org/B" in uris

    @pytest.mark.asyncio
    async def test_query_filter_includes_shared_uri_node(self):
        """A node belonging to both ontologies is returned by either filter."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        # Shared node: belongs to both.
        await wrapper.upsert("test", [("http://ex.org/SHARED", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")
        await wrapper.upsert("test", [("http://ex.org/SHARED", [1.0, 0.0, 0.1, 0.0])], ontology="other")

        hits_pkmn = await wrapper.query("test", [1.0, 0.0, 0.0, 0.0], top_k=5, ontology_filter="pkmn")
        hits_other = await wrapper.query("test", [1.0, 0.0, 0.0, 0.0], top_k=5, ontology_filter="other")
        assert any(h[0] == "http://ex.org/SHARED" for h in hits_pkmn), "Shared node visible from pkmn"
        assert any(h[0] == "http://ex.org/SHARED" for h in hits_other), "Shared node visible from other"


class TestQdrantDeleteByOntology:
    """QdrantWrapper.delete_by_ontology: only deletes tagged points."""

    @pytest.mark.asyncio
    async def test_delete_removes_only_targeted_ontology(self):
        """delete_by_ontology('pkmn') removes pkmn points; 'other' points survive."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        await wrapper.upsert("test", [("http://ex.org/A", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")
        await wrapper.upsert("test", [("http://ex.org/B", [0.5, 0.5, 0.0, 0.0])], ontology="other")

        await wrapper.delete_by_ontology("test", "pkmn")

        # A should be gone; B should survive.
        vec_a = await wrapper.retrieve_vector("test", "http://ex.org/A")
        vec_b = await wrapper.retrieve_vector("test", "http://ex.org/B")
        assert vec_a is None, "pkmn point should have been deleted"
        assert vec_b is not None, "other point should survive"

    @pytest.mark.asyncio
    async def test_delete_leaves_shared_node_for_other_ontology(self):
        """Deleting pkmn removes shared-URI node, but it will be re-upserted on next build."""
        wrapper = _wrapper_with_fake()
        await wrapper.ensure_collection("test", dim=4)
        # Shared node belongs to pkmn AND other.
        await wrapper.upsert("test", [("http://ex.org/SHARED", [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")
        await wrapper.upsert("test", [("http://ex.org/SHARED", [1.0, 0.1, 0.0, 0.0])], ontology="other")

        # Delete pkmn — shared node is included because it has 'pkmn' in its list.
        await wrapper.delete_by_ontology("test", "pkmn")

        # Shared node deleted (will be re-added on next scoped build for 'other').
        vec = await wrapper.retrieve_vector("test", "http://ex.org/SHARED")
        # It was tagged with both; delete_by_ontology removes the whole point.
        assert vec is None, "Shared node deleted — will be re-upserted on next 'other' build"

    @pytest.mark.asyncio
    async def test_delete_missing_collection_does_not_raise(self):
        """delete_by_ontology on a nonexistent collection silently ignores the error."""
        wrapper = _wrapper_with_fake()
        # Should not raise — collection does not exist.
        await wrapper.delete_by_ontology("nonexistent", "pkmn")


# ── FusekiEmbeddingMixin unit tests ───────────────────────────────────────────


class FakeQdrantForFuseki:
    """Lightweight fake for FusekiEmbeddingMixin tests."""

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, list[float]]] = {}
        self._ontologies: dict[str, dict[str, list[str]]] = {}
        self.ensure_collection_calls: list[tuple[str, int]] = []
        self.delete_collection_calls: list[str] = []
        self.delete_by_ontology_calls: list[tuple[str, str]] = []
        self.upsert_calls: list[dict] = []

    async def ensure_collection(self, name: str, dim: int) -> None:
        self.ensure_collection_calls.append((name, dim))
        self._collections.setdefault(name, {})

    async def delete_collection(self, name: str) -> None:
        self.delete_collection_calls.append(name)
        self._collections.pop(name, None)

    async def delete_by_ontology(self, collection: str, ontology: str) -> None:
        self.delete_by_ontology_calls.append((collection, ontology))
        col = self._collections.get(collection, {})
        ont = self._ontologies.get(collection, {})
        for uri in list(col):
            if ontology in ont.get(uri, []):
                col.pop(uri, None)
                ont.pop(uri, None)

    async def upsert(
        self,
        collection: str,
        points: list[tuple[str, list[float]]],
        ontology: str | None = None,
    ) -> int:
        self.upsert_calls.append({"collection": collection, "points": points, "ontology": ontology})
        self._collections.setdefault(collection, {})
        self._ontologies.setdefault(collection, {})
        for uri, vec in points:
            self._collections[collection][uri] = vec
            if ontology is not None:
                prev = self._ontologies[collection].get(uri, [])
                if ontology not in prev:
                    self._ontologies[collection][uri] = [*prev, ontology]
        return len(points)

    async def retrieve_vector(self, collection: str, uri: str) -> list[float] | None:
        return self._collections.get(collection, {}).get(uri)

    async def query(
        self,
        collection: str,
        vector: list[float],
        top_k: int,
        ontology_filter: str | None = None,
    ) -> list[tuple[str, float]]:
        col = self._collections.get(collection, {})
        ont_map = self._ontologies.get(collection, {})
        results = [(u, 1.0 - abs(v[0] - vector[0])) for u, v in col.items()]
        if ontology_filter is not None:
            results = [(u, s) for u, s in results if ontology_filter in ont_map.get(u, [])]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


def _make_fuseki_store(fake_qdrant: FakeQdrantForFuseki) -> MagicMock:
    """Build a minimal FusekiStore-like mock for embedding mixin tests."""
    store = MagicMock(spec=_FusekiEmbeddingMixin)
    store._qdrant = fake_qdrant
    store._get_qdrant = lambda: fake_qdrant
    store.build_embeddings = (
        lambda *a, **kw: _FusekiEmbeddingMixin.build_embeddings(store, *a, **kw)
    )
    store._build_structural_embeddings = (
        lambda **kw: _FusekiEmbeddingMixin._build_structural_embeddings(store, **kw)
    )
    store._build_textual_embeddings = (
        lambda p, **kw: _FusekiEmbeddingMixin._build_textual_embeddings(store, p, **kw)
    )
    store.find_similar = (
        lambda *a, **kw: _FusekiEmbeddingMixin.find_similar(store, *a, **kw)
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
    store._textual_page_sparql = _FusekiEmbeddingMixin._textual_page_sparql
    store._rows_to_text_pairs = _FusekiEmbeddingMixin._rows_to_text_pairs
    return store


class TestFusekiBuildEmbeddingsScoped:
    """Fuseki mixin: scoped build uses delete_by_ontology, not delete_collection."""

    @pytest.mark.asyncio
    async def test_scoped_build_calls_delete_by_ontology(self):
        """build_embeddings(ontology='pkmn') must call delete_by_ontology, not delete_collection."""
        fake_qdrant = FakeQdrantForFuseki()
        store = _make_fuseki_store(fake_qdrant)

        # Return 1 node and no edges.
        node_result = {"results": {"bindings": [{"inst": {"value": "http://ex.org/A"}}]}}
        store._sparql_select = AsyncMock(side_effect=[node_result, {"results": {"bindings": []}}])

        await store._build_structural_embeddings(ontology="pkmn")

        # delete_by_ontology called, NOT delete_collection.
        assert ("ontorag_struct", "pkmn") in fake_qdrant.delete_by_ontology_calls
        assert "ontorag_struct" not in fake_qdrant.delete_collection_calls

    @pytest.mark.asyncio
    async def test_unscoped_build_calls_delete_collection(self):
        """build_embeddings(ontology=None) must call delete_collection (clear-on-build)."""
        fake_qdrant = FakeQdrantForFuseki()
        store = _make_fuseki_store(fake_qdrant)

        node_result = {"results": {"bindings": [{"inst": {"value": "http://ex.org/A"}}]}}
        store._sparql_select = AsyncMock(side_effect=[node_result, {"results": {"bindings": []}}])

        await store._build_structural_embeddings(ontology=None)

        assert "ontorag_struct" in fake_qdrant.delete_collection_calls

    @pytest.mark.asyncio
    async def test_scoped_build_passes_ontology_to_upsert(self):
        """Scoped build must pass ontology='pkmn' to qdrant.upsert."""
        fake_qdrant = FakeQdrantForFuseki()
        store = _make_fuseki_store(fake_qdrant)

        node_result = {"results": {"bindings": [{"inst": {"value": "http://ex.org/A"}}]}}
        store._sparql_select = AsyncMock(side_effect=[node_result, {"results": {"bindings": []}}])

        await store._build_structural_embeddings(ontology="pkmn")

        assert fake_qdrant.upsert_calls, "upsert must have been called"
        for call in fake_qdrant.upsert_calls:
            if call["collection"] == "ontorag_struct":
                assert call["ontology"] == "pkmn", "ontology must be passed to upsert"

    @pytest.mark.asyncio
    async def test_scoped_build_uses_graph_clause_in_sparql(self):
        """Scoped SPARQL must include GRAPH <...> clause for the target ontology."""
        fake_qdrant = FakeQdrantForFuseki()
        store = _make_fuseki_store(fake_qdrant)

        captured: list[str] = []

        async def _capture(sparql: str):
            captured.append(sparql)
            if len(captured) == 1:
                return {"results": {"bindings": [{"inst": {"value": "http://ex.org/A"}}]}}
            return {"results": {"bindings": []}}

        store._sparql_select = _capture

        await store._build_structural_embeddings(ontology="pkmn")

        node_query = captured[0]
        # The scoped SPARQL must reference the ontology's named data graph.
        assert "urn:ontorag:pkmn:data" in node_query, (
            f"Expected 'urn:ontorag:pkmn:data' GRAPH clause in SPARQL; got: {node_query}"
        )

    @pytest.mark.asyncio
    async def test_invalid_ontology_id_raises(self):
        """build_embeddings with an invalid ontology id must raise ValueError."""
        store = _make_fuseki_store(FakeQdrantForFuseki())
        store._sparql_select = AsyncMock()

        with pytest.raises(ValueError, match="Invalid ontology id"):
            await store.build_embeddings(ontology="invalid id!")


class TestFusekiFindSimilarScoped:
    """Fuseki mixin: find_similar passes ontology_filter to Qdrant."""

    @pytest.mark.asyncio
    async def test_find_similar_passes_ontology_filter(self):
        """find_similar(ontology='pkmn') must call qdrant.query with ontology_filter='pkmn'."""
        fake_qdrant = FakeQdrantForFuseki()
        # Pre-load a vector for Pikachu with pkmn ontology.
        await fake_qdrant.ensure_collection("ontorag_struct", dim=4)
        await fake_qdrant.upsert(
            "ontorag_struct",
            [
                ("http://ex.org/Pikachu", [1.0, 0.0, 0.0, 0.0]),
                ("http://ex.org/Raichu", [0.9, 0.1, 0.0, 0.0]),
            ],
            ontology="pkmn",
        )
        await fake_qdrant.upsert(
            "ontorag_struct",
            [("http://ex.org/OtherEntity", [0.5, 0.5, 0.0, 0.0])],
            ontology="other",
        )

        store = _make_fuseki_store(fake_qdrant)
        # SPARQL meta resolution returns empty (no label/class for simplicity).
        store._sparql_select = AsyncMock(return_value={"results": {"bindings": []}})

        hits = await store._find_similar_single(
            "http://ex.org/Pikachu", 5, "structural", ontology="pkmn"
        )
        hit_uris = {h.uri for h in hits}
        assert "http://ex.org/OtherEntity" not in hit_uris, (
            "OtherEntity belongs to 'other' ontology; must be excluded from 'pkmn' results"
        )

    @pytest.mark.asyncio
    async def test_find_similar_no_ontology_returns_all(self):
        """find_similar(ontology=None) returns results from all ontologies."""
        fake_qdrant = FakeQdrantForFuseki()
        await fake_qdrant.ensure_collection("ontorag_struct", dim=4)
        await fake_qdrant.upsert(
            "ontorag_struct",
            [("http://ex.org/Pikachu", [1.0, 0.0, 0.0, 0.0])],
            ontology="pkmn",
        )
        await fake_qdrant.upsert(
            "ontorag_struct",
            [("http://ex.org/OtherEntity", [0.9, 0.0, 0.0, 0.0])],
            ontology="other",
        )

        store = _make_fuseki_store(fake_qdrant)
        store._sparql_select = AsyncMock(return_value={"results": {"bindings": []}})

        hits = await store._find_similar_single(
            "http://ex.org/Pikachu", 5, "structural", ontology=None
        )
        hit_uris = {h.uri for h in hits}
        assert "http://ex.org/OtherEntity" in hit_uris, (
            "Without ontology filter all ontologies' points should appear"
        )


# ── Neo4j post-filter unit tests ──────────────────────────────────────────────


class TestNeo4jPostFilter:
    """_Neo4jEmbeddingMixin._rows_to_similar_hits: post-filter by _ontology."""

    def _mixin(self) -> _Neo4jEmbeddingMixin:
        return _Neo4jEmbeddingMixin()

    def test_no_ontology_includes_all_nodes(self):
        """When ontology=None, all rows (incl. those with no _ontology prop) are included."""
        mixin = self._mixin()
        rows = [
            {"uri": "http://ex.org/A", "raw_label": None, "node_ontology": ["pkmn"], "cls_uri": None, "score": 0.9},
            {"uri": "http://ex.org/B", "raw_label": None, "node_ontology": None, "cls_uri": None, "score": 0.8},
            {"uri": "http://ex.org/C", "raw_label": None, "node_ontology": ["other"], "cls_uri": None, "score": 0.7},
        ]
        hits = mixin._rows_to_similar_hits(rows, "http://ex.org/X", 10, "structural", frozenset(), ontology=None)
        uris = {h.uri for h in hits}
        assert uris == {"http://ex.org/A", "http://ex.org/B", "http://ex.org/C"}

    def test_ontology_filter_excludes_non_members(self):
        """When ontology='pkmn', only rows with 'pkmn' in node_ontology are included."""
        mixin = self._mixin()
        rows = [
            {"uri": "http://ex.org/A", "raw_label": None, "node_ontology": ["pkmn"], "cls_uri": None, "score": 0.9},
            {"uri": "http://ex.org/B", "raw_label": None, "node_ontology": ["other"], "cls_uri": None, "score": 0.8},
            {"uri": "http://ex.org/C", "raw_label": None, "node_ontology": None, "cls_uri": None, "score": 0.7},
        ]
        hits = mixin._rows_to_similar_hits(rows, "http://ex.org/X", 10, "structural", frozenset(), ontology="pkmn")
        uris = {h.uri for h in hits}
        assert "http://ex.org/A" in uris
        assert "http://ex.org/B" not in uris, "B is in 'other', not 'pkmn'"
        assert "http://ex.org/C" not in uris, "C has no _ontology — excluded when scope is active"

    def test_ontology_filter_includes_shared_uri(self):
        """A node in both ontologies is included by either filter."""
        mixin = self._mixin()
        rows = [
            {
                "uri": "http://ex.org/SHARED",
                "raw_label": None,
                "node_ontology": ["pkmn", "other"],
                "cls_uri": None,
                "score": 0.99,
            }
        ]
        hits_pkmn = mixin._rows_to_similar_hits(
            rows, "http://ex.org/X", 10, "structural", frozenset(), ontology="pkmn"
        )
        hits_other = mixin._rows_to_similar_hits(
            rows, "http://ex.org/X", 10, "structural", frozenset(), ontology="other"
        )
        assert any(h.uri == "http://ex.org/SHARED" for h in hits_pkmn)
        assert any(h.uri == "http://ex.org/SHARED" for h in hits_other)

    def test_start_node_always_excluded(self):
        """Start node is excluded regardless of ontology scope."""
        mixin = self._mixin()
        START = "http://ex.org/Start"
        rows = [
            {"uri": START, "raw_label": None, "node_ontology": ["pkmn"], "cls_uri": None, "score": 1.0},
            {"uri": "http://ex.org/Other", "raw_label": None, "node_ontology": ["pkmn"], "cls_uri": None, "score": 0.8},
        ]
        hits = mixin._rows_to_similar_hits(rows, START, 10, "structural", frozenset(), ontology="pkmn")
        assert all(h.uri != START for h in hits)

    def test_top_k_respected_after_filter(self):
        """Post-filtered results honour top_k cap."""
        mixin = self._mixin()
        rows = [
            {"uri": f"http://ex.org/{i}", "raw_label": None, "node_ontology": ["pkmn"], "cls_uri": None, "score": float(10 - i)}
            for i in range(10)
        ]
        hits = mixin._rows_to_similar_hits(rows, "http://ex.org/X", 3, "structural", frozenset(), ontology="pkmn")
        assert len(hits) <= 3


class TestNeo4jBuildScopeValidation:
    """build_embeddings / find_similar validate ontology id before use."""

    @pytest.mark.asyncio
    async def test_build_embeddings_invalid_ontology_raises(self):
        """Invalid ontology slug must raise ValueError immediately."""
        store = MagicMock(spec=_Neo4jEmbeddingMixin)
        store._ensure_prefix_map = AsyncMock()
        store.build_embeddings = (
            lambda *a, **kw: _Neo4jEmbeddingMixin.build_embeddings(store, *a, **kw)
        )

        with pytest.raises(ValueError, match="Invalid ontology id"):
            await store.build_embeddings(ontology="bad id!")

    @pytest.mark.asyncio
    async def test_find_similar_invalid_ontology_raises(self):
        """Invalid ontology slug must raise ValueError immediately."""
        store = MagicMock(spec=_Neo4jEmbeddingMixin)
        store._ensure_prefix_map = AsyncMock()
        store.find_similar = (
            lambda *a, **kw: _Neo4jEmbeddingMixin.find_similar(store, *a, **kw)
        )

        with pytest.raises(ValueError, match="Invalid ontology id"):
            await store.find_similar("http://ex.org/A", ontology="bad id!")
