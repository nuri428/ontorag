"""Tests for multi-ontology scoping (v0.5).

Unit tests: _scoped_graph / _graph_clause helpers in fuseki.py (no Fuseki needed).
Integration tests: live Fuseki isolation + union behavior (skip if Fuseki down).
"""

from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import httpx
import pytest

from ontorag.stores.fuseki import FusekiStore, _graph_clause, _scoped_graph

pytestmark_integration = pytest.mark.integration

_FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030")

# Minimal "other" ontology — TBox and ABox are separate so mode detection is
# deterministic (TBox: only owl:Class declarations; ABox: only instances).
_OTHER_SCHEMA_TTL = dedent("""\
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix ex:  <http://example.org/other#> .
    ex:Widget a owl:Class .
""")

_OTHER_DATA_TTL = dedent("""\
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix ex:   <http://example.org/other#> .
    ex:w1 a ex:Widget .
    ex:w1 rdfs:label "Widget One" .
""")

_NS_OTHER = "http://example.org/other#"
_NS_PKMN = "http://example.org/pokemon#"
_POKEMON = Path("src/ontorag/_templates/examples/pokemon")


# ── Helper ────────────────────────────────────────────────────────────────────


def _fuseki_reachable() -> bool:
    try:
        resp = httpx.get(f"{_FUSEKI_URL}/$/ping", timeout=2.0)
        return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


# ── Unit tests: _scoped_graph ─────────────────────────────────────────────────


def test_scoped_graph_none_schema_returns_none():
    """ontology=None → None (signals union default graph, no GRAPH wrapper)."""
    assert _scoped_graph(None, "schema") is None


def test_scoped_graph_none_data_returns_none():
    """ontology=None → None for data as well."""
    assert _scoped_graph(None, "data") is None


def test_scoped_graph_id_schema_returns_per_ontology_uri():
    """ontology='pkmn' → correct per-ontology schema URI."""
    assert _scoped_graph("pkmn", "schema") == "urn:ontorag:pkmn:schema"


def test_scoped_graph_id_data_returns_per_ontology_uri():
    """ontology='pkmn' → correct per-ontology data URI."""
    assert _scoped_graph("pkmn", "data") == "urn:ontorag:pkmn:data"


def test_scoped_graph_id_with_hyphen_and_underscore():
    """Hyphens and underscores are valid in the slug."""
    assert _scoped_graph("my-onto_v2", "data") == "urn:ontorag:my-onto_v2:data"


# ── Unit tests: _graph_clause ─────────────────────────────────────────────────


def test_graph_clause_none_wraps_in_braces_only():
    """graph_uri=None → bare { body } without GRAPH keyword."""
    result = _graph_clause(None, "?s ?p ?o .")
    assert result == "{ ?s ?p ?o . }"
    assert "GRAPH" not in result


def test_graph_clause_uri_wraps_in_graph_keyword():
    """graph_uri=<uri> → GRAPH <uri> { body }."""
    result = _graph_clause("urn:ontorag:pkmn:data", "?s ?p ?o .")
    assert "GRAPH <urn:ontorag:pkmn:data>" in result
    assert "?s ?p ?o ." in result


def test_graph_clause_body_preserved():
    """The body content is not altered."""
    body = "?inst a <http://example.org/Foo> . FILTER(?inst != <x:y>)"
    result = _graph_clause("urn:x", body)
    assert body in result


# ── Integration fixtures ───────────────────────────────────────────────────────


@pytest.fixture
async def store_clean():
    """Clean FusekiStore — clears ALL graphs before and after each test."""
    if not _fuseki_reachable():
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    s = FusekiStore.from_env()
    # Clear the legacy defaults AND per-ontology graphs used in tests.
    await s.clear_graph("all")
    await s.clear_graph("all", ontology="pkmn")
    await s.clear_graph("all", ontology="other")
    yield s
    await s.clear_graph("all")
    await s.clear_graph("all", ontology="pkmn")
    await s.clear_graph("all", ontology="other")
    await s.aclose()


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_load_rdf_with_ontology_sets_load_result(store_clean):
    """load_rdf(ontology='pkmn') returns LoadResult with ontology='pkmn'."""
    result = await store_clean.load_rdf(
        str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn"
    )
    assert result.ontology == "pkmn"
    assert result.mode == "schema"
    assert result.triples_loaded > 0


@pytest.mark.asyncio
@pytestmark_integration
async def test_load_rdf_none_ontology_returns_none(store_clean):
    """load_rdf(ontology=None) returns LoadResult with ontology=None (legacy)."""
    result = await store_clean.load_rdf(
        str(_POKEMON / "schema.ttl"), mode="schema", ontology=None
    )
    assert result.ontology is None


def _load_other(tmp_path: Path) -> tuple[str, str]:
    """Write the 'other' schema and data TTL files and return their paths."""
    schema_file = tmp_path / "other_schema.ttl"
    data_file = tmp_path / "other_data.ttl"
    schema_file.write_text(_OTHER_SCHEMA_TTL)
    data_file.write_text(_OTHER_DATA_TTL)
    return str(schema_file), str(data_file)


@pytest.mark.asyncio
@pytestmark_integration
async def test_find_entities_scoped_isolates_pkmn(store_clean, tmp_path):
    """find_entities scoped to 'pkmn' returns only Pokémon instances."""
    # Load pokemon under ontology="pkmn".
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")

    # Load a tiny "other" ontology under ontology="other".
    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    # Scoped to "pkmn": should return Pokémon, not Widgets.
    pkmn_results = await store_clean.find_entities(
        f"{_NS_PKMN}Pokemon", ontology="pkmn", limit=100
    )
    assert pkmn_results, "Expected Pokémon instances when scoped to 'pkmn'"

    # Scoped to "pkmn": Widget class from "other" must not appear.
    other_results = await store_clean.find_entities(
        f"{_NS_OTHER}Widget", ontology="pkmn", limit=100
    )
    assert other_results == [], "Expected no Widget when scoped to 'pkmn'"


@pytest.mark.asyncio
@pytestmark_integration
async def test_find_entities_scoped_other_isolates_widgets(store_clean, tmp_path):
    """find_entities scoped to 'other' returns only Widget instances."""
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")

    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    # Scoped to "other": Widget should be found, Pokemon should not.
    widget_results = await store_clean.find_entities(
        f"{_NS_OTHER}Widget", ontology="other", limit=100
    )
    assert widget_results, "Expected Widget instance when scoped to 'other'"

    pkmn_in_other = await store_clean.find_entities(
        f"{_NS_PKMN}Pokemon", ontology="other", limit=100
    )
    assert pkmn_in_other == [], "Expected no Pokémon when scoped to 'other'"


@pytest.mark.asyncio
@pytestmark_integration
async def test_find_entities_union_returns_all(store_clean, tmp_path):
    """find_entities with ontology=None returns union across both ontologies."""
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")

    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    # ontology=None → union default graph (tdb2:unionDefaultGraph true).
    pkmn_union = await store_clean.find_entities(
        f"{_NS_PKMN}Pokemon", ontology=None, limit=100
    )
    widget_union = await store_clean.find_entities(
        f"{_NS_OTHER}Widget", ontology=None, limit=100
    )
    assert pkmn_union, "Expected Pokémon in union query"
    assert widget_union, "Expected Widget in union query"


@pytest.mark.asyncio
@pytestmark_integration
async def test_count_entities_respects_scope(store_clean, tmp_path):
    """count_entities is consistent with ontology scope."""
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")

    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    # pkmn scope: positive count for Pokemon, zero for Widget.
    n_pkmn = await store_clean.count_entities(f"{_NS_PKMN}Pokemon", ontology="pkmn")
    n_widget_in_pkmn = await store_clean.count_entities(
        f"{_NS_OTHER}Widget", ontology="pkmn"
    )
    assert n_pkmn > 0, "Expected positive count for Pokemon in 'pkmn' scope"
    assert n_widget_in_pkmn == 0, "Expected zero Widget in 'pkmn' scope"

    # other scope: positive count for Widget, zero for Pokemon.
    n_widget = await store_clean.count_entities(f"{_NS_OTHER}Widget", ontology="other")
    n_pkmn_in_other = await store_clean.count_entities(
        f"{_NS_PKMN}Pokemon", ontology="other"
    )
    assert n_widget > 0, "Expected positive count for Widget in 'other' scope"
    assert n_pkmn_in_other == 0, "Expected zero Pokemon in 'other' scope"


@pytest.mark.asyncio
@pytestmark_integration
async def test_get_schema_scoped_shows_only_own_classes(store_clean, tmp_path):
    """get_schema scoped to 'pkmn' lists only Pokémon classes (not Widget)."""
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")

    other_schema, _ = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")

    schema_pkmn = await store_clean.get_schema(ontology="pkmn")
    class_uris = {c.uri for c in schema_pkmn.classes}

    # Pokémon TBox classes should be present.
    assert any(_NS_PKMN in u for u in class_uris), (
        f"Expected Pokémon classes in scoped schema; got {class_uris}"
    )
    # Widget from "other" should NOT appear.
    assert f"{_NS_OTHER}Widget" not in class_uris, (
        "Widget class must not appear in 'pkmn'-scoped schema"
    )


@pytest.mark.asyncio
@pytestmark_integration
async def test_clear_graph_with_ontology_removes_only_that_graph(store_clean, tmp_path):
    """clear_graph(ontology='other') removes only the 'other' graphs."""
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")

    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    # Confirm 'other' has data before clearing.
    n_before = await store_clean.count_entities(f"{_NS_OTHER}Widget", ontology="other")
    assert n_before > 0

    # Clear only 'other'.
    removed = await store_clean.clear_graph("all", ontology="other")
    assert removed.get("data", 0) > 0 or removed.get("schema", 0) > 0

    # 'other' Widget is gone.
    n_after = await store_clean.count_entities(f"{_NS_OTHER}Widget", ontology="other")
    assert n_after == 0, "Widget must be gone after clear_graph(ontology='other')"

    # 'pkmn' Pokemon data is still present.
    n_pkmn_after = await store_clean.count_entities(
        f"{_NS_PKMN}Pokemon", ontology="pkmn"
    )
    assert n_pkmn_after > 0, "Pokémon data must survive clearing 'other' ontology"
