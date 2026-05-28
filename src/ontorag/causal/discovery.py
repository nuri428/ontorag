"""Causal structure discovery from observational data (v0.8.3).

Runs the PC algorithm over instance data (reusing the CPT-learning data
extraction) to **propose** a causal DAG. The result is a proposal only — it is
never auto-committed (CLAUDE.md anti-pattern): the PC algorithm recovers a
Markov-equivalence class, so some edge orientations are not determined by the
data and are chosen heuristically when a single DAG is extracted. A human must
review before the DAG is stored or used for causal claims.

pgmpy + pandas are the optional ``[bayes]`` extra; imported lazily, fit runs in
a worker thread.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ontorag.bayes.engine import BayesianEngineError
from ontorag.bayes.learn import gather_observations
from ontorag.core.bayes import StructureSpec
from ontorag.core.causal import CausalModel, CausalVariable

if TYPE_CHECKING:
    from ontorag.stores.base import GraphStore


async def discover_dag(
    store: GraphStore,
    structure: StructureSpec,
    target_class: str,
    *,
    ontology: str | None = None,
    ci_test: str = "chi_square",
    significance_level: float = 0.01,
    limit: int = 10_000,
) -> tuple[CausalModel, int]:
    """Propose a causal DAG over ``structure``'s variables from ABox data.

    Edges declared in ``structure`` are ignored — the DAG is learned from data.

    Args:
        store: graph store to pull observations from.
        structure: variables (with ``bn:represents`` + states); edges ignored.
        target_class: class URI whose instances are observation rows.
        ontology: ontology scope for the data query.
        ci_test: conditional-independence test for PC (e.g. "chi_square").
        significance_level: PC significance threshold.
        limit: max instances to sample.

    Returns:
        ``(proposed_model, n_observations)``. The model is a *proposal* — review
        before storing.

    Raises:
        BayesianEngineError: missing pgmpy/pandas or no usable observations.
    """
    rows = await gather_observations(
        store, structure.variables, target_class, ontology=ontology, limit=limit
    )
    model = await asyncio.to_thread(
        _pc_sync, structure, rows, ci_test, significance_level
    )
    return model, len(rows)


def _pc_sync(
    structure: StructureSpec,
    rows: list[dict[str, str]],
    ci_test: str,
    significance_level: float,
) -> CausalModel:
    try:
        import pandas as pd
        from pgmpy.estimators import PC
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise BayesianEngineError(
            "Structure discovery requires pgmpy + pandas. Install the optional "
            "extra: `pip install 'ontorag[bayes]'` (or `uv sync --extra bayes`)."
        ) from exc

    df = pd.DataFrame(rows).astype(object)
    est = PC(data=df)
    dag = est.estimate(
        return_type="dag",
        ci_test=ci_test,
        significance_level=significance_level,
        show_progress=False,
    )
    edges = [(str(a), str(b)) for a, b in dag.edges()]
    variables = [
        CausalVariable(uri=v.uri, observed=True, label=v.label)
        for v in structure.variables
    ]
    return CausalModel(
        variables=variables,
        edges=edges,
        name=(structure.name or "discovered"),
    )
