"""Integration tests for Neo4jStore against the live Neo4j container.

These tests require a running Neo4j instance (bolt://localhost:7687).
They are marked with @pytest.mark.integration and skipped gracefully when
the container is unreachable.

Data is loaded once per module using a module-scoped fixture.  The Neo4j driver
is created fresh per test to avoid event-loop sharing issues across sessions.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from ontorag.stores.base import (
    AggFunc,
    EntityFilter,
    FilterOp,
    PatternQuery,
    PatternTriple,
    TraversalDirection,
)

# ── Constants ─────────────────────────────────────────────────────────────────

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ontorag123"

SCHEMA_TTL = "src/ontorag/_templates/examples/pokemon/schema.ttl"
DATA_TTL = "src/ontorag/_templates/examples/pokemon/data.ttl"

_POKEMON_CLASS = "http://example.org/pokemon#Pokemon"
_LEGENDARY_CLASS = "http://example.org/pokemon#LegendaryPokemon"
_PIKACHU_URI = "http://example.org/pokemon/data#Pikachu"
_TRAINER_ASH = "http://example.org/pokemon/data#TrainerAsh"
_HAS_TYPE = "http://example.org/pokemon#hasType"
_HAS_MOVE = "http://example.org/pokemon#hasMove"
_EVOLVES_FROM = "http://example.org/pokemon#evolvesFrom"
_TRAINER_CLASS = "http://example.org/pokemon#Trainer"


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
    """Function-scoped store fixture that loads schema+data fresh each test.

    Resets the ABox before loading to ensure clean state.
    """
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    s = Neo4jStore(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)

    # Full reset (keep graphconfig + prefixes)
    await s._run_write(
        "MATCH (n:Resource) WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
        "DETACH DELETE n"
    )

    await s.load_rdf(SCHEMA_TTL, mode="schema")
    await s.load_rdf(DATA_TTL, mode="data")

    yield s
    await s.aclose()


# ── Status ────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_connected(store) -> None:
    """status() should report connected=True after data is loaded."""
    status = await store.status()
    assert status.connected is True
    assert status.store_type == "neo4j"
    assert status.schema_loaded is True
    assert status.data_loaded is True
    assert status.triple_count is not None and status.triple_count > 0


# ── get_schema ────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_returns_classes(store) -> None:
    """get_schema should return Pokemon, LegendaryPokemon, Type, etc."""
    schema = await store.get_schema()
    class_uris = {c.uri for c in schema.classes}
    assert _POKEMON_CLASS in class_uris
    assert _LEGENDARY_CLASS in class_uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_returns_properties(store) -> None:
    """get_schema should include object and datatype properties."""
    schema = await store.get_schema()
    prop_uris = {p.uri for p in schema.properties}
    assert _HAS_TYPE in prop_uris
    assert _EVOLVES_FROM in prop_uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_legendary_has_parent(store) -> None:
    """LegendaryPokemon class summary should have Pokemon as parent."""
    schema = await store.get_schema()
    legendary = next((c for c in schema.classes if c.uri == _LEGENDARY_CLASS), None)
    assert legendary is not None
    assert legendary.parent_uri == _POKEMON_CLASS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_schema_namespaces(store) -> None:
    """Namespaces dict should include pk prefix."""
    schema = await store.get_schema()
    assert "pk" in schema.namespaces
    assert schema.namespaces["pk"] == "http://example.org/pokemon#"


# ── get_class_detail ─────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_class_detail_pokemon(store) -> None:
    """get_class_detail for Pokemon should return children and properties."""
    detail = await store.get_class_detail(_POKEMON_CLASS)
    assert detail.uri == _POKEMON_CLASS
    assert _LEGENDARY_CLASS in detail.child_uris
    assert any(p.uri == _HAS_TYPE for p in detail.properties)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_class_detail_not_found(store) -> None:
    """get_class_detail for non-existent URI should raise KeyError."""
    with pytest.raises(KeyError):
        await store.get_class_detail("http://example.org/does-not-exist#Foo")


# ── find_entities ─────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_base_class(store) -> None:
    """find_entities(Pokemon) should return Mewtwo (subclass inference)."""
    entities = await store.find_entities(_POKEMON_CLASS, limit=100)
    uris = {e.uri for e in entities}
    # Mewtwo is a LegendaryPokemon (subclass of Pokemon) — inference must work
    assert "http://example.org/pokemon/data#Mewtwo" in uris
    assert _PIKACHU_URI in uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_legendary_only(store) -> None:
    """find_entities(LegendaryPokemon) should return only Mewtwo."""
    entities = await store.find_entities(_LEGENDARY_CLASS)
    uris = {e.uri for e in entities}
    assert "http://example.org/pokemon/data#Mewtwo" in uris
    assert _PIKACHU_URI not in uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_result_shape(store) -> None:
    """EntityResult should have uri, label, class_uri, and properties."""
    entities = await store.find_entities(_LEGENDARY_CLASS)
    assert len(entities) == 1
    mewtwo = entities[0]
    assert mewtwo.uri == "http://example.org/pokemon/data#Mewtwo"
    assert mewtwo.label is not None
    prop_keys = set(mewtwo.properties.keys())
    # At least one property key should be a full URI
    assert any("pokemon" in k for k in prop_keys)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_entities_with_limit(store) -> None:
    """find_entities with limit=2 should return at most 2 results."""
    entities = await store.find_entities(_POKEMON_CLASS, limit=2)
    assert len(entities) <= 2


# ── describe_entity ───────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_describe_entity_pikachu(store) -> None:
    """describe_entity for Pikachu should return its properties and relationships."""
    entity = await store.describe_entity(_PIKACHU_URI)
    assert entity.uri == _PIKACHU_URI
    assert entity.label is not None
    assert _HAS_TYPE in entity.properties


@pytest.mark.integration
@pytest.mark.asyncio
async def test_describe_entity_not_found(store) -> None:
    """describe_entity for unknown URI should raise KeyError."""
    with pytest.raises(KeyError):
        await store.describe_entity("http://example.org/not-exist#NoOne")


# ── count_entities ────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_count_entities_pokemon(store) -> None:
    """count_entities(Pokemon) should count all including Mewtwo (subclass)."""
    count = await store.count_entities(_POKEMON_CLASS)
    assert count >= 14


@pytest.mark.integration
@pytest.mark.asyncio
async def test_count_entities_legendary(store) -> None:
    """count_entities(LegendaryPokemon) should return 1 (only Mewtwo)."""
    count = await store.count_entities(_LEGENDARY_CLASS)
    assert count == 1


# ── aggregate ─────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_aggregate_pokemon_by_type(store) -> None:
    """aggregate by hasType should group pokemon by their type."""
    results = await store.aggregate(_POKEMON_CLASS, _HAS_TYPE, AggFunc.count)
    assert len(results) > 0
    assert all(r.group_value.startswith("http://") for r in results)
    total = sum(r.result for r in results)
    assert total >= 14


# ── traverse ─────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_traverse_pikachu_outgoing(store) -> None:
    """traverse from Pikachu should find neighbors."""
    result = await store.traverse(
        _PIKACHU_URI, max_depth=1, direction=TraversalDirection.outgoing
    )
    node_uris = {n["uri"] for n in result.nodes}
    assert _PIKACHU_URI in node_uris
    assert len(result.nodes) > 1
    assert len(result.edges) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_traverse_result_has_predicates(store) -> None:
    """Traversal edges should contain full-URI predicates."""
    result = await store.traverse(_PIKACHU_URI, max_depth=1)
    for edge in result.edges:
        assert "predicate" in edge
        assert "://" in edge["predicate"], f"predicate not a full URI: {edge['predicate']}"


# ── find_path ─────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_path_pikachu_to_ash(store) -> None:
    """find_path from Pikachu to TrainerAsh should return a direct path."""
    result = await store.find_path(_PIKACHU_URI, _TRAINER_ASH)
    assert result.start_uri == _PIKACHU_URI
    assert result.end_uri == _TRAINER_ASH
    assert len(result.nodes) >= 2
    assert len(result.edges) >= 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_path_no_path(store) -> None:
    """find_path between unconnected nodes should return empty result."""
    result = await store.find_path(
        "http://example.org/pokemon/data#Snorlax",
        "http://example.org/pokemon/data#TrainerBrock",
        max_depth=2,
    )
    assert result.depth_reached == 0 or len(result.nodes) == 0


# ── property_path_closure ─────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_property_path_closure_mode1(store) -> None:
    """Mode 1: evolvesFrom closure from Venusaur should reach Bulbasaur."""
    results = await store.property_path_closure(
        predicate_uri=_EVOLVES_FROM,
        start_uri="http://example.org/pokemon/data#Venusaur",
    )
    uris = {r["uri"] for r in results}
    assert "http://example.org/pokemon/data#Ivysaur" in uris
    assert "http://example.org/pokemon/data#Bulbasaur" in uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_property_path_closure_mode2(store) -> None:
    """Mode 2: label lookup closure — Pikachu evolves from Pichu."""
    results = await store.property_path_closure(
        predicate_uri=_EVOLVES_FROM,
        start_label="피카츄",
        start_class_uri=_POKEMON_CLASS,
    )
    uris = {r["uri"] for r in results}
    assert "http://example.org/pokemon/data#Pichu" in uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_property_path_closure_mode3(store) -> None:
    """Mode 3: class-wide closure for Pokemon via evolvesFrom."""
    results = await store.property_path_closure(
        predicate_uri=_EVOLVES_FROM,
        start_class_uri=_POKEMON_CLASS,
    )
    assert len(results) > 0


# ── find_related ─────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_related_pokemon_trainer(store) -> None:
    """find_related(Pokemon, trainedBy, Trainer) should return pairs."""
    trained_by = "http://example.org/pokemon#trainedBy"
    results = await store.find_related(
        class_uri_a=_POKEMON_CLASS,
        predicate=trained_by,
        class_uri_b=_TRAINER_CLASS,
    )
    assert len(results) > 0
    for pair in results:
        assert "entity_a" in pair
        assert "entity_b" in pair
        assert pair["entity_a"]["class_uri"] == _POKEMON_CLASS
        assert pair["entity_b"]["class_uri"] == _TRAINER_CLASS


# ── query_pattern ─────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_query_pattern_basic(store) -> None:
    """query_pattern with rdf:type triple should return LegendaryPokemon instances."""
    query = PatternQuery(
        select=["?inst"],
        where=[
            PatternTriple(
                s="?inst",
                p="rdf:type",
                o="<http://example.org/pokemon#LegendaryPokemon>",
            )
        ],
        limit=10,
    )
    result = await store.query_pattern(query)
    assert result.total >= 1
    uris = [row.get("inst") for row in result.rows if row.get("inst")]
    assert any("Mewtwo" in u for u in uris)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_query_pattern_relationship(store) -> None:
    """query_pattern with object-property triple should return type pairs."""
    query = PatternQuery(
        select=["?inst", "?type"],
        where=[
            PatternTriple(
                s="?inst",
                p="<http://example.org/pokemon#hasType>",
                o="?type",
            )
        ],
        limit=50,
    )
    result = await store.query_pattern(query)
    assert result.total > 0
    assert "inst" in result.columns
    assert "type" in result.columns


@pytest.mark.integration
@pytest.mark.asyncio
async def test_query_pattern_multi_triple_literal_filter(store) -> None:
    """Review #4: relationship triple + literal-filter triple on the same
    subject var must combine correctly (one WHERE) and return right results.

    Find electric-type pokemon (hasType TypeElectric) with hp = 35 → Pikachu.
    """
    query = PatternQuery(
        select=["?p"],
        where=[
            PatternTriple(
                s="?p",
                p="<http://example.org/pokemon#hasType>",
                o="<http://example.org/pokemon/data#TypeElectric>",
            ),
            PatternTriple(
                s="?p",
                p="<http://example.org/pokemon#hp>",
                o="35",
            ),
        ],
        limit=20,
    )
    result = await store.query_pattern(query)
    uris = [row.get("p") for row in result.rows if row.get("p")]
    # Pikachu is electric with hp 35; Pichu (hp 20) / Raichu (hp 60) excluded.
    assert _PIKACHU_URI in uris
    assert "http://example.org/pokemon/data#Pichu" not in uris
    assert "http://example.org/pokemon/data#Raichu" not in uris


@pytest.mark.integration
@pytest.mark.asyncio
async def test_query_pattern_concrete_uri_object(store) -> None:
    """Review #5: ?p evolvesFrom <Pikachu> must bind the object and return
    only Raichu, not every pokemon with an evolvesFrom edge."""
    query = PatternQuery(
        select=["?p"],
        where=[
            PatternTriple(
                s="?p",
                p="<http://example.org/pokemon#evolvesFrom>",
                o="<http://example.org/pokemon/data#Pikachu>",
            )
        ],
        limit=20,
    )
    result = await store.query_pattern(query)
    uris = {row.get("p") for row in result.rows if row.get("p")}
    # Only Raichu evolves from Pikachu.
    assert uris == {"http://example.org/pokemon/data#Raichu"}


# ── dump_graph ────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dump_graph_ttl(store) -> None:
    """dump_graph(all, ttl) should return non-empty Turtle bytes."""
    data = await store.dump_graph("all", "ttl")
    assert isinstance(data, bytes)
    assert len(data) > 100
    assert b"http://example.org/pokemon" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dump_graph_ttl_round_trips_via_rdflib(store) -> None:
    """Lower-priority verify: TTL export parses back via rdflib and contains
    a known instance triple (Pikachu rdf:type pk:Pokemon)."""
    from rdflib import RDF, Graph, URIRef  # noqa: PLC0415

    data = await store.dump_graph("all", "ttl")
    g = Graph()
    g.parse(data=data.decode(), format="turtle")
    assert len(g) > 0
    # Pikachu must be typed as a Pokemon in the round-tripped graph.
    assert (
        URIRef(_PIKACHU_URI),
        RDF.type,
        URIRef(_POKEMON_CLASS),
    ) in g
    # A literal property survives (Pikachu's nationalDex = 25).
    dex = list(
        g.objects(URIRef(_PIKACHU_URI), URIRef("http://example.org/pokemon#nationalDex"))
    )
    assert dex and str(dex[0]) == "25"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dump_graph_json(store) -> None:
    """dump_graph(all, json) should return parseable JSON with s/p/o keys."""
    import json as _json

    data = await store.dump_graph("all", "json")
    triples = _json.loads(data)
    assert isinstance(triples, list)
    assert len(triples) > 0
    assert "s" in triples[0] and "p" in triples[0] and "o" in triples[0]


# ── clear_graph ───────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clear_and_reload(store) -> None:
    """clear_graph(data) then load_rdf should restore Pokemon count."""
    count_before = await store.count_entities(_POKEMON_CLASS)
    assert count_before > 0

    removed = await store.clear_graph("data")
    assert "data" in removed

    count_after = await store.count_entities(_POKEMON_CLASS)
    assert count_after == 0

    await store.load_rdf(DATA_TTL, mode="data")
    count_restored = await store.count_entities(_POKEMON_CLASS)
    assert count_restored == count_before


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clear_data_preserves_internal_n10s_nodes(store) -> None:
    """Review #8: clear_graph('data') must NOT delete _NsPrefDef/_GraphConfig.

    With correct OR/AND precedence the guards apply to the whole WHERE, so
    n10s internal nodes and the prefix mapping survive a data clear.
    """
    pref_before = await store._run("MATCH (p:_NsPrefDef) RETURN count(p) AS c")
    cfg_before = await store._run("MATCH (g:_GraphConfig) RETURN count(g) AS c")
    assert pref_before[0]["c"] >= 1
    assert cfg_before[0]["c"] >= 1

    await store.clear_graph("data")

    pref_after = await store._run("MATCH (p:_NsPrefDef) RETURN count(p) AS c")
    cfg_after = await store._run("MATCH (g:_GraphConfig) RETURN count(g) AS c")
    assert pref_after[0]["c"] == pref_before[0]["c"]
    assert cfg_after[0]["c"] == cfg_before[0]["c"]

    # Prefix mapping still resolvable after the clear.
    await store._reload_prefix_map()
    assert store._prefix_to_ns.get("pk") == "http://example.org/pokemon#"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_class_detail_leaf_class_not_raised(store) -> None:
    """Review #9: a real class node with no children/props/instances must be
    returned, not raised as KeyError."""
    leaf = "http://example.org/leaf#Orphan"
    await store._run_write(
        "MERGE (c:Resource {uri: $uri}) SET c:owl__Class",
        uri=leaf,
    )
    try:
        detail = await store.get_class_detail(leaf)
        assert detail.uri == leaf
        assert detail.child_uris == []
        assert detail.properties == []
        assert detail.instance_count == 0
    finally:
        await store._run_write(
            "MATCH (c:Resource {uri: $uri}) DETACH DELETE c", uri=leaf
        )


# ── Injection / safety regression tests (review CRITICAL #1, #2, HIGH #3) ──────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_property_path_closure_injection_does_not_delete(store) -> None:
    """Review #1: a malicious start_class_uri must NOT delete data.

    Mode 1 previously f-string-interpolated start_class_uri into Cypher.
    With it parameterized, the payload is treated as a literal URI (matching
    nothing) and all data survives.
    """
    count_before = await store.count_entities(_POKEMON_CLASS)
    assert count_before > 0

    payload = "') DETACH DELETE (n //"
    # Should not raise from injected Cypher and must not delete anything.
    results = await store.property_path_closure(
        predicate_uri=_EVOLVES_FROM,
        start_uri="http://example.org/pokemon/data#Venusaur",
        start_class_uri=payload,
    )
    # Payload matches no class, so the type filter yields no start node → empty.
    assert results == []

    count_after = await store.count_entities(_POKEMON_CLASS)
    assert count_after == count_before  # data fully intact


@pytest.mark.integration
@pytest.mark.asyncio
async def test_aggregate_with_real_data_still_works_after_safety(store) -> None:
    """Sanity: _safe_rel validation does not break legitimate predicates."""
    results = await store.aggregate(_POKEMON_CLASS, _HAS_TYPE, AggFunc.count)
    assert len(results) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_subclassof_cycle_terminates(store) -> None:
    """Review #3: a cyclic subClassOf hierarchy (A⊂B⊂A) must terminate.

    Unbounded ``*0..`` on a cycle would loop forever / blow up; the capped
    ``*0..N`` form terminates and returns a finite, correct count.
    """
    a = "http://example.org/cycle#A"
    b = "http://example.org/cycle#B"
    inst = "http://example.org/cycle#inst1"

    # Build A ⊂ B ⊂ A cycle + one instance of A.
    await store._run_write(
        """
        MERGE (a:Resource {uri: $a}) SET a:owl__Class
        MERGE (b:Resource {uri: $b}) SET b:owl__Class
        MERGE (a)-[:rdfs__subClassOf]->(b)
        MERGE (b)-[:rdfs__subClassOf]->(a)
        MERGE (i:Resource {uri: $inst})
        MERGE (i)-[:rdf__type]->(a)
        """,
        a=a,
        b=b,
        inst=inst,
    )
    try:
        # Must return promptly (capped depth) and count the single instance.
        count = await store.count_entities(a)
        assert count == 1
        ents = await store.find_entities(a, limit=10)
        assert any(e.uri == inst for e in ents)
    finally:
        # Clean up the cycle so it doesn't pollute other tests' shared DB.
        await store._run_write(
            "MATCH (n:Resource) WHERE n.uri IN $uris DETACH DELETE n",
            uris=[a, b, inst],
        )
