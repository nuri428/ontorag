"""Reasoning-layer goldset — evaluation for the probabilistic + causal tools.

The SPARQL-centric :class:`~ontorag.eval.goldset.Goldset` can't express reasoning
questions (they're posterior / do / counterfactual / identify queries with
expected probabilities, not SPARQL). This module adds a parallel, minimal goldset
format + a runner that loads a stored Bayesian network (and optional causal DAG)
from the active backend and checks each question against its expected values
within a tolerance.

This fills the gap noted in docs/BENCHMARK_v1.md ("reasoning layers have no
goldset questions yet"). Backend-agnostic: the engines run identically on
Fuseki / Neo4j / FalkorDB.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

ReasoningKind = Literal["posterior", "do", "counterfactual", "identify"]


class ReasoningQuestion(BaseModel):
    """One reasoning-goldset row. Fields used depend on ``kind``."""

    id: str
    kind: ReasoningKind
    description: str = ""
    # distribution-returning kinds (posterior / do / counterfactual)
    query: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)   # posterior
    do: dict[str, str] = Field(default_factory=dict)          # do
    observed: dict[str, str] = Field(default_factory=dict)    # counterfactual
    intervention: dict[str, str] = Field(default_factory=dict)  # counterfactual
    expected: dict[str, float] = Field(default_factory=dict)  # {state: prob}
    tolerance: float = 0.01
    # identify kind
    treatment: str | None = None
    outcome: str | None = None
    expected_identifiable: bool | None = None
    expected_backdoor: list[str] | None = None


class ReasoningGoldset(BaseModel):
    questions: list[ReasoningQuestion] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "ReasoningGoldset":
        rows = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(questions=[ReasoningQuestion(**r) for r in rows])


async def evaluate(
    goldset: ReasoningGoldset, bn: Any, causal: Any = None
) -> dict[str, Any]:
    """Run every reasoning question against the engines; return a report dict.

    Args:
        goldset: parsed reasoning goldset.
        bn: a quantified ``BayesNetwork`` (from ``store.get_bayes_network``).
        causal: optional ``CausalModel`` (for do / counterfactual / identify).

    Returns:
        ``{total, failures, results: [...]}`` — each result carries pass/fail,
        the observed value, the expected value, and the max deviation.
    """
    from ontorag.bayes.engine import BayesianEngine
    from ontorag.causal.engine import CausalEngine

    bayes = BayesianEngine(bn)
    causal_engine = CausalEngine(bn, causal)
    results: list[dict[str, Any]] = []

    for q in goldset.questions:
        ok = False
        detail: dict[str, Any] = {"id": q.id, "kind": q.kind, "description": q.description}
        try:
            if q.kind in ("posterior", "do", "counterfactual"):
                if q.kind == "posterior":
                    dist = await bayes.compute_posterior(q.evidence, [q.query])
                elif q.kind == "do":
                    dist = await causal_engine.do_query(q.do, [q.query], q.evidence)
                else:  # counterfactual
                    dist = await causal_engine.counterfactual(
                        q.observed, q.intervention, [q.query]
                    )
                got = dist[q.query]
                max_dev = max(
                    abs(got.get(state, 0.0) - exp) for state, exp in q.expected.items()
                )
                ok = max_dev <= q.tolerance
                detail.update(
                    {"expected": q.expected, "got": {k: round(v, 4) for k, v in got.items()},
                     "max_deviation": round(max_dev, 4), "tolerance": q.tolerance}
                )
            elif q.kind == "identify":
                info = await causal_engine.identify(q.treatment, q.outcome)
                ident_ok = (
                    q.expected_identifiable is None
                    or info["identifiable"] == q.expected_identifiable
                )
                bd_ok = (
                    q.expected_backdoor is None
                    or sorted(info["backdoor_adjustment_set"]) == sorted(q.expected_backdoor)
                )
                ok = ident_ok and bd_ok
                detail.update(
                    {"expected_identifiable": q.expected_identifiable,
                     "expected_backdoor": q.expected_backdoor,
                     "got_identifiable": info["identifiable"],
                     "got_backdoor": info["backdoor_adjustment_set"]}
                )
        except Exception as exc:  # noqa: BLE001 — record, don't abort the run
            detail["error"] = f"{type(exc).__name__}: {exc}"
        detail["passed"] = ok
        results.append(detail)

    failures = sum(1 for r in results if not r["passed"])
    return {"total": len(results), "failures": failures, "results": results}
