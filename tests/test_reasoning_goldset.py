"""Reasoning-layer goldset evaluation (Item 3).

Runs the in-memory smoking BN + causal DAG against a reasoning goldset and
checks the evaluate() pass/fail logic. Backend-free (uses the engines directly),
so it runs in the CI unit gate. Requires the [bayes] extra.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pgmpy", reason="reasoning eval requires the [bayes] extra")

from ontorag.core.bayes import CPD, BayesNetwork, BayesVariable
from ontorag.core.causal import CausalModel, CausalVariable
from ontorag.eval.reasoning_goldset import ReasoningGoldset, ReasoningQuestion, evaluate

SM = "https://ontorag.dev/smoking#"
G, S, C = SM + "Genotype", SM + "Smoking", SM + "Cancer"


def _bn() -> BayesNetwork:
    return BayesNetwork(
        name="smoking",
        variables=[
            BayesVariable(uri=G, states=["g0", "g1"]),
            BayesVariable(uri=S, states=["no", "yes"]),
            BayesVariable(uri=C, states=["no", "yes"]),
        ],
        cpds=[
            CPD(variable=G, values=[[0.5], [0.5]]),
            CPD(variable=S, evidence=[G], values=[[0.8, 0.2], [0.2, 0.8]]),
            CPD(variable=C, evidence=[S, G], values=[[0.9, 0.7, 0.6, 0.2], [0.1, 0.3, 0.4, 0.8]]),
        ],
    )


def _causal() -> CausalModel:
    return CausalModel(
        name="smoking",
        variables=[CausalVariable(uri=G), CausalVariable(uri=S), CausalVariable(uri=C)],
        edges=[(G, S), (G, C), (S, C)],
    )


async def test_reasoning_goldset_all_pass():
    gs = ReasoningGoldset.load("examples/smoking/reasoning_goldset.jsonl")
    report = await evaluate(gs, _bn(), _causal())
    assert report["total"] == 6
    assert report["failures"] == 0, [r for r in report["results"] if not r["passed"]]


async def test_reasoning_goldset_detects_wrong_expected():
    # A deliberately wrong expectation must be reported as a failure.
    gs = ReasoningGoldset(questions=[
        ReasoningQuestion(
            id="X", kind="posterior", query=C, evidence={S: "yes"},
            expected={"yes": 0.99, "no": 0.01}, tolerance=0.01,
        )
    ])
    report = await evaluate(gs, _bn(), _causal())
    assert report["failures"] == 1
    assert report["results"][0]["passed"] is False


async def test_reasoning_identify_backdoor():
    gs = ReasoningGoldset(questions=[
        ReasoningQuestion(
            id="ID", kind="identify", treatment=S, outcome=C,
            expected_identifiable=True, expected_backdoor=[G],
        )
    ])
    report = await evaluate(gs, _bn(), _causal())
    assert report["failures"] == 0
    assert report["results"][0]["got_backdoor"] == [G]
