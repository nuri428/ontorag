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


# ── Unit tests: helper validation ─────────────────────────────────────────────


def test_scoped_graph_rejects_bad_kind():
    """scoped_graph raises on an unknown graph kind."""
    with pytest.raises(ValueError, match="kind must be"):
        _scoped_graph("pkmn", "bogus")


def test_scoped_graph_rejects_bad_id():
    """scoped_graph propagates validate_ontology_id's ValueError on bad ids."""
    with pytest.raises(ValueError, match="Invalid ontology id"):
        _scoped_graph("pk:Foo", "data")


# ── HIGH #1 regression: status + dump_graph reflect per-ontology data ─────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_status_reflects_per_ontology_data(store_clean, tmp_path):
    """status() reports data_loaded/schema_loaded=True when data lives only in
    a per-ontology graph (not the legacy default graphs)."""
    # Load ONLY under ontology="other" — nothing in the legacy default graphs.
    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    st = await store_clean.status()
    assert st.connected is True
    assert st.data_loaded is True, (
        "status must report data_loaded=True for per-ontology ABox"
    )
    assert st.schema_loaded is True, (
        "status must report schema_loaded=True for per-ontology TBox"
    )
    assert st.triple_count and st.triple_count > 0, (
        "status triple_count must count per-ontology graphs (union)"
    )


@pytest.mark.asyncio
@pytestmark_integration
async def test_dump_graph_scoped_returns_per_ontology_triples(store_clean, tmp_path):
    """dump_graph(ontology='other') returns that ontology's triples; default
    dump (ontology=None) does NOT contain them."""
    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    scoped_dump = await store_clean.dump_graph("all", fmt="ttl", ontology="other")
    scoped_text = scoped_dump.decode()
    assert "Widget" in scoped_text, (
        f"Scoped dump must contain Widget triples; got: {scoped_text!r}"
    )

    # The legacy default graphs are empty, so an unscoped dump is empty of Widget.
    default_dump = await store_clean.dump_graph("all", fmt="ttl", ontology=None)
    assert "Widget" not in default_dump.decode(), (
        "Default-graph dump must not contain per-ontology 'other' triples"
    )


# ── HIGH #3 regression: scoped direct-match arm (no subclass) + isolation ─────


@pytest.mark.asyncio
@pytestmark_integration
async def test_scoped_direct_match_no_subclass_isolated(store_clean, tmp_path):
    """find_entities/count for a class with NO subclass exercises the scoped
    direct-match arm. It must return the instance when scoped to its own
    ontology and zero when scoped to a different ontology."""
    # Load pokemon under pkmn and the Widget ontology under other.
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")
    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    # Widget has no subclasses → only the direct-match arm can match it.
    widgets = await store_clean.find_entities(
        f"{_NS_OTHER}Widget", ontology="other", limit=100
    )
    assert widgets, "Direct-match arm must return Widget when scoped to 'other'"
    n_widget = await store_clean.count_entities(f"{_NS_OTHER}Widget", ontology="other")
    assert n_widget == len(widgets) == 1

    # Scoped to a different ontology: direct-match arm must not leak.
    widget_in_pkmn = await store_clean.find_entities(
        f"{_NS_OTHER}Widget", ontology="pkmn", limit=100
    )
    assert widget_in_pkmn == [], "Direct-match arm must be isolated to its ontology"
    assert await store_clean.count_entities(f"{_NS_OTHER}Widget", ontology="pkmn") == 0


@pytest.mark.asyncio
@pytestmark_integration
async def test_scoped_find_entities_still_subclass_aware(store_clean):
    """Scoped find_entities keeps subclass inference (subClassOf* arm) intact:
    find_entities(Pokemon, ontology='pkmn') includes LegendaryPokemon."""
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")

    pokemon = await store_clean.find_entities(
        f"{_NS_PKMN}Pokemon", ontology="pkmn", limit=200
    )
    legendary = await store_clean.find_entities(
        f"{_NS_PKMN}LegendaryPokemon", ontology="pkmn", limit=200
    )
    assert legendary, "Expected at least one LegendaryPokemon"
    pokemon_uris = {e.uri for e in pokemon}
    legendary_uris = {e.uri for e in legendary}
    assert legendary_uris.issubset(pokemon_uris), (
        "Scoped find_entities must still include subclass instances"
    )
    assert len(pokemon_uris) > len(legendary_uris)


# ── HIGH #2 regression: scoped search_text recall ─────────────────────────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_scoped_search_text_recall_and_isolation(store_clean, tmp_path):
    """search_text scoped to 'other' returns Widget (recall) and never a
    Pokémon hit (isolation), even though Pokémon dominates the index size."""
    # pkmn is large; other is tiny — exercises the recall trade-off.
    await store_clean.load_rdf(str(_POKEMON / "schema.ttl"), mode="schema", ontology="pkmn")
    await store_clean.load_rdf(str(_POKEMON / "data.ttl"), mode="data", ontology="pkmn")
    other_schema, other_data = _load_other(tmp_path)
    await store_clean.load_rdf(other_schema, mode="schema", ontology="other")
    await store_clean.load_rdf(other_data, mode="data", ontology="other")

    # Scoped to "other": the Widget label must be recalled.
    other_hits = await store_clean.search_text("Widget", ontology="other", limit=10)
    other_uris = {h.uri for h in other_hits}
    assert f"{_NS_OTHER}w1" in other_uris, (
        f"Scoped search must recall the Widget instance; got {other_uris}"
    )
    # No Pokémon URI may leak into the 'other'-scoped result.
    assert all(_NS_OTHER in u or "other" in u for u in other_uris), (
        f"Scoped 'other' search leaked non-other URIs: {other_uris}"
    )

    # Scoped to "pkmn": Widget must NOT appear.
    pkmn_hits = await store_clean.search_text("Widget", ontology="pkmn", limit=10)
    assert f"{_NS_OTHER}w1" not in {h.uri for h in pkmn_hits}, (
        "Widget must not appear in a 'pkmn'-scoped search"
    )


# ── HIGH #4 regression: scoped traverse predicate-label does not leak ─────────


@pytest.mark.asyncio
@pytestmark_integration
async def test_scoped_traverse_predicate_label_no_cross_ontology(store_clean, tmp_path):
    """A scoped traverse must use only its own ontology's predicate rdfs:label.

    Two ontologies declare the SAME predicate URI with DIFFERENT labels.
    Traversing in ontology 'a' must surface 'a's label, never 'b's.
    """
    ns = "http://example.org/shared#"
    pred = f"{ns}linkedTo"

    # Ontology A: predicate label "Label A", one edge n1 -> n2.
    a_schema = tmp_path / "a_schema.ttl"
    a_schema.write_text(dedent(f"""\
        @prefix owl:  <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix ex:   <{ns}> .
        ex:Node a owl:Class .
        ex:linkedTo a owl:ObjectProperty ; rdfs:label "Label A" .
    """))
    a_data = tmp_path / "a_data.ttl"
    a_data.write_text(dedent(f"""\
        @prefix ex: <{ns}> .
        ex:n1 a ex:Node ; ex:linkedTo ex:n2 .
        ex:n2 a ex:Node .
    """))

    # Ontology B: SAME predicate URI, different label "Label B".
    b_schema = tmp_path / "b_schema.ttl"
    b_schema.write_text(dedent(f"""\
        @prefix owl:  <http://www.w3.org/2002/07/owl#> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
        @prefix ex:   <{ns}> .
        ex:linkedTo a owl:ObjectProperty ; rdfs:label "Label B" .
    """))

    await store_clean.clear_graph("all", ontology="a")
    await store_clean.clear_graph("all", ontology="b")
    try:
        await store_clean.load_rdf(str(a_schema), mode="schema", ontology="a")
        await store_clean.load_rdf(str(a_data), mode="data", ontology="a")
        await store_clean.load_rdf(str(b_schema), mode="schema", ontology="b")

        result = await store_clean.traverse(f"{ns}n1", max_depth=1, ontology="a")
        link_edges = [e for e in result.edges if e.get("predicate") == pred]
        assert link_edges, "Expected a linkedTo edge in the scoped traversal"
        for e in link_edges:
            assert e.get("predicate_label") == "Label A", (
                f"Scoped traverse leaked another ontology's predicate label: "
                f"{e.get('predicate_label')!r} (expected 'Label A')"
            )
    finally:
        await store_clean.clear_graph("all", ontology="a")
        await store_clean.clear_graph("all", ontology="b")
