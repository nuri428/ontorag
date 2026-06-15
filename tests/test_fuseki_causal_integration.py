"""Live Fuseki integration tests for the CausalStore capability (v0.8.0).

Skipped when no Fuseki is reachable at FUSEKI_URL. Start: docker compose up -d fuseki
"""

from __future__ import annotations

import os

import httpx
import pytest

from ontorag.core.causal import CausalModel, CausalVariable, causal_graph_uri
from ontorag.stores.fuseki import FusekiStore

pytestmark = pytest.mark.integration

_FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030")
SM = "https://ontorag.dev/smoking#"


def _reachable() -> bool:
    try:
        return httpx.get(f"{_FUSEKI_URL}/$/ping", timeout=2.0).status_code == 200
    except (httpx.HTTPError, OSError):
        return False


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


@pytest.fixture
async def store():
    if not _reachable():
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    s = FusekiStore.from_env()
    await s.clear_causal_model()
    yield s
    await s.clear_causal_model()
    await s.aclose()


@pytest.mark.asyncio
async def test_put_then_get_round_trips(store):
    m = _model()
    assert await store.put_causal_model(m) > 0
    restored = await store.get_causal_model()
    assert restored is not None
    assert restored.name == m.name
    assert restored.based_on == m.based_on
    assert sorted(restored.edges) == sorted(m.edges)
    geno = next(v for v in restored.variables if v.uri == SM + "Genotype")
    assert geno.observed is False


@pytest.mark.asyncio
async def test_get_empty_returns_none(store):
    assert await store.get_causal_model() is None


@pytest.mark.asyncio
async def test_clear_removes_model(store):
    await store.put_causal_model(_model())
    assert await store.clear_causal_model() > 0
    assert await store.get_causal_model() is None


@pytest.mark.asyncio
async def test_causal_isolated_from_other_graphs(store):
    assert causal_graph_uri(None) == "urn:ontorag:causal"
    # Capture counts BEFORE to avoid failing on leftover data from other tests.
    n_schema_before = await store._count_graph("urn:ontorag:schema")
    n_data_before = await store._count_graph("urn:ontorag:data")
    n_prob_before = await store._count_graph("urn:ontorag:probabilistic")

    await store.put_causal_model(_model())
    assert await store._count_graph("urn:ontorag:causal") > 0
    assert await store._count_graph("urn:ontorag:schema") == n_schema_before
    assert await store._count_graph("urn:ontorag:data") == n_data_before
    assert await store._count_graph("urn:ontorag:probabilistic") == n_prob_before
