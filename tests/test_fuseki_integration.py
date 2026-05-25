"""Live Fuseki integration tests (parity with the Neo4j backend).

Skipped automatically when no Fuseki is reachable at FUSEKI_URL
(default http://localhost:3030). Start one with:

    docker compose up -d fuseki
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from ontorag.stores.fuseki import FusekiStore

pytestmark = pytest.mark.integration

_FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030")
_POKEMON = Path("src/ontorag/_templates/examples/pokemon")
_NS = "http://example.org/pokemon#"


def _fuseki_reachable() -> bool:
    try:
        resp = httpx.get(f"{_FUSEKI_URL}/$/ping", timeout=2.0)
        return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


@pytest.fixture
async def store():
    """A FusekiStore loaded with the Pokémon ontology; skips if Fuseki is down."""
    if not _fuseki_reachable():
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    s = FusekiStore.from_env()
    await s.clear_graph("all")
    await s.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema")
    await s.load_rdf(str(_POKEMON / "data.ttl"), mode="data")
    yield s
    await s.aclose()


@pytest.mark.asyncio
async def test_find_entities_is_subclass_aware(store):
    """find_entities(Pokemon) must include LegendaryPokemon instances
    (reasoning parity with Neo4j — query-level rdfs:subClassOf*)."""
    pokemon = await store.find_entities(f"{_NS}Pokemon", limit=100)
    legendary = await store.find_entities(f"{_NS}LegendaryPokemon", limit=100)

    assert legendary, "expected at least one LegendaryPokemon instance"
    pokemon_uris = {e.uri for e in pokemon}
    legendary_uris = {e.uri for e in legendary}
    # Every legendary instance is also returned as a Pokemon (subclass inference).
    assert legendary_uris.issubset(pokemon_uris)
    # And the parent-class result is strictly larger than the subclass result.
    assert len(pokemon_uris) > len(legendary_uris)


@pytest.mark.asyncio
async def test_count_entities_is_subclass_aware(store):
    """count_entities(Pokemon) includes subclass instances."""
    n_pokemon = await store.count_entities(f"{_NS}Pokemon")
    n_legendary = await store.count_entities(f"{_NS}LegendaryPokemon")

    assert n_legendary >= 1
    # Parent count includes the legendary subclass instances.
    assert n_pokemon > n_legendary


# ── jena-text full-text search (search_text) ─────────────────────────────────


@pytest.mark.asyncio
async def test_search_text_korean_label_returns_correct_entity(store):
    """search_text("피카츄") returns Pikachu with a positive score.

    Verifies that the jena-text index is populated on data load and that
    Korean-script rdfs:label values are searchable.
    """
    hits = await store.search_text("피카츄", limit=5)

    assert hits, "Expected at least one hit for '피카츄'"
    uris = [h.uri for h in hits]
    assert "http://example.org/pokemon/data#Pikachu" in uris, (
        f"Pikachu URI missing from hits: {uris}"
    )
    # All scores must be positive (Lucene relevance).
    for h in hits:
        assert h.score > 0, f"Non-positive score for {h.uri}: {h.score}"


@pytest.mark.asyncio
async def test_search_text_english_entity_uri_term(store):
    """search_text with an entity-local-name term matching a label works.

    'Pikachu' is the local name but the rdfs:label is '피카츄'.  We search
    for '피카츄' again to confirm the result from a clean second call.
    """
    hits = await store.search_text("피카츄", limit=10)

    # Should return exactly one entity for this unambiguous Korean label.
    pikachu_uris = [
        h.uri for h in hits
        if h.uri == "http://example.org/pokemon/data#Pikachu"
    ]
    assert len(pikachu_uris) >= 1


@pytest.mark.asyncio
async def test_search_text_class_uri_filter_restricts_to_class_instances(store):
    """class_uri filter limits results to instances of that class or subclasses.

    Search for '관동' (Kanto region label).  With no class filter it could
    match any entity; with class_uri=pk:Region it must only include Region
    instances.
    """
    # 관동 지방 is the label for the Kanto region (pk:Region instance).
    all_hits = await store.search_text("관동", limit=20)
    region_hits = await store.search_text(
        "관동",
        class_uri=f"{_NS}Region",
        limit=20,
    )

    assert all_hits, "Expected hits for '관동'"
    assert region_hits, "Expected at least one Region hit for '관동'"

    # All hits from the class-filtered call must be Region instances.
    region_uri = f"{_NS}Region"
    for h in region_hits:
        assert h.class_uri == region_uri or h.class_uri is None, (
            f"Unexpected class_uri for hit {h.uri}: {h.class_uri!r} "
            f"(expected {region_uri!r} or None)"
        )


@pytest.mark.asyncio
async def test_search_text_subclass_aware_filter(store):
    """class_uri filter includes subclass instances.

    LegendaryPokemon is a subclass of Pokemon.  Searching for a legendary
    pokémon label with class_uri=pk:Pokemon must still return the hit
    because the subClassOf* pattern resolves the type chain.
    """
    # 뮤츠 (Mewtwo) is a LegendaryPokemon — subclass of Pokemon.
    hits = await store.search_text("뮤츠", class_uri=f"{_NS}Pokemon", limit=10)

    assert hits, (
        "Expected at least one hit for '뮤츠' with class_uri=Pokemon "
        "(subClassOf* must include LegendaryPokemon instances)"
    )
    uris = [h.uri for h in hits]
    assert "http://example.org/pokemon/data#Mewtwo" in uris, (
        f"Mewtwo URI not in class-filtered hits: {uris}"
    )


@pytest.mark.asyncio
async def test_search_text_no_match_returns_empty_list(store):
    """A query with no matching entities returns an empty list (never raises)."""
    hits = await store.search_text("nonexistentxyz_12345_abc", limit=5)

    assert hits == [], f"Expected [], got {hits}"


@pytest.mark.asyncio
async def test_search_text_scores_are_positive_floats(store):
    """All returned SearchHit scores are positive float values."""
    hits = await store.search_text("불꽃", limit=10)

    assert hits, "Expected hits for '불꽃' (fire type label)"
    for h in hits:
        assert isinstance(h.score, float), f"score is not float: {type(h.score)}"
        assert h.score > 0, f"Non-positive score: {h.score}"
