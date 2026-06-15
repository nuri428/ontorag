"""Live Fuseki integration tests for the BayesianStore capability (v0.7.1).

Skipped automatically when no Fuseki is reachable at FUSEKI_URL
(default http://localhost:3030). Start one with:

    docker compose up -d fuseki
"""

from __future__ import annotations

import os

import httpx
import pytest

from ontorag.core.bayes import BayesNetwork, BayesVariable, CPD, probabilistic_graph_uri
from ontorag.stores.fuseki import FusekiStore

pytestmark = pytest.mark.integration

_FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030")
PK = "https://ontorag.dev/pokemon#"


def _fuseki_reachable() -> bool:
    try:
        resp = httpx.get(f"{_FUSEKI_URL}/$/ping", timeout=2.0)
        return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


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


@pytest.fixture
async def store():
    if not _fuseki_reachable():
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    s = FusekiStore.from_env()
    await s.clear_bayes_network()
    yield s
    await s.clear_bayes_network()
    await s.aclose()


@pytest.mark.asyncio
async def test_put_then_get_round_trips(store):
    net = _network()
    written = await store.put_bayes_network(net)
    assert written > 0

    restored = await store.get_bayes_network()
    assert restored is not None
    assert restored.name == net.name
    assert {v.uri for v in restored.variables} == {v.uri for v in net.variables}
    outcome = restored.variable(f"{PK}Outcome")
    assert outcome.states == ["win", "lose"]
    cpd = next(c for c in restored.cpds if c.variable == f"{PK}Outcome")
    assert cpd.evidence == [f"{PK}TypeMatchup"]
    assert cpd.values == [[0.8, 0.5, 0.2], [0.2, 0.5, 0.8]]


@pytest.mark.asyncio
async def test_get_empty_returns_none(store):
    assert await store.get_bayes_network() is None


@pytest.mark.asyncio
async def test_clear_removes_network(store):
    await store.put_bayes_network(_network())
    removed = await store.clear_bayes_network()
    assert removed > 0
    assert await store.get_bayes_network() is None


@pytest.mark.asyncio
async def test_cpts_isolated_from_schema_and_data_graphs(store):
    """CPTs must live ONLY in the probabilistic graph — never schema/data."""
    prob = probabilistic_graph_uri(None)
    assert prob == "urn:ontorag:probabilistic"
    # Capture counts BEFORE to avoid failing on leftover data from other tests.
    n_schema_before = await store._count_graph("urn:ontorag:schema")
    n_data_before = await store._count_graph("urn:ontorag:data")

    await store.put_bayes_network(_network())
    # The schema and data named graphs must not have gained any new triples.
    n_schema_after = await store._count_graph("urn:ontorag:schema")
    n_data_after = await store._count_graph("urn:ontorag:data")
    n_prob = await store._count_graph(prob)
    assert n_prob > 0
    assert n_schema_after == n_schema_before
    assert n_data_after == n_data_before


@pytest.mark.asyncio
async def test_scoped_network_isolated_from_default(store):
    await store.put_bayes_network(_network(), ontology="pokemon")
    # Default scope is untouched by a scoped write.
    assert await store.get_bayes_network() is None
    scoped = await store.get_bayes_network(ontology="pokemon")
    assert scoped is not None
    await store.clear_bayes_network(ontology="pokemon")


@pytest.mark.asyncio
async def test_end_to_end_inference_from_stored_network(store):
    """store → retrieve → infer: the full chain against a live backend.

    Storage and inference are otherwise tested separately; this locks in that a
    network round-tripped through Fuseki produces the hand-computed posteriors.
    """
    pytest.importorskip("pgmpy", reason="inference requires the [bayes] extra")
    from ontorag.bayes.engine import BayesianEngine

    await store.put_bayes_network(_network())
    stored = await store.get_bayes_network()
    engine = BayesianEngine(stored)

    post = await engine.compute_posterior({}, [f"{PK}Outcome"])
    assert abs(post[f"{PK}Outcome"]["win"] - 0.53) < 1e-6  # .4*.8+.3*.5+.3*.2
    mpe = await engine.mpe({f"{PK}Outcome": "lose"})
    assert mpe[f"{PK}TypeMatchup"] == "disadvantage"
