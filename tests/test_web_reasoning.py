"""Tests for the Reasoning WebUI routes (web/router.py, v0.8.4).

Capability / no-network guards render an amber hint partial and run without
pgmpy. The happy-path posterior / do / counterfactual tests require the [bayes]
extra and skip otherwise. Mirrors the smoking quality bar: see(0.72) != do(0.60).
"""

from __future__ import annotations

import importlib.util

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ontorag.api.deps import get_store
from ontorag.core.bayes import BayesNetwork, BayesVariable, CPD
from ontorag.core.causal import CausalModel, CausalVariable
from ontorag.web import router as web_router

_HAS_PGMPY = importlib.util.find_spec("pgmpy") is not None
_needs_pgmpy = pytest.mark.skipif(not _HAS_PGMPY, reason="requires the [bayes] extra")

SM = "https://ontorag.dev/smoking#"
GENO, SMOKE, CANCER = f"{SM}Genotype", f"{SM}Smoking", f"{SM}Cancer"


def _smoking_bn() -> BayesNetwork:
    return BayesNetwork(
        name="smoking",
        variables=[
            BayesVariable(uri=GENO, states=["g0", "g1"], label="Genotype"),
            BayesVariable(uri=SMOKE, states=["no", "yes"], label="Smoking"),
            BayesVariable(uri=CANCER, states=["no", "yes"], label="Cancer"),
        ],
        cpds=[
            CPD(variable=GENO, values=[[0.5], [0.5]]),
            CPD(variable=SMOKE, evidence=[GENO], values=[[0.8, 0.2], [0.2, 0.8]]),
            CPD(
                variable=CANCER,
                evidence=[SMOKE, GENO],
                values=[[0.9, 0.7, 0.6, 0.2], [0.1, 0.3, 0.4, 0.8]],
            ),
        ],
    )


def _smoking_causal() -> CausalModel:
    return CausalModel(
        name="smoking",
        variables=[
            CausalVariable(uri=GENO, observed=True, label="Genotype"),
            CausalVariable(uri=SMOKE, observed=True, label="Smoking"),
            CausalVariable(uri=CANCER, observed=True, label="Cancer"),
        ],
        edges=[(GENO, SMOKE), (GENO, CANCER), (SMOKE, CANCER)],
    )


class _NoBayesStore:
    """A store without the BayesianStore capability."""


class _BayesStore:
    def __init__(self, bn: BayesNetwork | None, causal: CausalModel | None = None) -> None:
        self._bn = bn
        self._causal = causal

    async def get_bayes_network(self, ontology=None):
        return self._bn

    async def get_causal_model(self, ontology=None):
        return self._causal


def _client(store) -> TestClient:
    app = FastAPI()
    app.include_router(web_router.router)
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


# ── page render ─────────────────────────────────────────────────────────────


def test_reasoning_page_no_backend_hint():
    client = _client(_NoBayesStore())
    r = client.get("/ui/reasoning")
    assert r.status_code == 200
    assert "지원하지 않습니다" in r.text


def test_reasoning_page_no_network_hint():
    client = _client(_BayesStore(None))
    r = client.get("/ui/reasoning")
    assert r.status_code == 200
    assert "네트워크가 없습니다" in r.text


def test_reasoning_page_with_bn_and_causal():
    client = _client(_BayesStore(_smoking_bn(), _smoking_causal()))
    r = client.get("/ui/reasoning")
    assert r.status_code == 200
    assert "subtab-bayes" in r.text
    assert "causal DAG 로드됨" in r.text  # DAG present banner


def test_posterior_empty_query_hint():
    client = _client(_BayesStore(_smoking_bn()))
    r = client.post("/ui/reasoning/posterior", data={})
    assert r.status_code == 200
    assert "질의" in r.text  # amber hint


def test_do_no_intervention_hint():
    client = _client(_BayesStore(_smoking_bn(), _smoking_causal()))
    r = client.post("/ui/reasoning/causal/do", data={"query": [CANCER]})
    assert r.status_code == 200
    assert "개입" in r.text


# ── happy path (needs pgmpy): see != do ─────────────────────────────────────


@_needs_pgmpy
def test_posterior_see_smoking():
    client = _client(_BayesStore(_smoking_bn()))
    r = client.post(
        "/ui/reasoning/posterior",
        data={"query": [CANCER], "evidence": [f"{SMOKE}=yes"]},
    )
    assert r.status_code == 200
    assert "72.0%" in r.text  # P(Cancer=yes | see Smoking=yes)
    assert "관측(see)" in r.text


@_needs_pgmpy
def test_do_smoking_deconfounds():
    client = _client(_BayesStore(_smoking_bn(), _smoking_causal()))
    r = client.post(
        "/ui/reasoning/causal/do",
        data={"query": [CANCER], "do": [f"{SMOKE}=yes"]},
    )
    assert r.status_code == 200
    assert "60.0%" in r.text  # P(Cancer=yes | do Smoking=yes) — back-door adjusted
    assert "개입(do)" in r.text


@_needs_pgmpy
def test_identify_backdoor_genotype():
    client = _client(_BayesStore(_smoking_bn(), _smoking_causal()))
    r = client.post(
        "/ui/reasoning/causal/identify",
        data={"treatment": SMOKE, "outcome": CANCER},
    )
    assert r.status_code == 200
    assert "Genotype" in r.text
    assert "True" in r.text


@_needs_pgmpy
def test_counterfactual_smoking():
    client = _client(_BayesStore(_smoking_bn(), _smoking_causal()))
    r = client.post(
        "/ui/reasoning/causal/counterfactual",
        data={
            "query": [CANCER],
            "intervention": [f"{SMOKE}=no"],
            "observed": [f"{SMOKE}=yes", f"{CANCER}=yes"],
        },
    )
    assert r.status_code == 200
    assert "27.78%" in r.text  # counterfactual P(Cancer=yes | had not smoked)


@_needs_pgmpy
def test_mpe_renders():
    client = _client(_BayesStore(_smoking_bn()))
    r = client.post("/ui/reasoning/mpe", data={"evidence": [f"{CANCER}=yes"]})
    assert r.status_code == 200
    assert "MPE" in r.text
