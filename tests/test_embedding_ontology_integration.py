"""Live integration tests for per-ontology embedding scoping.

Tests that (a) scoped find_similar returns only that ontology's entities,
(b) a scoped build does NOT wipe another ontology's embeddings, and
(c) ontology=None spans both ontologies.

Requires ALL three services:
  - Fuseki at http://localhost:3030 (admin/admin, dataset "ontorag")
  - Neo4j at bolt://localhost:7687 (neo4j/ontorag123, GDS installed)
  - Qdrant at http://localhost:6333

Skip behaviour (tests are SKIPPED, never errored, when containers are down):
  - Each backend's test class carries its own ``@requires_*`` skipif, so a
    partially-available environment runs the tests it can (e.g. only Neo4j up
    → Neo4j class runs, Fuseki+Qdrant class skips).
  - A module-level ``pytestmark`` skipif additionally skips EVERY test (incl.
    any future top-level test) when NO backend this module needs is reachable.

Two ontologies are loaded:
  - "pkmn": Pokémon schema + data (src/ontorag/_templates/examples/pokemon/)
  - "other": A tiny synthetic 2-node ontology (defined inline below)

Test plan:
  1. Load pkmn and other into each backend under their respective ontology ids.
  2. build_embeddings(ontology="pkmn") → structural + textual.
  3. find_similar(pikachu, ontology="pkmn") → only pkmn entities, not "other".
  4. build_embeddings(ontology="other") → must NOT remove pkmn's embeddings.
  5. find_similar(pikachu, ontology="pkmn") → still works (no wipe regression).
  6. find_similar(pikachu, ontology=None) → spans both ontologies.

Textual embeddings use a deterministic FAKE EmbeddingProvider — no real API.
"""

from __future__ import annotations

import socket
import tempfile
import textwrap

import pytest

from ontorag.stores._qdrant import STRUCT_COLLECTION, TEXT_COLLECTION, QdrantWrapper

# ── Constants ─────────────────────────────────────────────────────────────────

FUSEKI_URL = "http://localhost:3030"
FUSEKI_USER = "admin"
FUSEKI_PASSWORD = "admin"
FUSEKI_DATASET = "ontorag"

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ontorag123"

QDRANT_URL = "http://localhost:6333"

SCHEMA_TTL = "src/ontorag/_templates/examples/pokemon/schema.ttl"
DATA_TTL = "src/ontorag/_templates/examples/pokemon/data.ttl"

_PIKACHU_URI = "http://example.org/pokemon/data#Pikachu"

# Fake embedding dimension — small to keep tests fast.
_FAKE_DIM: int = 8

# A minimal "other" ontology TTL with 2 entities.
_OTHER_TTL = textwrap.dedent("""\
    @prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix owl:  <http://www.w3.org/2002/07/owl#> .
    @prefix ex:   <http://example.org/other#> .

    ex:Thing a owl:Class ;
        rdfs:label "Thing" .

    ex:Alpha a ex:Thing ;
        rdfs:label "Alpha Entity" .

    ex:Beta a ex:Thing ;
        rdfs:label "Beta Entity" .
""")


# ── Fake embedding provider ───────────────────────────────────────────────────


class FakeEmbeddingProvider:
    """Deterministic hash-based embedding provider (no external calls)."""

    model: str = "fake-ontology-scope-v1"
    dimension: int = _FAKE_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a stable L2-normalised vector derived from the text hash."""
        result = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFF
            vec = [float((h >> (i * 4)) & 0xF) / 15.0 for i in range(self.dimension)]
            norm = sum(x * x for x in vec) ** 0.5
            if norm > 0:
                vec = [x / norm for x in vec]
            result.append(vec)
        return result


# ── Availability checks ───────────────────────────────────────────────────────


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _is_fuseki_up() -> bool:
    try:
        import httpx
        r = httpx.get(f"{FUSEKI_URL}/$/ping", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _is_qdrant_up() -> bool:
    try:
        import httpx
        r = httpx.get(f"{QDRANT_URL}/healthz", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _is_neo4j_up() -> bool:
    return _tcp_reachable("localhost", 7687)


_FUSEKI_UP = _is_fuseki_up()
_QDRANT_UP = _is_qdrant_up()
_NEO4J_UP = _is_neo4j_up()

# This module exercises TWO independent backends:
#   - Fuseki + Qdrant  (TestFusekiOntologyScopeIntegration)
#   - Neo4j + GDS       (TestNeo4jOntologyScopeIntegration)
# Each backend's test class carries its own ``@requires_*`` skipif so a
# partially-available environment (e.g. only Neo4j up) still runs the tests it
# can.  The module-level skipif below guards EVERY test (incl. any future
# top-level test) when NOTHING this module needs is reachable — so a fully-down
# environment SKIPS the whole module rather than erroring.
_ANY_BACKEND_UP = (_FUSEKI_UP and _QDRANT_UP) or _NEO4J_UP

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _ANY_BACKEND_UP,
        reason=(
            "No embedding backend reachable: needs Fuseki+Qdrant "
            "(http://localhost:3030 + http://localhost:6333) or "
            "Neo4j (bolt://localhost:7687)"
        ),
    ),
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_ttl_tempfile(content: str) -> str:
    """Write a TTL string to a named temp file and return the file path."""
    f = tempfile.NamedTemporaryFile(
        suffix=".ttl", delete=False, mode="w", encoding="utf-8"
    )
    f.write(content)
    f.flush()
    f.close()
    return f.name


async def _clean_qdrant_collections() -> None:
    """Drop both Qdrant embedding collections so each test starts clean."""
    qdrant = QdrantWrapper(url=QDRANT_URL)
    await qdrant.delete_collection(STRUCT_COLLECTION)
    await qdrant.delete_collection(TEXT_COLLECTION)
    await qdrant.aclose()


# ── Fuseki + Qdrant integration ───────────────────────────────────────────────


requires_fuseki_qdrant = pytest.mark.skipif(
    not (_FUSEKI_UP and _QDRANT_UP),
    reason="Requires live Fuseki (http://localhost:3030) and Qdrant (http://localhost:6333)",
)


@requires_fuseki_qdrant
class TestFusekiOntologyScopeIntegration:
    """Live Fuseki + Qdrant: per-ontology embedding scope isolation."""

    async def _setup_stores(self):
        """Return a fresh FusekiStore with pkmn + other ontologies loaded."""
        from ontorag.stores.fuseki import FusekiStore

        store = FusekiStore(
            url=FUSEKI_URL,
            dataset=FUSEKI_DATASET,
            user=FUSEKI_USER,
            password=FUSEKI_PASSWORD,
        )
        # Clear all existing data.
        await store.clear_graph("all")
        # Load pokemon under ontology id "pkmn".
        await store.load_rdf(SCHEMA_TTL, mode="schema", ontology="pkmn")
        await store.load_rdf(DATA_TTL, mode="data", ontology="pkmn")
        # Load tiny "other" ontology.
        other_path = _write_ttl_tempfile(_OTHER_TTL)
        await store.load_rdf(other_path, mode="data", ontology="other")
        return store

    @pytest.mark.asyncio
    async def test_scoped_find_similar_returns_only_pkmn(self):
        """find_similar(ontology='pkmn') must not return 'other' ontology entities."""
        await _clean_qdrant_collections()
        store = await self._setup_stores()
        try:
            await store.build_embeddings(
                "structural", ontology="pkmn"
            )
            await store.build_embeddings(
                "structural", ontology="other"
            )

            hits = await store.find_similar(_PIKACHU_URI, top_k=20, mode="structural", ontology="pkmn")
        finally:
            await store.aclose()

        # No "other" entities in results.
        other_uris = {h.uri for h in hits if "other#" in h.uri}
        assert not other_uris, (
            f"find_similar(ontology='pkmn') must not return 'other' entities; got: {other_uris}"
        )
        assert len(hits) > 0, "Expected at least one pkmn result."

    @pytest.mark.asyncio
    async def test_scoped_build_does_not_wipe_other_ontology(self):
        """build_embeddings(ontology='other') must not remove pkmn embeddings."""
        await _clean_qdrant_collections()
        store = await self._setup_stores()
        try:
            # Build pkmn embeddings first.
            pkmn_result = await store.build_embeddings(
                "structural", ontology="pkmn"
            )
            assert pkmn_result.get("structural", 0) > 0

            # Build other embeddings — must NOT wipe pkmn.
            await store.build_embeddings("structural", ontology="other")

            # pkmn find_similar must still work.
            hits = await store.find_similar(
                _PIKACHU_URI, top_k=5, mode="structural", ontology="pkmn"
            )
        finally:
            await store.aclose()

        assert len(hits) > 0, (
            "pkmn embeddings must survive after building 'other' ontology embeddings"
        )

    @pytest.mark.asyncio
    async def test_union_find_similar_spans_both_ontologies(self):
        """find_similar(ontology=None) must return hits from both ontologies.

        We query from Alpha (an 'other' entity) and verify pkmn entities appear.
        Querying from Pikachu is unreliable here because the 2-node 'other'
        ontology has a very different graph topology and its nodes may rank low
        in Pikachu's top-N.  Querying from an 'other' entity and confirming pkmn
        entities appear proves that union mode spans all ontologies.
        """
        _ALPHA_URI = "http://example.org/other#Alpha"
        await _clean_qdrant_collections()
        store = await self._setup_stores()
        try:
            await store.build_embeddings("structural", ontology="pkmn")
            await store.build_embeddings("structural", ontology="other")

            # Query from Alpha without filter — expect pkmn entities in results.
            hits = await store.find_similar(_ALPHA_URI, top_k=20, mode="structural", ontology=None)
        finally:
            await store.aclose()

        # Any pokemon entity appearing in union results confirms the query spans pkmn.
        pkmn_uris = {h.uri for h in hits if "example.org/pokemon" in h.uri}
        assert pkmn_uris, (
            f"Union find_similar from 'other' entity must include pkmn entities; "
            f"all hits: {[h.uri for h in hits]}"
        )

    @pytest.mark.asyncio
    async def test_textual_scoped_build_and_find(self):
        """Textual mode: scoped build then scoped find does not leak other entities."""
        await _clean_qdrant_collections()
        store = await self._setup_stores()
        provider = FakeEmbeddingProvider()
        try:
            await store.build_embeddings("textual", provider, ontology="pkmn")
            await store.build_embeddings("textual", provider, ontology="other")

            hits = await store.find_similar(_PIKACHU_URI, top_k=20, mode="textual", ontology="pkmn")
        finally:
            await store.aclose()

        other_uris = {h.uri for h in hits if "other#" in h.uri}
        assert not other_uris, (
            f"Textual find_similar(ontology='pkmn') must not return 'other' entities; got: {other_uris}"
        )

    @pytest.mark.asyncio
    async def test_textual_scoped_build_no_wipe(self):
        """Textual mode: building 'other' must not wipe pkmn textual embeddings."""
        await _clean_qdrant_collections()
        store = await self._setup_stores()
        provider = FakeEmbeddingProvider()
        try:
            pkmn_result = await store.build_embeddings("textual", provider, ontology="pkmn")
            assert pkmn_result.get("textual", 0) > 0

            await store.build_embeddings("textual", provider, ontology="other")

            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="textual", ontology="pkmn")
        finally:
            await store.aclose()

        assert len(hits) > 0, (
            "pkmn textual embeddings must survive after building 'other' ontology textual embeddings"
        )

    @pytest.mark.asyncio
    async def test_live_rebuild_keeps_shared_uri_other_tag(self):
        """HIGH regression on REAL Qdrant: rebuilding pkmn keeps a shared URI's 'other' tag.

        Exercises the un-tag delete path end-to-end against a live Qdrant
        server (not the fake client).  A single URI is tagged into both
        ontologies, then the pkmn ontology is rebuilt (delete_by_ontology +
        re-upsert).  The shared point must retain its 'other' tag throughout and
        stay queryable under 'other'.
        """
        from ontorag.stores._qdrant import _point_id

        COLLECTION = STRUCT_COLLECTION
        SHARED = "http://example.org/shared#Node"
        qdrant = QdrantWrapper(url=QDRANT_URL)
        try:
            await qdrant.delete_collection(COLLECTION)
            await qdrant.ensure_collection(COLLECTION, dim=4)

            # Step 1: build pkmn — SHARED tagged ["pkmn"].
            await qdrant.delete_by_ontology(COLLECTION, "pkmn")
            await qdrant.upsert(COLLECTION, [(SHARED, [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")

            # Step 2: build other — SHARED merges to ["pkmn", "other"].
            await qdrant.delete_by_ontology(COLLECTION, "other")
            await qdrant.upsert(COLLECTION, [(SHARED, [1.0, 0.1, 0.0, 0.0])], ontology="other")

            # Step 3: rebuild pkmn — delete (un-tag) then re-upsert.
            await qdrant.delete_by_ontology(COLLECTION, "pkmn")

            # Mid-rebuild: the point must survive and 'other' must remain.
            mid = await qdrant._client.retrieve(
                collection_name=COLLECTION,
                ids=[_point_id(SHARED)],
                with_payload=True,
            )
            assert mid, "Shared point must NOT be deleted during pkmn rebuild (live)"
            assert mid[0].payload["ontology"] == ["other"], (
                "After un-tagging pkmn, only 'other' should remain (live)"
            )

            await qdrant.upsert(COLLECTION, [(SHARED, [1.0, 0.0, 0.0, 0.0])], ontology="pkmn")

            # Final: both tags present; queryable under each scope.
            final = await qdrant._client.retrieve(
                collection_name=COLLECTION,
                ids=[_point_id(SHARED)],
                with_payload=True,
            )
            assert set(final[0].payload["ontology"]) == {"pkmn", "other"}, (
                f"Shared node must retain both tags; got {final[0].payload['ontology']}"
            )
            hits_other = await qdrant.query(
                COLLECTION, [1.0, 0.0, 0.0, 0.0], top_k=5, ontology_filter="other"
            )
            assert any(h[0] == SHARED for h in hits_other), (
                "Shared node must stay queryable under 'other' after a pkmn rebuild (live)"
            )
        finally:
            await qdrant.delete_collection(COLLECTION)
            await qdrant.aclose()


# ── Neo4j + GDS integration ───────────────────────────────────────────────────


requires_neo4j = pytest.mark.skipif(
    not _NEO4J_UP,
    reason="Requires live Neo4j at bolt://localhost:7687",
)


@requires_neo4j
class TestNeo4jOntologyScopeIntegration:
    """Live Neo4j + GDS: per-ontology embedding scope isolation."""

    async def _setup_store(self):
        """Return a fresh Neo4jStore with pkmn + other ontologies loaded."""
        from ontorag.stores._neo4j_embedding_mixin import _STRUCT_INDEX, _TEXT_INDEX
        from ontorag.stores.neo4j import Neo4jStore

        store = Neo4jStore(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
        # Clear all ABox + TBox nodes (preserve _NsPrefDef / _GraphConfig).
        await store._run_write(
            """
            MATCH (n:Resource)
            WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig
            DETACH DELETE n
            """
        )
        await store._run_write(f"DROP INDEX {_STRUCT_INDEX} IF EXISTS")
        await store._run_write(f"DROP INDEX {_TEXT_INDEX} IF EXISTS")

        # Load pokemon under ontology "pkmn".
        await store.load_rdf(SCHEMA_TTL, "schema", ontology="pkmn")
        await store.load_rdf(DATA_TTL, "data", ontology="pkmn")

        # Load tiny "other" ontology.
        other_path = _write_ttl_tempfile(_OTHER_TTL)
        await store.load_rdf(other_path, "data", ontology="other")

        return store

    @pytest.mark.asyncio
    async def test_scoped_find_similar_returns_only_pkmn(self):
        """find_similar(ontology='pkmn') must not include 'other' entities."""
        store = await self._setup_store()
        try:
            await store.build_embeddings("structural", ontology="pkmn")

            hits = await store.find_similar(
                _PIKACHU_URI, top_k=20, mode="structural", ontology="pkmn"
            )
        finally:
            await store.aclose()

        other_uris = {h.uri for h in hits if "other#" in h.uri or "ex.org/other" in h.uri}
        assert not other_uris, (
            f"find_similar(ontology='pkmn') must not return 'other' entities; got: {other_uris}"
        )
        assert len(hits) > 0, "Expected at least one pkmn structural result."

    @pytest.mark.asyncio
    async def test_scoped_build_does_not_wipe_other_ontology(self):
        """Building 'other' embeddings must not remove pkmn node properties."""
        from ontorag.stores._neo4j_embedding_mixin import _STRUCT_PROP

        store = await self._setup_store()
        try:
            # Build pkmn first.
            pkmn_result = await store.build_embeddings("structural", ontology="pkmn")
            assert pkmn_result.get("structural", 0) > 0

            # Count pkmn nodes with _struct_embedding before 'other' build.
            rows_before = await store._run(
                f"MATCH (n:Resource) WHERE n.`{_STRUCT_PROP}` IS NOT NULL "
                f"AND $oid IN n._ontology RETURN count(n) AS cnt",
                oid="pkmn",
            )
            count_before = rows_before[0]["cnt"] if rows_before else 0

            # Build 'other' — should only touch 'other' nodes.
            await store.build_embeddings("structural", ontology="other")

            # Count pkmn nodes again — must be unchanged.
            rows_after = await store._run(
                f"MATCH (n:Resource) WHERE n.`{_STRUCT_PROP}` IS NOT NULL "
                f"AND $oid IN n._ontology RETURN count(n) AS cnt",
                oid="pkmn",
            )
            count_after = rows_after[0]["cnt"] if rows_after else 0
        finally:
            await store.aclose()

        assert count_after == count_before, (
            f"pkmn embedding count changed after building 'other': "
            f"{count_before} → {count_after}"
        )
        assert count_before > 0, "pkmn must have structural embeddings after build"

    @pytest.mark.asyncio
    async def test_union_find_similar_spans_both_ontologies(self):
        """find_similar(ontology=None) must include entities from both ontologies.

        We query from Alpha (an 'other' entity) and verify that pkmn entities appear
        in the result.  Querying from Pikachu is not reliable here because the
        'other' ontology only has 2 tiny nodes — they may not rank in the top-N
        when compared against 50+ structurally rich pkmn nodes.  Querying from
        an 'other' node and verifying pkmn entities appear demonstrates that union
        mode truly spans both ontologies.
        """
        _ALPHA_URI = "http://example.org/other#Alpha"
        store = await self._setup_store()
        try:
            # Build embeddings for both.
            await store.build_embeddings("structural", ontology="pkmn")
            await store.build_embeddings("structural", ontology="other")

            # Query from Alpha (an 'other' entity) without ontology filter.
            hits = await store.find_similar(
                _ALPHA_URI, top_k=20, mode="structural", ontology=None
            )
        finally:
            await store.aclose()

        # Any pokemon entity (TBox or ABox) appearing in union results confirms
        # the query spans the pkmn ontology.
        pkmn_uris = {h.uri for h in hits if "example.org/pokemon" in h.uri}
        assert pkmn_uris, (
            f"Union find_similar from 'other' entity must include pkmn entities; "
            f"all hits: {[h.uri for h in hits]}"
        )

    @pytest.mark.asyncio
    async def test_textual_scoped_find_excludes_other(self):
        """Textual scoped find_similar excludes 'other' ontology entities."""
        store = await self._setup_store()
        provider = FakeEmbeddingProvider()
        try:
            await store.build_embeddings("textual", provider, ontology="pkmn")
            await store.build_embeddings("textual", provider, ontology="other")

            hits = await store.find_similar(
                _PIKACHU_URI, top_k=20, mode="textual", ontology="pkmn"
            )
        finally:
            await store.aclose()

        other_uris = {h.uri for h in hits if "other#" in h.uri or "ex.org/other" in h.uri}
        assert not other_uris, (
            f"Textual find_similar(ontology='pkmn') must not return 'other' entities; got: {other_uris}"
        )

    @pytest.mark.asyncio
    async def test_textual_scoped_no_wipe(self):
        """Textual build for 'other' must not wipe pkmn textual embeddings."""
        from ontorag.stores._neo4j_embedding_mixin import _TEXT_PROP

        store = await self._setup_store()
        provider = FakeEmbeddingProvider()
        try:
            pkmn_result = await store.build_embeddings("textual", provider, ontology="pkmn")
            assert pkmn_result.get("textual", 0) > 0

            rows_before = await store._run(
                f"MATCH (n:Resource) WHERE n.`{_TEXT_PROP}` IS NOT NULL "
                f"AND $oid IN n._ontology RETURN count(n) AS cnt",
                oid="pkmn",
            )
            count_before = rows_before[0]["cnt"] if rows_before else 0

            await store.build_embeddings("textual", provider, ontology="other")

            rows_after = await store._run(
                f"MATCH (n:Resource) WHERE n.`{_TEXT_PROP}` IS NOT NULL "
                f"AND $oid IN n._ontology RETURN count(n) AS cnt",
                oid="pkmn",
            )
            count_after = rows_after[0]["cnt"] if rows_after else 0
        finally:
            await store.aclose()

        assert count_after == count_before, (
            f"pkmn textual embedding count changed after 'other' build: "
            f"{count_before} → {count_after}"
        )
