"""Integration tests for the Neo4j BayesianStore capability (v0.7.2).

Require a running Neo4j (bolt://localhost:7687). Marked @pytest.mark.integration
and skipped gracefully when the container is unreachable.

Parity: get_bayes_network must return a BayesNetwork identical to what the
Fuseki backend returns for the same input (same models, same semantics).
"""

from __future__ import annotations

import socket

import pytest
import pytest_asyncio

from ontorag.core.bayes import BayesNetwork, BayesVariable, CPD

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "ontorag123"
PK = "https://ontorag.dev/pokemon#"


def _is_neo4j_reachable() -> bool:
    try:
        host, port_str = NEO4J_URI.replace("bolt://", "").split(":")
        with socket.create_connection((host, int(port_str)), timeout=2):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _is_neo4j_reachable(),
    reason="Neo4j not reachable at bolt://localhost:7687",
)


def _network() -> BayesNetwork:
    return BayesNetwork(
        name="Pokemon battle",
        variables=[
            BayesVariable(
                uri=f"{PK}TypeMatchup",
                states=["advantage", "neutral", "disadvantage"],
                represents=f"{PK}hasType",
            ),
            BayesVariable(uri=f"{PK}Outcome", states=["win", "lose"]),
        ],
        cpds=[
            CPD(variable=f"{PK}TypeMatchup", values=[[0.4], [0.3], [0.3]]),
            CPD(
                variable=f"{PK}Outcome",
                evidence=[f"{PK}TypeMatchup"],
                values=[[0.8, 0.5, 0.2], [0.2, 0.5, 0.8]],
            ),
        ],
    )


@pytest_asyncio.fixture()
async def store():
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    s = Neo4jStore(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    await s.clear_bayes_network()
    await s.clear_bayes_network(ontology="pokemon")
    yield s
    await s.clear_bayes_network()
    await s.clear_bayes_network(ontology="pokemon")
    await s.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_then_get_round_trips(store):
    net = _network()
    written = await store.put_bayes_network(net)
    assert written == 1 + len(net.variables) + len(net.cpds)

    restored = await store.get_bayes_network()
    assert restored is not None
    assert restored.name == net.name
    assert {v.uri for v in restored.variables} == {v.uri for v in net.variables}
    outcome = restored.variable(f"{PK}Outcome")
    assert outcome.states == ["win", "lose"]
    cpd = next(c for c in restored.cpds if c.variable == f"{PK}Outcome")
    assert cpd.evidence == [f"{PK}TypeMatchup"]
    assert cpd.values == [[0.8, 0.5, 0.2], [0.2, 0.5, 0.8]]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_empty_returns_none(store):
    assert await store.get_bayes_network() is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clear_removes_network(store):
    await store.put_bayes_network(_network())
    removed = await store.clear_bayes_network()
    assert removed == 1 + len(_network().variables) + len(_network().cpds)
    assert await store.get_bayes_network() is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scoped_network_isolated_from_default(store):
    await store.put_bayes_network(_network(), ontology="pokemon")
    assert await store.get_bayes_network() is None
    scoped = await store.get_bayes_network(ontology="pokemon")
    assert scoped is not None
    assert scoped.variable(f"{PK}Outcome").states == ["win", "lose"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bayes_nodes_isolated_from_resource_graph(store):
    """Network nodes use dedicated labels — never mixed into :Resource."""
    await store.put_bayes_network(_network())
    rows = await store._run(
        "MATCH (n:_BayesVariable) WHERE n:Resource RETURN count(n) AS c"
    )
    assert rows[0]["c"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_inference_from_stored_network(store):
    """store → retrieve → infer against live Neo4j; parity with Fuseki E2E."""
    pytest.importorskip("pgmpy", reason="inference requires the [bayes] extra")
    from ontorag.bayes.engine import BayesianEngine

    await store.put_bayes_network(_network())
    stored = await store.get_bayes_network()
    engine = BayesianEngine(stored)

    post = await engine.compute_posterior({}, [f"{PK}Outcome"])
    assert abs(post[f"{PK}Outcome"]["win"] - 0.53) < 1e-6
    mpe = await engine.mpe({f"{PK}Outcome": "lose"})
    assert mpe[f"{PK}TypeMatchup"] == "disadvantage"
