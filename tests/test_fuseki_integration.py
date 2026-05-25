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
