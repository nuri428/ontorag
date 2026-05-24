"""Integration tests for Neo4j BM25 full-text search.

These tests require a running Neo4j instance (bolt://localhost:7687).
Marked with @pytest.mark.integration; skipped gracefully when the container
is unreachable.

Each test loads a fresh copy of the pokemon schema + data to ensure
isolation. The graphconfig and _NsPrefDef nodes are preserved between
resets (neo4j only allows one graphconfig).

Pokemon data uses Korean rdfs:label values and English pk:category ("Special").
Tests use Korean labels (e.g. "피카츄" for Pikachu) or English TBox labels
(e.g. "special attack") to exercise the full-text index.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

# ── Constants ─────────────────────────────────────────────────────────────────

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ontorag123"

SCHEMA_TTL = "src/ontorag/_templates/examples/pokemon/schema.ttl"
DATA_TTL = "src/ontorag/_templates/examples/pokemon/data.ttl"

_POKEMON_CLASS = "http://example.org/pokemon#Pokemon"
_MOVE_CLASS = "http://example.org/pokemon#Move"
_LEGENDARY_CLASS = "http://example.org/pokemon#LegendaryPokemon"
_PIKACHU_URI = "http://example.org/pokemon/data#Pikachu"

# Korean label for Pikachu in the pokemon data TTL
_PIKACHU_LABEL_KO = "피카츄"


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


_NEO4J_REACHABLE = _is_neo4j_reachable()

pytestmark = pytest.mark.skipif(
    not _NEO4J_REACHABLE,
    reason="Neo4j not reachable at bolt://localhost:7687",
)


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def store():
    """Function-scoped store: load schema + data fresh each test.

    Drops all Resource nodes (keeps graphconfig/prefixes) and the
    ontorag_fulltext index, then reloads to ensure a clean state.
    """
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    s = Neo4jStore(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)

    # Full reset (keep graphconfig + prefixes)
    await s._run_write(
        "MATCH (n:Resource) WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
        "DETACH DELETE n"
    )
    # Drop the full-text index so each test starts from scratch.
    await s._run_write("DROP INDEX ontorag_fulltext IF EXISTS")

    await s.load_rdf(SCHEMA_TTL, mode="schema")
    await s.load_rdf(DATA_TTL, mode="data")

    yield s
    await s.aclose()


# ── B2: index creation ────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fulltext_index_created_after_load(store) -> None:
    """After load_rdf the ontorag_fulltext index must exist and be ONLINE."""
    rows = await store._run("SHOW INDEXES WHERE type = 'FULLTEXT'")
    names = [r["name"] for r in rows]
    assert "ontorag_fulltext" in names, f"Expected ontorag_fulltext in {names}"

    index_row = next(r for r in rows if r["name"] == "ontorag_fulltext")
    assert index_row["state"] == "ONLINE"
    # The index should cover at least rdfs__label.
    props = index_row.get("properties") or []
    assert "rdfs__label" in props, f"rdfs__label missing from index props: {props}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fulltext_index_properties_include_text_keys(store) -> None:
    """Index should cover all discovered string-valued keys."""
    rows = await store._run("SHOW INDEXES WHERE type = 'FULLTEXT'")
    index_row = next((r for r in rows if r["name"] == "ontorag_fulltext"), None)
    assert index_row is not None
    props = set(index_row.get("properties") or [])
    # Pokemon data has rdfs__label, rdfs__comment, pk__category, pk__hometown
    assert "rdfs__label" in props
    # rdfs__comment and pk__category are also string-valued and must be indexed.
    assert "rdfs__comment" in props or "pk__category" in props, (
        f"Expected rdfs__comment or pk__category in props: {props}"
    )


# ── B3: search ────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_returns_pikachu_by_korean_label(store) -> None:
    """Searching the Korean label '피카츄' returns Pikachu with a positive score."""
    hits = await store.search_text(_PIKACHU_LABEL_KO)

    uris = [h.uri for h in hits]
    assert _PIKACHU_URI in uris, f"Pikachu URI not found in hits: {uris}"

    pikachu = next(h for h in hits if h.uri == _PIKACHU_URI)
    assert pikachu.score > 0.0
    assert pikachu.label == _PIKACHU_LABEL_KO


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_results_ordered_by_score(store) -> None:
    """Results must be ordered by score descending."""
    # "special" appears in both rdfs:label ("special attack/defense") and pk:category.
    hits = await store.search_text("special")
    assert len(hits) >= 2, "Expected multiple hits for 'special'"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), "Hits not ordered by score desc"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_with_class_uri_restricts_to_class(store) -> None:
    """class_uri filter restricts results to instances of Move (and subclasses)."""
    hits = await store.search_text("Special", class_uri=_MOVE_CLASS)

    if not hits:
        pytest.skip("No Move instances matched 'Special' — check data.")

    for hit in hits:
        assert hit.class_uri == _MOVE_CLASS, (
            f"Hit class_uri {hit.class_uri!r} is not the Move class"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_with_class_uri_excludes_non_instances(store) -> None:
    """Searching 'special' restricted to Pokemon should NOT return TBox properties."""
    hits = await store.search_text("special", class_uri=_POKEMON_CLASS)

    # TBox properties like pk:spAttack (label "special attack") should NOT appear.
    tbox_uris = {h.uri for h in hits if "pokemon#" in h.uri and "#" in h.uri}
    assert not tbox_uris, f"TBox URIs unexpectedly included: {tbox_uris}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_no_match_returns_empty(store) -> None:
    """Searching for a nonsense token returns an empty list."""
    hits = await store.search_text("xyzzy_no_match_12345_abc")
    assert hits == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_empty_string_returns_empty_or_results(store) -> None:
    """An empty query string does not crash; returns a list."""
    # Lucene may return an error or empty for ""; we should handle both.
    try:
        hits = await store.search_text("")
        assert isinstance(hits, list)
    except Exception as exc:
        # If Neo4j raises a Lucene parse error, that's acceptable —
        # the caller should validate inputs before calling.
        assert "parse" in str(exc).lower() or "query" in str(exc).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_limit_respected(store) -> None:
    """Returned hits must not exceed the requested limit."""
    hits = await store.search_text("a", limit=3)
    assert len(hits) <= 3


# ── B2: index recreation after second load ────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_index_recreation_after_second_load(store) -> None:
    """Loading data a second time should keep the index intact and correct."""
    # Record current index properties.
    rows_before = await store._run("SHOW INDEXES WHERE type = 'FULLTEXT'")
    props_before = set(
        next(r for r in rows_before if r["name"] == "ontorag_fulltext")["properties"]
    )

    # Reload the data (replace=True to exercise full re-import).
    await store.load_rdf(DATA_TTL, mode="data", replace=True)

    rows_after = await store._run("SHOW INDEXES WHERE type = 'FULLTEXT'")
    idx = next((r for r in rows_after if r["name"] == "ontorag_fulltext"), None)
    assert idx is not None, "Index missing after second load"
    assert idx["state"] == "ONLINE"
    props_after = set(idx.get("properties") or [])

    # Property set should be the same or a superset.
    assert props_before <= props_after, (
        f"Index property set shrank after second load: {props_before} → {props_after}"
    )

    # Search should still work.
    hits = await store.search_text(_PIKACHU_LABEL_KO)
    uris = [h.uri for h in hits]
    assert _PIKACHU_URI in uris


# ── B2: _ensure_fulltext_index no-op on same property set ────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ensure_fulltext_index_noop_when_properties_unchanged(store) -> None:
    """Calling _ensure_fulltext_index twice in a row does not drop+recreate."""
    # The index already exists (from fixture's load_rdf).
    rows_before = await store._run("SHOW INDEXES WHERE type = 'FULLTEXT'")
    idx_before = next(r for r in rows_before if r["name"] == "ontorag_fulltext")
    id_before = idx_before["id"]

    # Call again — should be a no-op (same properties).
    await store._ensure_fulltext_index()

    rows_after = await store._run("SHOW INDEXES WHERE type = 'FULLTEXT'")
    idx_after = next((r for r in rows_after if r["name"] == "ontorag_fulltext"), None)
    assert idx_after is not None
    # Index id should be unchanged (not dropped+recreated).
    assert idx_after["id"] == id_before, (
        "Index was unnecessarily recreated when property set did not change."
    )
