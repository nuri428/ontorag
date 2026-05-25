"""Integration tests for Fuseki + Qdrant graph-embedding (structural + textual).

Requires BOTH a running Fuseki instance (http://localhost:3030) AND a running
Qdrant instance (http://localhost:6333).

Marked with ``@pytest.mark.integration`` — skipped gracefully when either
service is unreachable.

Textual embeddings use a deterministic FAKE EmbeddingProvider so no real
OpenAI/Ollama API calls or keys are required.

Pokemon data is loaded fresh at the start of each test class; Qdrant
collections are deleted and recreated per run to ensure isolation.
"""

from __future__ import annotations

import asyncio

import pytest

from ontorag.stores._qdrant import STRUCT_COLLECTION, TEXT_COLLECTION, QdrantWrapper
from ontorag.stores.fuseki import FusekiStore

# ── Constants ─────────────────────────────────────────────────────────────────

FUSEKI_URL = "http://localhost:3030"
QDRANT_URL = "http://localhost:6333"

SCHEMA_TTL = "src/ontorag/_templates/examples/pokemon/schema.ttl"
DATA_TTL = "src/ontorag/_templates/examples/pokemon/data.ttl"

_PIKACHU_URI = "http://example.org/pokemon/data#Pikachu"
_RAICHU_URI = "http://example.org/pokemon/data#Raichu"
_CHARMANDER_URI = "http://example.org/pokemon/data#Charmander"
_BULBASAUR_URI = "http://example.org/pokemon/data#Bulbasaur"

# Fake embedding dimension — small to keep tests fast.
_FAKE_DIM: int = 8


# ── Fake embedding provider ───────────────────────────────────────────────────


class FakeEmbeddingProvider:
    """Deterministic hash-based embedding provider (no external calls)."""

    model: str = "fake-embed-v1"
    dimension: int = _FAKE_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return a stable vector derived from the text hash."""
        result = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFF
            vec = [float((h >> (i * 4)) & 0xF) / 15.0 for i in range(self.dimension)]
            # L2-normalise so cosine similarity is meaningful.
            norm = sum(x * x for x in vec) ** 0.5
            if norm > 0:
                vec = [x / norm for x in vec]
            result.append(vec)
        return result


# ── Availability checks ───────────────────────────────────────────────────────


def _is_fuseki_up() -> bool:
    """Return True if Fuseki is reachable at FUSEKI_URL."""
    import httpx

    try:
        r = httpx.get(f"{FUSEKI_URL}/$/ping", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _is_qdrant_up() -> bool:
    """Return True if Qdrant is reachable at QDRANT_URL."""
    import httpx

    try:
        r = httpx.get(f"{QDRANT_URL}/healthz", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


requires_both = pytest.mark.skipif(
    not (_is_fuseki_up() and _is_qdrant_up()),
    reason="Requires live Fuseki (http://localhost:3030) and Qdrant (http://localhost:6333)",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


async def _fresh_store() -> FusekiStore:
    """Create a FusekiStore loaded with the pokemon example ontology."""
    store = FusekiStore(
        url=FUSEKI_URL,
        dataset="ontorag",
        user="admin",
        password="admin",
    )
    # Clear existing data to ensure test isolation.
    await store.clear_graph("all")
    await store.load_rdf(SCHEMA_TTL, mode="schema")
    await store.load_rdf(DATA_TTL, mode="data")
    return store


async def _clean_qdrant() -> None:
    """Drop the embedding collections so each test run starts clean."""
    qdrant = QdrantWrapper(url=QDRANT_URL)
    await qdrant.delete_collection(STRUCT_COLLECTION)
    await qdrant.delete_collection(TEXT_COLLECTION)
    await qdrant.aclose()


# ── Structural embedding integration ─────────────────────────────────────────


@pytest.mark.integration
class TestStructuralEmbeddingIntegration:
    """Live Fuseki + Qdrant: build structural embeddings and query them."""

    @pytest.mark.asyncio
    async def test_build_structural_returns_positive_count(self):
        """build_embeddings(structural) returns a positive node count."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            result = await store.build_embeddings("structural")
        finally:
            await store.aclose()

        assert "structural" in result
        assert result["structural"] > 0, (
            "Expected at least one structural embedding; got 0."
        )

    @pytest.mark.asyncio
    async def test_find_similar_structural_excludes_self(self):
        """find_similar must not return the query entity itself."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("structural")
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="structural")
        finally:
            await store.aclose()

        uris = [h.uri for h in hits]
        assert _PIKACHU_URI not in uris, "Self-hit must be excluded from results."

    @pytest.mark.asyncio
    async def test_find_similar_structural_returns_pokemon(self):
        """Structural similarity should return other Pokémon (same graph topology)."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("structural")
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="structural")
        finally:
            await store.aclose()

        # At least one hit should be present.
        assert len(hits) > 0, "Expected at least one similar entity."
        # All hits should have scores in valid range.
        for h in hits:
            assert 0.0 <= h.score <= 1.0 or h.score > 0.0, (
                f"Score {h.score} for {h.uri} should be non-negative."
            )
            assert h.mode == "structural"

    @pytest.mark.asyncio
    async def test_raichu_similar_to_pikachu_structurally(self):
        """Raichu (evolves from Pikachu) should appear in Pikachu's structural neighbours.

        Both share the same graph neighbourhood (same evolution chain, trainer,
        region), so FastRP should place them close together.
        """
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("structural")
            hits = await store.find_similar(_PIKACHU_URI, top_k=10, mode="structural")
        finally:
            await store.aclose()

        uris = [h.uri for h in hits]
        # Raichu is Pikachu's direct evolution — very likely to be structurally close.
        # We use a soft assertion: if data changes, this should still hold for top-10.
        assert _RAICHU_URI in uris, (
            f"Raichu ({_RAICHU_URI}) expected in Pikachu's structural top-10; got: {uris}"
        )

    @pytest.mark.asyncio
    async def test_find_similar_empty_without_embeddings(self):
        """find_similar returns [] when no embeddings have been built (fresh Qdrant)."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            # Do NOT call build_embeddings — no vectors in Qdrant.
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="structural")
        finally:
            await store.aclose()

        assert hits == [], f"Expected [] without embeddings, got {hits}"

    @pytest.mark.asyncio
    async def test_structural_all_hits_have_class_uri(self):
        """All structural hits should report a non-vocab class_uri."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("structural")
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="structural")
        finally:
            await store.aclose()

        for h in hits:
            if h.class_uri is not None:
                assert "owl#Class" not in h.class_uri, (
                    f"TBox type leaked into class_uri: {h.class_uri}"
                )

    @pytest.mark.asyncio
    async def test_class_uri_is_domain_class_and_stable(self):
        """HIGH #2: class_uri resolves to the real domain class, stable across calls.

        Raichu is a pk:Pokemon — its class_uri must be the Pokemon domain class
        (never owl:Class / a vocab type), and identical across repeated queries.
        """
        _POKEMON_CLASS = "http://example.org/pokemon#Pokemon"
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("structural")
            hits1 = await store.find_similar(_PIKACHU_URI, top_k=10, mode="structural")
            hits2 = await store.find_similar(_PIKACHU_URI, top_k=10, mode="structural")
        finally:
            await store.aclose()

        raichu1 = next((h for h in hits1 if h.uri == _RAICHU_URI), None)
        raichu2 = next((h for h in hits2 if h.uri == _RAICHU_URI), None)
        assert raichu1 is not None, "Raichu expected in Pikachu's structural top-10."
        assert raichu2 is not None
        # class_uri must be the real domain class, not a vocab type.
        assert raichu1.class_uri == _POKEMON_CLASS, (
            f"Expected Pokemon domain class, got {raichu1.class_uri!r}"
        )
        # Deterministic across calls.
        assert raichu1.class_uri == raichu2.class_uri


# ── Textual embedding integration ─────────────────────────────────────────────


@pytest.mark.integration
class TestTextualEmbeddingIntegration:
    """Live Fuseki + Qdrant: build textual embeddings with a fake provider."""

    @pytest.mark.asyncio
    async def test_build_textual_returns_positive_count(self):
        """build_embeddings(textual) returns a positive count with fake provider."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            result = await store.build_embeddings("textual", FakeEmbeddingProvider())
        finally:
            await store.aclose()

        assert "textual" in result
        assert result["textual"] > 0, (
            "Expected at least one textual embedding; got 0."
        )

    @pytest.mark.asyncio
    async def test_find_similar_textual_excludes_self(self):
        """Textual mode must not return the query entity itself."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("textual", FakeEmbeddingProvider())
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="textual")
        finally:
            await store.aclose()

        uris = [h.uri for h in hits]
        assert _PIKACHU_URI not in uris, "Self-hit must be excluded from results."
        for h in hits:
            assert h.mode == "textual"

    @pytest.mark.asyncio
    async def test_find_similar_textual_returns_hits(self):
        """Textual mode should return non-empty results."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("textual", FakeEmbeddingProvider())
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="textual")
        finally:
            await store.aclose()

        assert len(hits) > 0, "Expected at least one textual hit."


# ── Hybrid (RRF) integration ──────────────────────────────────────────────────


@pytest.mark.integration
class TestHybridEmbeddingIntegration:
    """Live Fuseki + Qdrant: hybrid mode fuses structural + textual with RRF."""

    @pytest.mark.asyncio
    async def test_build_both_modes(self):
        """build_embeddings('both') populates both Qdrant collections."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            result = await store.build_embeddings("both", FakeEmbeddingProvider())
        finally:
            await store.aclose()

        assert result.get("structural", 0) > 0
        assert result.get("textual", 0) > 0

    @pytest.mark.asyncio
    async def test_hybrid_excludes_self_and_returns_hits(self):
        """Hybrid mode must not return the query entity and must return results."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("both", FakeEmbeddingProvider())
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="hybrid")
        finally:
            await store.aclose()

        assert len(hits) > 0, "Expected at least one hybrid hit."
        uris = [h.uri for h in hits]
        assert _PIKACHU_URI not in uris, "Self-hit must be excluded from hybrid results."
        for h in hits:
            assert h.mode == "hybrid"
            assert h.score > 0.0

    @pytest.mark.asyncio
    async def test_hybrid_empty_when_no_embeddings(self):
        """Hybrid returns [] when no embeddings have been built."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            hits = await store.find_similar(_PIKACHU_URI, top_k=5, mode="hybrid")
        finally:
            await store.aclose()

        assert hits == [], f"Expected [] without embeddings, got {hits}"

    @pytest.mark.asyncio
    async def test_hybrid_top_k_respected(self):
        """Hybrid mode must honour the top_k cap."""
        await _clean_qdrant()
        store = await _fresh_store()
        try:
            await store.build_embeddings("both", FakeEmbeddingProvider())
            hits = await store.find_similar(_PIKACHU_URI, top_k=3, mode="hybrid")
        finally:
            await store.aclose()

        assert len(hits) <= 3, f"Expected at most 3 hits, got {len(hits)}"
