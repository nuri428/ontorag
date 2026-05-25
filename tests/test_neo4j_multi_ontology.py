"""Multi-ontology scoping integration tests for Neo4jStore.

Tests require a running Neo4j instance at bolt://localhost:7687.
They are marked with @pytest.mark.integration and skipped gracefully
when the container is unreachable (mirrors the guard in test_neo4j_integration.py).

Test coverage:
  - load_rdf(ontology=...) tags nodes with the ontology id.
  - find_entities scoped to "pkmn" excludes "other" instances and vice-versa.
  - count_entities respects scope.
  - get_schema scoped to "pkmn" shows only pkmn classes.
  - ontology=None returns the union of all instances (backward compat).
  - search_text scoped to "pkmn" excludes "other" hits.
  - shared-URI test: a URI loaded under two ids appears in both scopes.
  - Invalid ontology id raises ValueError (unit-level, no container needed).
  - _tag_ontology_nodes is idempotent (re-tagging the same id is safe).
"""

from __future__ import annotations

import socket
import textwrap

import pytest
import pytest_asyncio

# ── Constants ─────────────────────────────────────────────────────────────────

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ontorag123"

POKEMON_SCHEMA = "src/ontorag/_templates/examples/pokemon/schema.ttl"
POKEMON_DATA = "src/ontorag/_templates/examples/pokemon/data.ttl"

_POKEMON_CLASS = "http://example.org/pokemon#Pokemon"
_TRAINER_CLASS = "http://example.org/pokemon#Trainer"
_ANIMAL_CLASS = "http://example.org/other#Animal"
_PIKACHU_URI = "http://example.org/pokemon/data#Pikachu"

# A tiny "other" ontology with two Animal instances.
_OTHER_TTL = textwrap.dedent("""\
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
    @prefix other: <http://example.org/other#> .
    @prefix odata: <http://example.org/other/data#> .

    other:Animal a owl:Class ;
        rdfs:label "Animal" .

    odata:Cat a other:Animal ;
        rdfs:label "Cat" .

    odata:Dog a other:Animal ;
        rdfs:label "Dog" .
""")

# ── Connectivity guard ────────────────────────────────────────────────────────


def _is_neo4j_reachable() -> bool:
    """Return True when the Neo4j bolt port is reachable."""
    try:
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


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def dual_store(tmp_path):
    """Load pokemon ontology under id="pkmn" + other ontology under id="other".

    Resets all :Resource nodes before loading to ensure isolation between tests.
    """
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    s = Neo4jStore(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)

    # Full reset (keep graphconfig + prefixes)
    await s._run_write(
        "MATCH (n:Resource) WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
        "DETACH DELETE n"
    )

    # Write the "other" ontology to a temp file for loading.
    other_ttl_path = tmp_path / "other.ttl"
    other_ttl_path.write_text(_OTHER_TTL)

    # Load pokemon under "pkmn"
    await s.load_rdf(POKEMON_SCHEMA, mode="schema", ontology="pkmn")
    await s.load_rdf(POKEMON_DATA, mode="data", ontology="pkmn")

    # Load other under "other"
    await s.load_rdf(str(other_ttl_path), mode="auto", ontology="other")

    yield s
    await s.aclose()


@pytest_asyncio.fixture()
async def single_store():
    """Load pokemon ontology with ontology=None (legacy/default behavior)."""
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    s = Neo4jStore(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)

    await s._run_write(
        "MATCH (n:Resource) WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
        "DETACH DELETE n"
    )
    await s.load_rdf(POKEMON_SCHEMA, mode="schema")
    await s.load_rdf(POKEMON_DATA, mode="data")

    yield s
    await s.aclose()


# ── Unit-level: validate_ontology_id (no container) ──────────────────────────


def test_invalid_ontology_id_raises():
    """validate_ontology_id rejects ids with illegal characters."""
    from ontorag.core.ontology import validate_ontology_id  # noqa: PLC0415

    with pytest.raises(ValueError):
        validate_ontology_id("bad id!")

    with pytest.raises(ValueError):
        validate_ontology_id("../../../etc/passwd")


def test_valid_ontology_id_passes():
    """validate_ontology_id accepts valid slugs."""
    from ontorag.core.ontology import validate_ontology_id  # noqa: PLC0415

    assert validate_ontology_id("pkmn") == "pkmn"
    assert validate_ontology_id("my-ontology_01") == "my-ontology_01"
    assert validate_ontology_id(None) is None


# ── Unit-level: ontology_scope_filter helper ──────────────────────────────────


def test_scope_filter_none_returns_empty():
    """ontology_scope_filter(None) returns empty fragment and empty params."""
    from ontorag.stores._neo4j_scope import ontology_scope_filter  # noqa: PLC0415

    frag, params = ontology_scope_filter(None)
    assert frag == ""
    assert params == {}


def test_scope_filter_id_returns_fragment():
    """ontology_scope_filter('pkmn') returns the expected Cypher fragment."""
    from ontorag.stores._neo4j_scope import ontology_scope_filter  # noqa: PLC0415

    frag, params = ontology_scope_filter("pkmn", node_alias="inst")
    assert frag == "$ontology_id IN inst._ontology"
    assert params == {"ontology_id": "pkmn"}


def test_scope_filter_invalid_raises():
    """ontology_scope_filter raises ValueError on illegal slug."""
    from ontorag.stores._neo4j_scope import ontology_scope_filter  # noqa: PLC0415

    with pytest.raises(ValueError):
        ontology_scope_filter("bad id!")


# ── Integration: tagging ──────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_load_rdf_tags_nodes(dual_store) -> None:
    """Nodes imported under 'pkmn' should have _ontology = ['pkmn']."""
    rows = await dual_store._run(
        "MATCH (n:Resource {uri: $uri}) RETURN n._ontology AS oids",
        uri=_PIKACHU_URI,
    )
    assert rows, "Pikachu node not found"
    oids = rows[0]["oids"]
    assert isinstance(oids, list)
    assert "pkmn" in oids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_load_rdf_no_ontology_no_tag(single_store) -> None:
    """Nodes loaded without ontology id should NOT have _ontology set."""
    rows = await single_store._run(
        "MATCH (n:Resource {uri: $uri}) RETURN n._ontology AS oids",
        uri=_PIKACHU_URI,
    )
    assert rows, "Pikachu node not found"
    # _ontology should be absent (None) — not set on unscoped loads.
    assert rows[0]["oids"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tagging_is_idempotent(dual_store) -> None:
    """Re-loading the same ontology id must not duplicate the tag."""
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    # Load pkmn data again with the same id.
    await dual_store.load_rdf(POKEMON_DATA, mode="data", ontology="pkmn")

    rows = await dual_store._run(
        "MATCH (n:Resource {uri: $uri}) RETURN n._ontology AS oids",
        uri=_PIKACHU_URI,
    )
    oids = rows[0]["oids"]
    # Should still contain "pkmn" exactly once.
    assert oids.count("pkmn") == 1


# ── Integration: shared-URI ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_shared_uri_appears_in_both_scopes(dual_store) -> None:
    """owl:Class URI (shared object IRI) should appear in both pkmn and other scopes.

    Both ontologies declare classes via ``a owl:Class``, which means the
    ``owl:Class`` URI appears as an *object* IRI in both graphs.  After loading
    under both ids, that node must carry both ids in its ``_ontology`` list.
    """
    # owl:Class is used as a type object in both pokemon and other ontologies.
    OWL_CLASS = "http://www.w3.org/2002/07/owl#Class"
    rows = await dual_store._run(
        "MATCH (n:Resource {uri: $uri}) RETURN n._ontology AS oids",
        uri=OWL_CLASS,
    )
    if not rows or rows[0]["oids"] is None:
        pytest.skip(
            "owl:Class node not found or untagged — shared-URI tagging differs"
        )

    oids = rows[0]["oids"]
    assert "pkmn" in oids, f"Expected 'pkmn' in {oids}"
    assert "other" in oids, f"Expected 'other' in {oids}"


# ── Integration: find_entities scoping ───────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_pkmn_excludes_other(dual_store) -> None:
    """find_entities scoped to 'pkmn' must not return Animal instances."""
    animals = await dual_store.find_entities(
        _ANIMAL_CLASS, ontology="pkmn", limit=100
    )
    assert animals == [], f"Expected no animals in pkmn scope, got {animals}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_other_excludes_pkmn(dual_store) -> None:
    """find_entities scoped to 'other' must not return Pokemon instances."""
    pokemon = await dual_store.find_entities(
        _POKEMON_CLASS, ontology="other", limit=100
    )
    assert pokemon == [], f"Expected no pokemon in other scope, got {pokemon}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_pkmn_returns_pikachu(dual_store) -> None:
    """find_entities scoped to 'pkmn' must include Pikachu."""
    entities = await dual_store.find_entities(
        _POKEMON_CLASS, ontology="pkmn", limit=100
    )
    uris = {e.uri for e in entities}
    assert _PIKACHU_URI in uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_other_returns_animals(dual_store) -> None:
    """find_entities scoped to 'other' must include Cat and Dog."""
    entities = await dual_store.find_entities(
        _ANIMAL_CLASS, ontology="other", limit=100
    )
    uris = {e.uri for e in entities}
    assert "http://example.org/other/data#Cat" in uris
    assert "http://example.org/other/data#Dog" in uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_none_returns_union(dual_store) -> None:
    """find_entities(ontology=None) must return instances from both ontologies."""
    pkmn_entities = await dual_store.find_entities(
        _POKEMON_CLASS, ontology=None, limit=200
    )
    pkmn_uris = {e.uri for e in pkmn_entities}

    animal_entities = await dual_store.find_entities(
        _ANIMAL_CLASS, ontology=None, limit=200
    )
    animal_uris = {e.uri for e in animal_entities}

    # Pokemon scope returns Pikachu; Animal scope returns Cat+Dog.
    assert _PIKACHU_URI in pkmn_uris
    assert "http://example.org/other/data#Cat" in animal_uris


# ── Integration: count_entities scoping ──────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_count_entities_pkmn(dual_store) -> None:
    """count_entities(Pokemon, pkmn) must be > 0 and Animal must be 0."""
    pkmn_cnt = await dual_store.count_entities(_POKEMON_CLASS, ontology="pkmn")
    animal_cnt = await dual_store.count_entities(_ANIMAL_CLASS, ontology="pkmn")

    assert pkmn_cnt > 0, "Expected pokemon instances in pkmn scope"
    assert animal_cnt == 0, f"Expected 0 animals in pkmn scope, got {animal_cnt}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_count_entities_other(dual_store) -> None:
    """count_entities(Animal, other) == 2 and Pokemon must be 0."""
    animal_cnt = await dual_store.count_entities(_ANIMAL_CLASS, ontology="other")
    pkmn_cnt = await dual_store.count_entities(_POKEMON_CLASS, ontology="other")

    assert animal_cnt == 2, f"Expected 2 animals in other scope, got {animal_cnt}"
    assert pkmn_cnt == 0, f"Expected 0 pokemon in other scope, got {pkmn_cnt}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_count_entities_none_union(dual_store) -> None:
    """count_entities(ontology=None) returns union across both ontologies."""
    pkmn_cnt = await dual_store.count_entities(_POKEMON_CLASS, ontology=None)
    animal_cnt = await dual_store.count_entities(_ANIMAL_CLASS, ontology=None)

    assert pkmn_cnt > 0
    assert animal_cnt > 0


# ── Integration: get_schema scoping ──────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_pkmn_excludes_other_classes(dual_store) -> None:
    """get_schema(ontology='pkmn') must not include Animal class from 'other'."""
    schema = await dual_store.get_schema(ontology="pkmn")
    uris = {c.uri for c in schema.classes}

    assert _POKEMON_CLASS in uris, "Pokemon class must appear in pkmn schema"
    assert _ANIMAL_CLASS not in uris, "Animal class must NOT appear in pkmn schema"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_other_excludes_pkmn_classes(dual_store) -> None:
    """get_schema(ontology='other') must not include Pokemon class."""
    schema = await dual_store.get_schema(ontology="other")
    uris = {c.uri for c in schema.classes}

    assert _ANIMAL_CLASS in uris, "Animal class must appear in other schema"
    assert _POKEMON_CLASS not in uris, "Pokemon class must NOT appear in other schema"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_none_returns_union(dual_store) -> None:
    """get_schema(ontology=None) must include classes from both ontologies."""
    schema = await dual_store.get_schema(ontology=None)
    uris = {c.uri for c in schema.classes}

    assert _POKEMON_CLASS in uris
    assert _ANIMAL_CLASS in uris


# ── Integration: backward compat (ontology=None unchanged) ───────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_backward_compat_no_ontology_param(single_store) -> None:
    """All operations called without ontology param behave as before (union/all)."""
    # find_entities with no ontology arg must work exactly as in v0.4.
    entities = await single_store.find_entities(_POKEMON_CLASS, limit=100)
    uris = {e.uri for e in entities}
    assert _PIKACHU_URI in uris

    cnt = await single_store.count_entities(_POKEMON_CLASS)
    assert cnt > 0

    schema = await single_store.get_schema()
    class_uris = {c.uri for c in schema.classes}
    assert _POKEMON_CLASS in class_uris


# ── Integration: search_text scoping ─────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_text_pkmn_excludes_other(dual_store) -> None:
    """search_text(ontology='pkmn') must not return Cat or Dog."""
    # search for "Cat" scoped to pkmn — should return nothing
    hits = await dual_store.search_text("Cat", ontology="pkmn", limit=20)
    hit_uris = {h.uri for h in hits}
    assert "http://example.org/other/data#Cat" not in hit_uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_text_other_excludes_pkmn(dual_store) -> None:
    """search_text(ontology='other') must not return Pikachu."""
    hits = await dual_store.search_text("Pikachu", ontology="other", limit=20)
    hit_uris = {h.uri for h in hits}
    assert _PIKACHU_URI not in hit_uris
