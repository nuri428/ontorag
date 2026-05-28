"""Live Neo4j integration tests for the CausalStore capability (v0.8.0).

Require Neo4j at bolt://localhost:7687. Parity with the Fuseki causal backend.
"""

from __future__ import annotations

import socket

import pytest
import pytest_asyncio

from ontorag.core.causal import CausalModel, CausalVariable

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ontorag123"
SM = "https://ontorag.dev/smoking#"


def _reachable() -> bool:
    try:
        host, port = NEO4J_URI.replace("bolt://", "").split(":")
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(), reason="Neo4j not reachable at bolt://localhost:7687"
)


def _model() -> CausalModel:
    return CausalModel(
        name="smoking-cancer",
        based_on="https://ontorag.dev/bn#network",
        variables=[
            CausalVariable(uri=SM + "Genotype", observed=False),
            CausalVariable(uri=SM + "Smoking"),
            CausalVariable(uri=SM + "Tar"),
            CausalVariable(uri=SM + "Cancer"),
        ],
        edges=[
            (SM + "Genotype", SM + "Smoking"),
            (SM + "Genotype", SM + "Cancer"),
            (SM + "Smoking", SM + "Tar"),
            (SM + "Tar", SM + "Cancer"),
        ],
    )


@pytest_asyncio.fixture()
async def store():
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    s = Neo4jStore(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    await s.clear_causal_model()
    yield s
    await s.clear_causal_model()
    await s.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_then_get_round_trips(store):
    m = _model()
    assert await store.put_causal_model(m) == 1 + len(m.variables) + len(m.edges)
    restored = await store.get_causal_model()
    assert restored is not None
    assert restored.name == m.name
    assert restored.based_on == m.based_on
    assert sorted(restored.edges) == sorted(m.edges)
    geno = next(v for v in restored.variables if v.uri == SM + "Genotype")
    assert geno.observed is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_empty_returns_none(store):
    assert await store.get_causal_model() is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clear_removes_model(store):
    await store.put_causal_model(_model())
    assert await store.clear_causal_model() > 0
    assert await store.get_causal_model() is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_causal_nodes_isolated_from_resource_graph(store):
    await store.put_causal_model(_model())
    rows = await store._run(
        "MATCH (n:_CausalVariable) WHERE n:Resource RETURN count(n) AS c"
    )
    assert rows[0]["c"] == 0
