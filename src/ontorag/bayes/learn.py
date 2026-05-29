"""CPT learning from ABox data (v0.7.4).

Given a :class:`~ontorag.core.bayes.StructureSpec` (a DAG whose variables carry
``bn:represents`` property URIs) and a target class, estimate the conditional
probability tables from the instance data already in the store, then assemble a
complete :class:`~ontorag.core.bayes.BayesNetwork`.

This ties v0.3 LLMs4OL output to BN parameter estimation: text → ontology
triples → (here) learned probabilities over those triples.

pgmpy + pandas are optional (the ``[bayes]`` extra); imported lazily and the
estimation runs in a worker thread (``asyncio.to_thread``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

from ontorag.bayes.engine import BayesianEngineError
from ontorag.core.bayes import BayesNetwork, BayesVariable, CPD, StructureSpec

if TYPE_CHECKING:
    from ontorag.stores.base import GraphStore

logger = logging.getLogger(__name__)

EstimatorName = Literal["bayes", "mle"]


async def learn_cpts(
    store: GraphStore,
    structure: StructureSpec,
    target_class: str,
    *,
    ontology: str | None = None,
    estimator: EstimatorName = "bayes",
    limit: int = 10_000,
) -> tuple[BayesNetwork, int]:
    """Estimate CPTs for ``structure`` from instances of ``target_class``.

    Each instance contributes one observation: for every variable, the value of
    its ``bn:represents`` property is read and matched (string-equal) to one of
    the variable's declared states. Instances missing any variable's value, or
    whose value is not a declared state, are skipped.

    Args:
        store: The graph store to pull observations from.
        structure: DAG + variable→property mapping (no CPTs).
        target_class: Class URI whose instances are the observation rows.
        ontology: Ontology scope for the data query.
        estimator: ``"bayes"`` (BDeu prior — robust to unseen combos) or
            ``"mle"`` (maximum likelihood).
        limit: Max instances to sample.

    Returns:
        ``(network, n_observations)`` — the learned network and how many
        instances produced a usable observation.

    Raises:
        BayesianEngineError: missing pgmpy/pandas, no usable observations, or a
            variable without a ``bn:represents`` mapping.
    """
    rows = await gather_observations(
        store, structure.variables, target_class, ontology=ontology, limit=limit
    )
    network = await asyncio.to_thread(_fit_sync, structure, rows, estimator)
    return network, len(rows)


async def gather_observations(
    store: GraphStore,
    variables: list[BayesVariable],
    target_class: str,
    *,
    ontology: str | None = None,
    limit: int = 10_000,
) -> list[dict[str, str]]:
    """Pull one observation row per instance of ``target_class``.

    Shared by CPT learning and causal structure discovery. Each variable's
    ``bn:represents`` property value is read and matched (string-equal) to a
    declared state; instances missing any variable's value, or whose value is
    not a declared state, are skipped.

    Returns:
        Rows as ``{variable_uri: state_label}``.

    Raises:
        BayesianEngineError: a variable without ``bn:represents``, or no usable
            observations at all.
    """
    for var in variables:
        if not var.represents:
            raise BayesianEngineError(
                f"Variable {var.uri!r} has no bn:represents property; cannot "
                "pull observations for it."
            )

    entities = await store.find_entities(target_class, limit=limit, ontology=ontology)
    if len(entities) == limit:
        logger.warning(
            "gather_observations: hit the instance limit (%d) for %s — CPTs are "
            "learned from a non-random first-%d slice. Raise `limit` to use the "
            "full population.",
            limit,
            target_class,
            limit,
        )
    rows: list[dict[str, str]] = []
    missing_count: dict[str, int] = {}  # var.uri → instances missing its value
    undeclared: dict[str, set[str]] = {}  # var.uri → observed values not in states
    for ent in entities:
        row: dict[str, str] = {}
        usable = True
        for var in variables:
            raw = ent.properties.get(var.represents)
            if raw is None:
                missing_count[var.uri] = missing_count.get(var.uri, 0) + 1
                usable = False
                break
            value = raw[0] if isinstance(raw, list) else raw
            state = str(value)
            if state not in var.states:
                undeclared.setdefault(var.uri, set()).add(state)
                usable = False
                break
            row[var.uri] = state
        if usable:
            rows.append(row)

    skipped = len(entities) - len(rows)
    if skipped:
        logger.warning(
            "gather_observations: skipped %d/%d instances of %s. "
            "Missing values per variable: %s. Undeclared values (likely a "
            "bn:represents pointing at a URI/object property, or a state-label "
            "mismatch): %s",
            skipped,
            len(entities),
            target_class,
            missing_count or "none",
            {k: sorted(v)[:5] for k, v in undeclared.items()} or "none",
        )

    if not rows:
        detail = ""
        if undeclared:
            sample = {k: sorted(v)[:3] for k, v in undeclared.items()}
            detail = (
                f" Observed values not matching any declared state: {sample} — "
                "check that bn:represents maps to a label/datatype property whose "
                "values equal the declared bn:states (object-property URIs will "
                "not match state labels)."
            )
        elif missing_count:
            detail = (
                f" Instances missing a value per variable: {missing_count} — "
                "check the bn:represents property URIs."
            )
        raise BayesianEngineError(
            f"No usable observations: none of the {len(entities)} instances of "
            f"{target_class!r} had a declared state for every variable.{detail}"
        )
    return rows


def _fit_sync(
    structure: StructureSpec,
    rows: list[dict[str, str]],
    estimator: EstimatorName,
) -> BayesNetwork:
    try:
        import pandas as pd
        from pgmpy.estimators import BayesianEstimator, MaximumLikelihoodEstimator
        from pgmpy.models import DiscreteBayesianNetwork
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise BayesianEngineError(
            "CPT learning requires pgmpy + pandas. Install the optional extra: "
            "`pip install 'ontorag[bayes]'` (or `uv sync --extra bayes`)."
        ) from exc

    # pandas 2.3+ infers python-str columns as the new `str` dtype, which
    # pgmpy 1.0's preprocess_data does not recognise (it checks is_object_dtype)
    # and rejects with "Couldn't infer datatype". Coerce to object so the state
    # labels are treated as categorical.
    df = pd.DataFrame(rows).astype(object)
    model = DiscreteBayesianNetwork(structure.edges)
    model.add_nodes_from([v.uri for v in structure.variables])

    # Declared states ensure CPTs cover every state even if unobserved.
    state_names = {v.uri: list(v.states) for v in structure.variables}

    if estimator == "mle":
        model.fit(
            df,
            estimator=MaximumLikelihoodEstimator,
            state_names=state_names,
        )
    else:
        model.fit(
            df,
            estimator=BayesianEstimator,
            prior_type="BDeu",
            equivalent_sample_size=1,
            state_names=state_names,
        )

    cpds: list[CPD] = []
    for tcpd in model.get_cpds():
        evidence = list(tcpd.variables[1:])  # variables = [var, *evidence]
        values = [[float(x) for x in row] for row in tcpd.get_values()]
        cpds.append(CPD(variable=str(tcpd.variable), evidence=evidence, values=values))

    return BayesNetwork(
        variables=list(structure.variables),
        cpds=cpds,
        name=structure.name,
    )
