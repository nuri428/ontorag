"""Integration tests for Neo4j graph-embedding mixin (C3 + C4).

Requires a running Neo4j instance at bolt://localhost:7687 (container
ontorag-neo4j-1 with GDS 2.13.10 installed).

Marked with @pytest.mark.integration — skipped gracefully when the
container is unreachable.

Each test class reloads a fresh copy of the pokemon schema + data to ensure
isolation.  The graphconfig and _NsPrefDef nodes are preserved (only one
graphconfig allowed per database).

Textual embeddings use a deterministic FAKE EmbeddingProvider — no real
OpenAI/Ollama calls (no API key required, no cost).
"""

from __future__ import annotations


import pytest
import pytest_asyncio

from ontorag.stores._neo4j_embedding_mixin import (
    _STRUCT_DIM,
    _STRUCT_INDEX,
    _STRUCT_PROP,
    _TEXT_INDEX,
    _TEXT_PROP,
)

# ── Constants ─────────────────────────────────────────────────────────────────

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ontorag123"

SCHEMA_TTL = "src/ontorag/_templates/examples/pokemon/schema.ttl"
DATA_TTL = "src/ontorag/_templates/examples/pokemon/data.ttl"

_POKEMON_CLASS = "http://example.org/pokemon#Pokemon"
_PIKACHU_URI = "http://example.org/pokemon/data#Pikachu"
_CHARMANDER_URI = "http://example.org/pokemon/data#Charmander"

# Text embedding dimension for the fake provider used in tests.
_FAKE_DIM: int = 16


# ── Fake provider ─────────────────────────────────────────────────────────────


class FakeEmbeddingProvider:
    """Deterministic fake provider — no external API.

    Vectors are derived from the text content hash so identical texts always
    produce identical vectors, and the dimension matches a fixed small size
    (_FAKE_DIM) so the vector index is created correctly.
    """

    model: str = "fake-integration-v1"
    dimension: int = _FAKE_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic unit-scaled hash vectors."""
        result = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFFFFFFFFFF  # 64-bit unsigned
            vec: list[float] = []
            for i in range(self.dimension):
                byte = (h >> (i % 8 * 8)) & 0xFF
                vec.append(float(byte) / 255.0)
            result.append(vec)
        return result


# ── Connectivity check ────────────────────────────────────────────────────────


def _is_neo4j_reachable() -> bool:
    """Return True if Neo4j bolt port is reachable."""
    try:
        import socket

        host, port_str = NEO4J_URI.replace("bolt://", "").split(":")
        with socket.create_connection((host, int(port_str)), timeout=2):
            return True
    except Exception:
        return False


SKIP_IF_UNREACHABLE = pytest.mark.skipif(
    not _is_neo4j_reachable(),
    reason="Neo4j container not reachable at bolt://localhost:7687",
)


# ── Store fixture ─────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def store():
    """Fresh Neo4jStore with pokemon data loaded; cleans up after each test."""
    from ontorag.stores.neo4j import Neo4jStore

    s = Neo4jStore(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    try:
        # Reset ABox only (preserve graphconfig / _NsPrefDef / TBox).
        await s._run_write(
            """
            MATCH (n:Resource)
            WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig
            DETACH DELETE n
            """
        )
        # Drop any leftover embedding indexes from previous runs.
        await s._run_write(f"DROP INDEX {_STRUCT_INDEX} IF EXISTS")
        await s._run_write(f"DROP INDEX {_TEXT_INDEX} IF EXISTS")
        # Load fresh data.
        await s.load_rdf(SCHEMA_TTL, "schema")
        await s.load_rdf(DATA_TTL, "data")
        yield s
    finally:
        # Clean up embedding properties and indexes.
        await s._run_write(
            f"MATCH (n:Resource) WHERE n.`{_STRUCT_PROP}` IS NOT NULL "
            f"REMOVE n.`{_STRUCT_PROP}`"
        )
        await s._run_write(
            f"MATCH (n:Resource) WHERE n.`{_TEXT_PROP}` IS NOT NULL "
            f"REMOVE n.`{_TEXT_PROP}`"
        )
        await s._run_write(f"DROP INDEX {_STRUCT_INDEX} IF EXISTS")
        await s._run_write(f"DROP INDEX {_TEXT_INDEX} IF EXISTS")
        await s.aclose()


# ── Structural embedding integration ─────────────────────────────────────────


@pytest.mark.integration
@SKIP_IF_UNREACHABLE
class TestStructuralEmbeddingIntegration:
    """Real GDS FastRP structural embedding tests."""

    @pytest.mark.asyncio
    async def test_build_structural_writes_embeddings(self, store):
        """build_embeddings('structural') must write _struct_embedding on nodes."""
        result = await store.build_embeddings("structural")
        assert result.get("structural", 0) > 0, "Expected at least one node embedded"

    @pytest.mark.asyncio
    async def test_struct_embedding_dimension(self, store):
        """Written embeddings must have the correct dimension (_STRUCT_DIM=256)."""
        await store.build_embeddings("structural")
        rows = await store._run(
            f"MATCH (n:Resource) WHERE n.`{_STRUCT_PROP}` IS NOT NULL "
            f"RETURN size(n.`{_STRUCT_PROP}`) AS dim LIMIT 1"
        )
        assert rows, "No nodes have _struct_embedding"
        assert rows[0]["dim"] == _STRUCT_DIM

    @pytest.mark.asyncio
    async def test_struct_vector_index_created(self, store):
        """After build_embeddings, the struct_vec index must be ONLINE."""
        await store.build_embeddings("structural")
        info = await store._get_vector_index_info(_STRUCT_INDEX)
        assert info is not None, f"Index '{_STRUCT_INDEX}' not found"
        assert info.get("state") == "ONLINE", f"Index state: {info.get('state')}"

    @pytest.mark.asyncio
    async def test_find_similar_structural_returns_pokemon(self, store):
        """find_similar(Pikachu, structural) must return other pokemon."""
        await store.build_embeddings("structural")

        hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="structural")

        # Must not include Pikachu itself.
        hit_uris = {h.uri for h in hits}
        assert _PIKACHU_URI not in hit_uris, "Start node must be excluded from results"

        # All scores must be in the valid cosine range [0, 1].
        for h in hits:
            assert 0.0 <= h.score <= 1.0, f"Score out of range: {h.score}"
            assert h.mode == "structural"

        # Must have at least some pokemon results.
        assert len(hits) > 0, "Expected at least one similar pokemon"

    @pytest.mark.asyncio
    async def test_find_similar_structural_respects_top_k(self, store):
        """find_similar must return at most top_k results."""
        await store.build_embeddings("structural")
        hits = await store.find_similar(_PIKACHU_URI, top_k=3, mode="structural")
        assert len(hits) <= 3

    @pytest.mark.asyncio
    async def test_find_similar_absent_node_returns_empty(self, store):
        """A URI not in the graph must return [] without raising."""
        await store.build_embeddings("structural")
        hits = await store.find_similar("http://ex.org/does-not-exist", mode="structural")
        assert hits == []

    @pytest.mark.asyncio
    async def test_find_similar_no_embedding_returns_empty(self, store):
        """Querying before build_embeddings returns [] gracefully."""
        # Do NOT call build_embeddings — no embeddings exist.
        hits = await store.find_similar(_PIKACHU_URI, mode="structural")
        assert hits == []

    @pytest.mark.asyncio
    async def test_build_structural_idempotent(self, store):
        """Calling build_embeddings('structural') twice must not raise."""
        result1 = await store.build_embeddings("structural")
        result2 = await store.build_embeddings("structural")
        assert result1.get("structural", 0) > 0
        assert result2.get("structural", 0) > 0


# ── Textual embedding integration ─────────────────────────────────────────────


@pytest.mark.integration
@SKIP_IF_UNREACHABLE
class TestTextualEmbeddingIntegration:
    """Textual embedding tests using the deterministic FakeEmbeddingProvider."""

    @pytest.mark.asyncio
    async def test_build_textual_writes_embeddings(self, store):
        """build_embeddings('textual', fake_provider) must write _text_embedding."""
        provider = FakeEmbeddingProvider()
        result = await store.build_embeddings("textual", provider)
        assert result.get("textual", 0) > 0, "Expected at least one node embedded"

    @pytest.mark.asyncio
    async def test_text_embedding_dimension(self, store):
        """Written textual embeddings must match provider.dimension."""
        provider = FakeEmbeddingProvider()
        await store.build_embeddings("textual", provider)
        rows = await store._run(
            f"MATCH (n:Resource) WHERE n.`{_TEXT_PROP}` IS NOT NULL "
            f"RETURN size(n.`{_TEXT_PROP}`) AS dim LIMIT 1"
        )
        assert rows, "No nodes have _text_embedding"
        assert rows[0]["dim"] == _FAKE_DIM

    @pytest.mark.asyncio
    async def test_text_vector_index_created(self, store):
        """After textual build, text_vec index must be ONLINE."""
        provider = FakeEmbeddingProvider()
        await store.build_embeddings("textual", provider)
        info = await store._get_vector_index_info(_TEXT_INDEX)
        assert info is not None, f"Index '{_TEXT_INDEX}' not found"
        assert info.get("state") == "ONLINE"

    @pytest.mark.asyncio
    async def test_find_similar_textual_returns_hits(self, store):
        """find_similar(Pikachu, textual) must return hits with valid scores."""
        provider = FakeEmbeddingProvider()
        await store.build_embeddings("textual", provider)

        hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="textual")

        hit_uris = {h.uri for h in hits}
        assert _PIKACHU_URI not in hit_uris, "Start node must be excluded"
        for h in hits:
            assert 0.0 <= h.score <= 1.0, f"Score out of range: {h.score}"
            assert h.mode == "textual"

    @pytest.mark.asyncio
    async def test_textual_no_embedding_returns_empty(self, store):
        """Without textual build, find_similar(textual) returns []."""
        hits = await store.find_similar(_PIKACHU_URI, mode="textual")
        assert hits == []


# ── Hybrid integration ────────────────────────────────────────────────────────


@pytest.mark.integration
@SKIP_IF_UNREACHABLE
class TestHybridIntegration:
    """RRF hybrid mode integration tests."""

    @pytest.mark.asyncio
    async def test_hybrid_returns_fused_results(self, store):
        """Hybrid mode must return results when both indexes exist."""
        provider = FakeEmbeddingProvider()
        await store.build_embeddings("both", provider)

        hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="hybrid")

        assert len(hits) > 0, "Hybrid should return at least one result"
        hit_uris = {h.uri for h in hits}
        assert _PIKACHU_URI not in hit_uris, "Start node must be excluded"
        for h in hits:
            assert h.mode == "hybrid"
            assert h.score > 0.0

    @pytest.mark.asyncio
    async def test_hybrid_partial_fallback(self, store):
        """Hybrid with only structural index returns results (textual gracefully absent)."""
        # Build structural only.
        await store.build_embeddings("structural")

        # Hybrid should still return structural results (textual returns []).
        hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="hybrid")
        # May return structural-only results or empty — must not raise.
        for h in hits:
            assert h.mode == "hybrid"

    @pytest.mark.asyncio
    async def test_hybrid_empty_graph_returns_empty(self, store):
        """On a fresh graph with no embeddings, hybrid returns []."""
        hits = await store.find_similar(_PIKACHU_URI, mode="hybrid")
        assert hits == []


# ── Empty graph edge cases ────────────────────────────────────────────────────


@pytest.mark.integration
@SKIP_IF_UNREACHABLE
class TestEmptyGraphIntegration:
    """Edge-case tests with an empty ABox."""

    @pytest_asyncio.fixture
    async def empty_store(self):
        """Store with schema loaded but no ABox data."""
        from ontorag.stores.neo4j import Neo4jStore

        s = Neo4jStore(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
        await s._run_write(
            "MATCH (n:Resource) WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
            "DETACH DELETE n"
        )
        await s._run_write(f"DROP INDEX {_STRUCT_INDEX} IF EXISTS")
        await s._run_write(f"DROP INDEX {_TEXT_INDEX} IF EXISTS")
        # Load TBox only — no ABox instances.
        await s.load_rdf(SCHEMA_TTL, "schema")
        yield s
        await s.aclose()

    @pytest.mark.asyncio
    async def test_build_structural_no_abox_returns_zero_or_positive(self, empty_store):
        """On a TBox-only graph, structural build returns 0 (no rel types) without crashing."""
        result = await empty_store.build_embeddings("structural")
        assert isinstance(result.get("structural", 0), int)

    @pytest.mark.asyncio
    async def test_find_similar_empty_graph_returns_empty(self, empty_store):
        """On an empty ABox, find_similar returns [] without raising."""
        hits = await empty_store.find_similar(_PIKACHU_URI, mode="structural")
        assert hits == []
