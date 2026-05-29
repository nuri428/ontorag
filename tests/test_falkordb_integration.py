"""Live integration tests for FalkorDBStore (v0.9).

Skipped unless a FalkorDB server is reachable at FALKORDB_HOST:FALKORDB_PORT
(default localhost:6379). Run a server with:

    docker run -d -p 6379:6379 falkordb/falkordb:latest

or point the suite at another port:

    FALKORDB_PORT=6380 uv run --extra bayes --extra falkordb pytest \
        tests/test_falkordb_integration.py -m integration

Mirrors the Neo4j integration suite: each test gets a fresh, isolated graph
(unique graph name) so runs never collide. Exercises the full protocol +
capabilities — the v0.9 parity bar.
"""

from __future__ import annotations

import os
import socket
import uuid

import pytest

_HOST = os.environ.get("FALKORDB_HOST", "localhost")
_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))


def _reachable() -> bool:
    try:
        with socket.create_connection((_HOST, _PORT), timeout=2):
            return True
    except Exception:
        return False


_FALKORDB_REACHABLE = _reachable()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _FALKORDB_REACHABLE,
        reason=f"FalkorDB not reachable at {_HOST}:{_PORT}",
    ),
]

_SCHEMA = "examples/pokemon/schema.ttl"
_DATA = "examples/pokemon/data.ttl"


@pytest.fixture
async def store():
    """Fresh FalkorDBStore on a unique graph; dropped on teardown."""
    from ontorag.stores.falkordb import FalkorDBStore

    name = f"test_{uuid.uuid4().hex[:8]}"
    s = FalkorDBStore(host=_HOST, port=_PORT, graph_name=name)
    try:
        yield s
    finally:
        try:
            await s._graph.delete()
        except Exception:
            pass
        await s.aclose()


async def _load(s) -> None:
    await s.load_rdf(_SCHEMA, mode="schema")
    await s.load_rdf(_DATA, mode="data")


def _pokemon_uri(schema) -> str:
    return next(c.uri for c in schema.classes if c.uri.endswith("#Pokemon"))


async def test_load_and_status(store):
    await _load(store)
    st = await store.status()
    assert st.connected
    assert st.schema_loaded
    assert st.data_loaded
    assert st.store_type == "falkordb"
    assert st.triple_count and st.triple_count > 0


async def test_get_schema(store):
    await _load(store)
    schema = await store.get_schema()
    names = {c.uri.split("#")[-1] for c in schema.classes}
    assert {"Pokemon", "Move", "Type", "Trainer"} <= names
    assert schema.total_properties > 0


async def test_find_entities_subclass_inference(store):
    await _load(store)
    schema = await store.get_schema()
    pk = _pokemon_uri(schema)
    ents = await store.find_entities(pk, limit=50)
    # 13 Pokémon incl. the LegendaryPokemon (subclass) — inference via
    # rdf__type -> rdfs__subClassOf*.
    assert len(ents) == 13
    assert await store.count_entities(pk) == 13


async def test_describe_entity_no_internal_leak(store):
    await _load(store)
    schema = await store.get_schema()
    ents = await store.find_entities(_pokemon_uri(schema), limit=5)
    d = await store.describe_entity(ents[0].uri)
    assert d.uri == ents[0].uri
    # Internal bookkeeping props must not surface as RDF properties.
    assert not any(k.startswith("_") for k in d.properties)


async def test_traverse(store):
    await _load(store)
    schema = await store.get_schema()
    ents = await store.find_entities(_pokemon_uri(schema), limit=50)
    tr = await store.traverse(ents[0].uri, predicate=None, max_depth=2)
    assert len(tr.nodes) > 0


async def test_query_pattern(store):
    await _load(store)
    from ontorag.stores.base import PatternQuery

    schema = await store.get_schema()
    pk = _pokemon_uri(schema)
    q = PatternQuery(select=["?s"], where=[{"s": "?s", "p": "rdf:type", "o": f"<{pk}>"}], limit=20)
    qr = await store.query_pattern(q)
    assert qr.total >= 12  # direct rdf:type matches (subclass not expanded in DSL)


async def test_search_text(store):
    await _load(store)
    hits = await store.search_text("피카츄", limit=5)
    assert hits
    assert any("피카츄" in (h.label or "") for h in hits)


async def test_build_embeddings_and_find_similar(store):
    await _load(store)
    counts = await store.build_embeddings(mode="structural")
    assert counts.get("structural", 0) > 0
    schema = await store.get_schema()
    ents = await store.find_entities(_pokemon_uri(schema), limit=50)
    sim = await store.find_similar(ents[0].uri, top_k=5, mode="structural")
    assert sim  # structurally similar neighbours exist
    assert all(h.uri != ents[0].uri for h in sim)  # self excluded


async def test_bayes_round_trip(store):
    from ontorag.core.bayes import CPD, BayesNetwork, BayesVariable

    bn = BayesNetwork(
        name="t",
        variables=[BayesVariable(uri="u#A", states=["0", "1"])],
        cpds=[CPD(variable="u#A", values=[[0.5], [0.5]])],
    )
    n = await store.put_bayes_network(bn)
    assert n > 0
    got = await store.get_bayes_network()
    assert got is not None and got.name == "t" and len(got.variables) == 1
    await store.clear_bayes_network()
    assert await store.get_bayes_network() is None


async def test_causal_round_trip(store):
    from ontorag.core.causal import CausalModel, CausalVariable

    cm = CausalModel(
        name="c",
        variables=[CausalVariable(uri="u#A"), CausalVariable(uri="u#B")],
        edges=[("u#A", "u#B")],
    )
    n = await store.put_causal_model(cm)
    assert n > 0
    got = await store.get_causal_model()
    assert got is not None and got.name == "c" and len(got.edges) == 1
    await store.clear_causal_model()
    assert await store.get_causal_model() is None


async def test_dump_ttl(store):
    await _load(store)
    ttl = await store.dump_graph("all", "ttl")
    assert ttl and b"pokemon" in ttl.lower()
