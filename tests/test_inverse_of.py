"""Tests for owl:inverseOf surfacing in describe_entity.

Covers both the Fuseki and Neo4j backends.

Unit tests (no backend required):
  - Fuseki: mock-based, exercises the SPARQL query construction and merge logic.

Integration tests (pytestmark skipif guards):
  - Fuseki: loads inverse_of_{schema,data}.ttl, asserts inverse surface.
  - Neo4j: same fixture, same assertions via Cypher/n10s path.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ontorag.stores.fuseki import FusekiStore

# ── Constants ─────────────────────────────────────────────────────────────────

_EX = "http://example.org/inv#"
_DATA = "http://example.org/inv/data#"

_ALICE = f"{_DATA}Alice"
_BOB = f"{_DATA}Bob"
_CAROL = f"{_DATA}Carol"
_DAVE = f"{_DATA}Dave"

_PARENT_OF = f"{_EX}parentOf"
_CHILD_OF = f"{_EX}childOf"
_KNOWS = f"{_EX}knows"

_FIXTURES = Path(__file__).parent / "fixtures"
_SCHEMA_TTL = str(_FIXTURES / "inverse_of_schema.ttl")
_DATA_TTL = str(_FIXTURES / "inverse_of_data.ttl")

_FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030")
_NEO4J_URI = "bolt://localhost:7687"
_NEO4J_USER = "neo4j"
_NEO4J_PASSWORD = "ontorag123"

# ── Connectivity guards ───────────────────────────────────────────────────────


def _fuseki_reachable() -> bool:
    try:
        import httpx

        resp = httpx.get(f"{_FUSEKI_URL}/$/ping", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def _neo4j_reachable() -> bool:
    try:
        host, port_str = _NEO4J_URI.replace("bolt://", "").split(":")
        with socket.create_connection((host, int(port_str)), timeout=2):
            return True
    except Exception:
        return False


_FUSEKI_UP = _fuseki_reachable()
_NEO4J_UP = _neo4j_reachable()

# ── Unit tests (Fuseki mock) ──────────────────────────────────────────────────

# Simulate the two SPARQL round-trips inside describe_entity:
#   1st call → outgoing properties (existence + label + class)
#   2nd call → inverse-of query

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


def _props_referencing(properties: dict, target_uri: str) -> list[str]:
    """Return property keys whose value (scalar or list) references *target_uri*.

    Args:
        properties: An EntityResult.properties dict.
        target_uri: The URI to search for in property values.

    Returns:
        The list of property keys that reference the URI (empty if none).
    """
    hits: list[str] = []
    for key, val in properties.items():
        items = val if isinstance(val, list) else [val]
        if any(isinstance(x, dict) and x.get("uri") == target_uri for x in items):
            hits.append(key)
    return hits


def _outgoing_bindings(uri: str) -> dict:
    """Fake SPARQL result for outgoing query on *uri* (Bob node)."""
    return {
        "results": {
            "bindings": [
                {
                    "pred": {"type": "uri", "value": _RDF_TYPE},
                    "obj": {"type": "uri", "value": f"{_EX}Person"},
                    "label": {"type": "literal", "value": "Bob"},
                },
            ]
        }
    }


def _inverse_bindings_with_result() -> dict:
    """Fake SPARQL inverse-of result: Alice --parentOf--> Bob → childOf → Alice."""
    return {
        "results": {
            "bindings": [
                {
                    "invPred": {"type": "uri", "value": _CHILD_OF},
                    "other": {"type": "uri", "value": _ALICE},
                    "otherLabel": {"type": "literal", "value": "Alice"},
                }
            ]
        }
    }


def _empty_bindings() -> dict:
    return {"results": {"bindings": []}}


@pytest.mark.asyncio
async def test_fuseki_unit_inverse_appears_in_properties():
    """Mock unit: Bob.childOf == Alice after inverse pass."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                _outgoing_bindings(_BOB),  # 1st call: outgoing query
                _inverse_bindings_with_result(),  # 2nd call: inverse query
            ]
        ),
    ):
        entity = await store.describe_entity(_BOB)

    assert _CHILD_OF in entity.properties, "childOf must appear via inverse pass"
    inv_val = entity.properties[_CHILD_OF]
    # Scalar or list — normalize to list for assertion
    inv_list = inv_val if isinstance(inv_val, list) else [inv_val]
    alice_uris = [v["uri"] for v in inv_list if isinstance(v, dict)]
    assert _ALICE in alice_uris


@pytest.mark.asyncio
async def test_fuseki_unit_no_inverse_when_empty():
    """Mock unit: no inverse results → no extra properties added."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                _outgoing_bindings(_BOB),
                _empty_bindings(),  # no inverse declarations
            ]
        ),
    ):
        entity = await store.describe_entity(_BOB)

    assert _CHILD_OF not in entity.properties
    assert _PARENT_OF not in entity.properties


@pytest.mark.asyncio
async def test_fuseki_unit_keyerror_preserved():
    """Mock unit: KeyError still raised when the entity does not exist."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(return_value=_empty_bindings()),
    ):
        with pytest.raises(KeyError):
            await store.describe_entity("http://example.org/ghost")


@pytest.mark.asyncio
async def test_fuseki_unit_predicates_filter_applied_to_inverse():
    """Mock unit: predicates filter restricts which inverse predicates surface."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    # The inverse query result returns childOf, but the caller only requests knows.
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                _outgoing_bindings(_BOB),
                _inverse_bindings_with_result(),  # childOf returned by SPARQL
            ]
        ),
    ):
        # Request only _KNOWS — childOf must NOT appear
        entity = await store.describe_entity(_BOB, predicates=[_KNOWS])

    assert _CHILD_OF not in entity.properties


@pytest.mark.asyncio
async def test_fuseki_unit_rdf_type_inverse_skipped_client_side():
    """Mock unit: a leaked rdf:type inverse binding is skipped client-side.

    The SPARQL FILTER(?invPred != rdf:type) excludes rdf:type server-side, but
    a mock bypasses that. The client-side guard must still drop it so a
    contrived `rdf:type owl:inverseOf X` cannot surface a typed neighbor — this
    test fails if that guard regresses.
    """
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    rdf_type_inverse = {
        "results": {
            "bindings": [
                {
                    "invPred": {"type": "uri", "value": _RDF_TYPE},
                    "other": {"type": "uri", "value": _ALICE},
                    "otherLabel": {"type": "literal", "value": "Alice"},
                }
            ]
        }
    }
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                _outgoing_bindings(_BOB),
                rdf_type_inverse,  # leaked rdf:type inverse — must be skipped
            ]
        ),
    ):
        entity = await store.describe_entity(_BOB)

    assert _RDF_TYPE not in entity.properties, (
        "rdf:type must never be surfaced as an inverse predicate"
    )
    # Alice must not leak in via the rdf:type binding either.
    assert _ALICE not in _props_referencing(entity.properties, _ALICE)
    assert not _props_referencing(entity.properties, _ALICE)


@pytest.mark.asyncio
async def test_fuseki_unit_non_inverse_predicate_not_surfaced():
    """Mock unit: a 'knows' inverse binding (no real inverse) restricted by filter.

    Simulates the inverse query leaking a knows-keyed binding while the caller
    only requested parentOf. The client-side predicates guard must drop the
    knows row — catching a regression where the filter is removed.
    """
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    knows_inverse = {
        "results": {
            "bindings": [
                {
                    "invPred": {"type": "uri", "value": _KNOWS},
                    "other": {"type": "uri", "value": _CAROL},
                    "otherLabel": {"type": "literal", "value": "Carol"},
                }
            ]
        }
    }
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                _outgoing_bindings(_BOB),
                knows_inverse,
            ]
        ),
    ):
        entity = await store.describe_entity(_BOB, predicates=[_PARENT_OF])

    assert _KNOWS not in entity.properties
    assert not _props_referencing(entity.properties, _CAROL)


@pytest.mark.asyncio
async def test_fuseki_unit_outgoing_unaffected():
    """Mock unit: outgoing parentOf on Alice is unchanged after inverse pass."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    alice_outgoing = {
        "results": {
            "bindings": [
                {
                    "pred": {"type": "uri", "value": _RDF_TYPE},
                    "obj": {"type": "uri", "value": f"{_EX}Person"},
                    "label": {"type": "literal", "value": "Alice"},
                },
                {
                    "pred": {"type": "uri", "value": _PARENT_OF},
                    "obj": {"type": "uri", "value": _BOB},
                    "label": {"type": "literal", "value": "Alice"},
                    "objLabel": {"type": "literal", "value": "Bob"},
                },
            ]
        }
    }

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                alice_outgoing,
                _empty_bindings(),  # Alice has no incoming inverse edges
            ]
        ),
    ):
        entity = await store.describe_entity(_ALICE)

    assert _PARENT_OF in entity.properties
    assert entity.label == "Alice"


# ── Integration tests (Fuseki) ────────────────────────────────────────────────

pytestmark_fuseki = pytest.mark.skipif(
    not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}"
)


@pytest.fixture()
async def fuseki_store():
    """Live Fuseki store loaded with the inverseOf fixture; skips if Fuseki is down."""
    if not _FUSEKI_UP:
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    store = FusekiStore.from_env()
    await store.clear_graph("all")
    await store.load_rdf(_SCHEMA_TTL, mode="schema")
    await store.load_rdf(_DATA_TTL, mode="data")
    yield store
    await store.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_inverse_surfaces(fuseki_store):
    """Live Fuseki: describe_entity(Bob) must include childOf → Alice."""
    entity = await fuseki_store.describe_entity(_BOB)

    assert _CHILD_OF in entity.properties, (
        f"Expected childOf in Bob's properties; got: {list(entity.properties.keys())}"
    )
    inv_val = entity.properties[_CHILD_OF]
    inv_list = inv_val if isinstance(inv_val, list) else [inv_val]
    alice_uris = [v["uri"] for v in inv_list if isinstance(v, dict)]
    assert _ALICE in alice_uris, f"Expected Alice in childOf values; got: {alice_uris}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_outgoing_unaffected(fuseki_store):
    """Live Fuseki: describe_entity(Alice) keeps parentOf → Bob (outgoing unchanged)."""
    entity = await fuseki_store.describe_entity(_ALICE)

    assert _PARENT_OF in entity.properties, "parentOf must be present as outgoing"
    val = entity.properties[_PARENT_OF]
    val_list = val if isinstance(val, list) else [val]
    bob_uris = [v["uri"] for v in val_list if isinstance(v, dict)]
    assert _BOB in bob_uris


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_no_inverse_for_knows(fuseki_store):
    """Live Fuseki: knows has no inverseOf — Carol must NOT leak into Dave's props.

    Carol --knows--> Dave is an incoming edge on Dave whose predicate (ex:knows)
    has NO owl:inverseOf declaration, so the inverse pass must surface nothing.
    The assertion fails if any property value references Carol.
    """
    entity = await fuseki_store.describe_entity(_DAVE)
    props_with_carol = _props_referencing(entity.properties, _CAROL)
    assert not props_with_carol, (
        f"Carol must NOT appear in Dave's properties via inverse pass; found: {props_with_carol}"
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_ontology_scoped(fuseki_store):
    """Live Fuseki: inverse surfacing works when ontology= is specified."""
    # Reload with explicit ontology scope
    await fuseki_store.clear_graph("all")
    await fuseki_store.load_rdf(_SCHEMA_TTL, mode="schema", ontology="invtest")
    await fuseki_store.load_rdf(_DATA_TTL, mode="data", ontology="invtest")

    entity = await fuseki_store.describe_entity(_BOB, ontology="invtest")
    assert _CHILD_OF in entity.properties, (
        "childOf must surface via inverse pass even under ontology scope"
    )


# ── Integration tests (Neo4j) ─────────────────────────────────────────────────

# Module-level skipif so ALL Neo4j tests below are skipped at collection time
# when Neo4j is not reachable.
pytestmark_neo4j = pytest.mark.skipif(
    not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687"
)


@pytest.fixture()
async def neo4j_store():
    """Live Neo4j store loaded with the inverseOf fixture; skips if Neo4j is down.

    Cross-test isolation note
    ─────────────────────────
    n10s stores namespace-prefix mappings in a global ``_NsPrefDef`` node
    (shared across the whole database).  When different test files load
    ontologies that use the same prefix letter (e.g. ``ex``) for different
    namespaces, n10s silently ignores subsequent ``nsprefixes.add`` calls for
    an already-registered prefix (it raises an "already exists" error, which
    ``_register_prefixes`` swallows).  The stale prefix then causes
    ``_shorten`` / ``_expand`` to produce wrong URIs — making property lookups
    fail intermittently depending on which test ran first.

    Fix: delete all ``_NsPrefDef`` nodes **before** calling ``clear_graph``
    and ``load_rdf``, so every fixture run re-registers its own prefixes from
    scratch against a clean n10s namespace table.  The ``_prefix_map_loaded``
    flag on the new store instance is already ``False``, so the first
    ``_ensure_prefix_map`` call after ``load_rdf`` will read the freshly
    populated ``_NsPrefDef`` rows.
    """
    if not _NEO4J_UP:
        pytest.skip("Neo4j not reachable at bolt://localhost:7687")
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    store = Neo4jStore(uri=_NEO4J_URI, user=_NEO4J_USER, password=_NEO4J_PASSWORD)
    # Reset n10s global prefix state BEFORE clearing graph data so that
    # _register_prefixes can register the correct URIs for this fixture.
    await store._run_write("MATCH (p:_NsPrefDef) DETACH DELETE p")
    await store.clear_graph("all")
    await store.load_rdf(_SCHEMA_TTL, mode="schema")
    await store.load_rdf(_DATA_TTL, mode="data")
    yield store
    await store.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_inverse_surfaces(neo4j_store):
    """Live Neo4j: describe_entity(Bob) must include childOf → Alice."""
    entity = await neo4j_store.describe_entity(_BOB)

    assert _CHILD_OF in entity.properties, (
        f"Expected childOf in Bob's properties; got: {list(entity.properties.keys())}"
    )
    inv_val = entity.properties[_CHILD_OF]
    inv_list = inv_val if isinstance(inv_val, list) else [inv_val]
    alice_uris = [v["uri"] for v in inv_list if isinstance(v, dict)]
    assert _ALICE in alice_uris, f"Expected Alice in childOf values; got: {alice_uris}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_outgoing_unaffected(neo4j_store):
    """Live Neo4j: describe_entity(Alice) keeps parentOf → Bob (outgoing unchanged)."""
    entity = await neo4j_store.describe_entity(_ALICE)

    assert _PARENT_OF in entity.properties, "parentOf must be present as outgoing"
    val = entity.properties[_PARENT_OF]
    val_list = val if isinstance(val, list) else [val]
    bob_uris = [v["uri"] for v in val_list if isinstance(v, dict)]
    assert _BOB in bob_uris


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_no_inverse_for_knows(neo4j_store):
    """Live Neo4j: knows has no inverseOf — Carol must NOT appear in Dave's props."""
    entity = await neo4j_store.describe_entity(_DAVE)
    props_with_carol = _props_referencing(entity.properties, _CAROL)
    assert not props_with_carol, (
        f"Carol must NOT appear in Dave's properties; found: {props_with_carol}"
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_ontology_scoped(neo4j_store):
    """Live Neo4j: inverse surfacing works when ontology= is specified."""
    await neo4j_store.clear_graph("all")
    await neo4j_store.load_rdf(_SCHEMA_TTL, mode="schema", ontology="invtest")
    await neo4j_store.load_rdf(_DATA_TTL, mode="data", ontology="invtest")

    entity = await neo4j_store.describe_entity(_BOB, ontology="invtest")
    assert _CHILD_OF in entity.properties, (
        "childOf must surface via inverse pass even under ontology scope"
    )
