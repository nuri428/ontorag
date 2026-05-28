"""v0.8.3 PC structure discovery — proposes a DAG from observational data.

Requires the [bayes] extra (pgmpy + pandas); skipped otherwise. PC recovers a
Markov-equivalence class, so we assert on the undirected skeleton (which edges
exist), not exact orientation.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pgmpy", reason="structure discovery requires the [bayes] extra")
pytest.importorskip("pandas")

from ontorag.bayes.engine import BayesianEngineError
from ontorag.causal.discovery import discover_dag
from ontorag.core.bayes import BayesVariable, StructureSpec
from ontorag.stores.base import EntityResult

SM = "https://ontorag.dev/chain#"
A, B, C = SM + "A", SM + "B", SM + "C"
AP, BP, CP = SM + "aProp", SM + "bProp", SM + "cProp"
TARGET = SM + "Sample"


def _structure() -> StructureSpec:
    return StructureSpec(
        variables=[
            BayesVariable(uri=A, states=["0", "1"], represents=AP),
            BayesVariable(uri=B, states=["0", "1"], represents=BP),
            BayesVariable(uri=C, states=["0", "1"], represents=CP),
        ],
        edges=[],  # ignored — discovered from data
    )


def _chain_entities(n: int = 3000, seed: int = 0) -> list[EntityResult]:
    """Generate A -> B -> C chain data (B≈A, C≈B with 10% noise)."""
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 2, n)
    b = a ^ (rng.random(n) < 0.1).astype(int)
    c = b ^ (rng.random(n) < 0.1).astype(int)
    out = []
    for i in range(n):
        out.append(
            EntityResult(
                uri=f"{SM}s{i}",
                label=None,
                class_uri=TARGET,
                properties={AP: str(a[i]), BP: str(b[i]), CP: str(c[i])},
            )
        )
    return out


class _MockStore:
    def __init__(self, entities):
        self._entities = entities

    async def find_entities(self, class_uri, filters=None, limit=100, ontology=None):
        return self._entities


async def test_pc_recovers_chain_skeleton():
    model, n = await discover_dag(_MockStore(_chain_entities()), _structure(), TARGET)
    assert n == 3000
    skeleton = {frozenset(e) for e in model.edges}
    # A-B and B-C are adjacent; A-C is NOT (conditional independence given B).
    assert frozenset({A, B}) in skeleton
    assert frozenset({B, C}) in skeleton
    assert frozenset({A, C}) not in skeleton


async def test_discovered_model_is_valid_acyclic_proposal():
    model, _ = await discover_dag(_MockStore(_chain_entities()), _structure(), TARGET)
    # CausalModel construction enforces acyclicity + known-variable edges.
    assert {v.uri for v in model.variables} == {A, B, C}
    assert all(v.observed for v in model.variables)


async def test_discovery_no_data_raises():
    with pytest.raises(BayesianEngineError, match="No usable observations"):
        await discover_dag(_MockStore([]), _structure(), TARGET)
