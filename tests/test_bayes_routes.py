"""Tests for the Bayesian inference MCP routes (api/routes/tools/bayes.py).

The capability-guard (501) and no-network (404) paths run without pgmpy. The
happy-path inference tests require the [bayes] extra and skip otherwise.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ontorag.api import deps
from ontorag.api.routes.tools import bayes as bayes_mod
from ontorag.core.bayes import BayesNetwork, BayesVariable, CPD

PK = "https://ontorag.dev/pokemon#"
MATCHUP = f"{PK}TypeMatchup"
OUTCOME = f"{PK}Outcome"


def _network() -> BayesNetwork:
    return BayesNetwork(
        variables=[
            BayesVariable(uri=MATCHUP, states=["advantage", "neutral", "disadvantage"]),
            BayesVariable(uri=OUTCOME, states=["win", "lose"]),
        ],
        cpds=[
            CPD(variable=MATCHUP, values=[[0.4], [0.3], [0.3]]),
            CPD(
                variable=OUTCOME,
                evidence=[MATCHUP],
                values=[[0.8, 0.5, 0.2], [0.2, 0.5, 0.8]],
            ),
        ],
    )


class _NoBayesStore:
    """A store without the BayesianStore capability."""


class _BayesStore:
    def __init__(self, network: BayesNetwork | None) -> None:
        self._network = network

    async def get_bayes_network(self, ontology: str | None = None) -> BayesNetwork | None:
        return self._network


def _make_app(store) -> FastAPI:
    app = FastAPI()
    app.include_router(bayes_mod.router)
    app.dependency_overrides[deps.get_store] = lambda: store
    return app


# ── capability + missing-network guards (no pgmpy needed) ─────────────────────


def test_posterior_501_when_backend_lacks_capability():
    client = TestClient(_make_app(_NoBayesStore()))
    resp = client.post("/tools/bayes/posterior", json={"query": [OUTCOME]})
    assert resp.status_code == 501


def test_posterior_404_when_no_network_stored():
    client = TestClient(_make_app(_BayesStore(None)))
    resp = client.post("/tools/bayes/posterior", json={"query": [OUTCOME]})
    assert resp.status_code == 404


def test_mpe_501_when_backend_lacks_capability():
    client = TestClient(_make_app(_NoBayesStore()))
    resp = client.post("/tools/bayes/mpe", json={"evidence": {}})
    assert resp.status_code == 501


def test_mpe_404_when_no_network_stored():
    client = TestClient(_make_app(_BayesStore(None)))
    resp = client.post("/tools/bayes/mpe", json={"evidence": {}})
    assert resp.status_code == 404


def test_posterior_requires_query():
    client = TestClient(_make_app(_BayesStore(_network())))
    resp = client.post("/tools/bayes/posterior", json={"query": []})
    assert resp.status_code == 422  # pydantic min_length


# ── happy path (requires pgmpy) ───────────────────────────────────────────────


def test_posterior_200_matches_hand_computed():
    import pytest

    pytest.importorskip("pgmpy")
    client = TestClient(_make_app(_BayesStore(_network())))
    resp = client.post("/tools/bayes/posterior", json={"query": [OUTCOME]})
    assert resp.status_code == 200
    post = resp.json()["posterior"]
    assert abs(post[OUTCOME]["win"] - 0.53) < 1e-6


def test_mpe_200_returns_assignment():
    import pytest

    pytest.importorskip("pgmpy")
    client = TestClient(_make_app(_BayesStore(_network())))
    resp = client.post("/tools/bayes/mpe", json={"evidence": {OUTCOME: "lose"}})
    assert resp.status_code == 200
    assert resp.json()["assignment"][MATCHUP] == "disadvantage"


def test_posterior_400_on_invalid_state():
    import pytest

    pytest.importorskip("pgmpy")
    client = TestClient(_make_app(_BayesStore(_network())))
    resp = client.post(
        "/tools/bayes/posterior",
        json={"query": [MATCHUP], "evidence": {OUTCOME: "draw"}},
    )
    assert resp.status_code == 400
