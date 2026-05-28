"""Bayesian inference engine — pgmpy wrapper (v0.7.3).

Turns a :class:`~ontorag.core.bayes.BayesNetwork` (the storage-agnostic spec) into
a pgmpy ``DiscreteBayesianNetwork`` and answers two question kinds:

- ``compute_posterior(evidence, query)`` — P(query | evidence) marginals
  (Pearl Rung 1 observational inference);
- ``mpe(evidence)`` — the most probable explanation: the single most likely
  joint assignment to all non-evidence variables.

pgmpy is an **optional** dependency (the ``[bayes]`` extra). It is imported
lazily inside the worker so importing this module never requires it; a missing
install surfaces as :class:`BayesianEngineError` with an actionable message.

Synchronous pgmpy calls run in a worker thread via ``asyncio.to_thread`` so the
event loop is never blocked (pgmpy has no async API).

Variables and evidence keys may be given as either the variable URI or its
rdfs:label — both resolve to the canonical URI. Evidence and query results use
state *labels* (not integer indices).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ontorag.core.bayes import BayesNetwork, BayesVariable

if TYPE_CHECKING:
    from pgmpy.models import DiscreteBayesianNetwork


class BayesianEngineError(RuntimeError):
    """Raised when inference cannot run (missing pgmpy, malformed model, or
    invalid evidence/query). Carries a user-facing message."""


class BayesianEngine:
    """Wraps one BayesNetwork for repeated inference queries.

    The pgmpy model is built lazily on first query and cached for the lifetime
    of the instance (build includes ``check_model()``).
    """

    def __init__(self, network: BayesNetwork) -> None:
        self._network = network
        self._model: DiscreteBayesianNetwork | None = None
        self._by_uri: dict[str, BayesVariable] = {v.uri: v for v in network.variables}
        # label → uri, only for labels that are unambiguous.
        self._label_to_uri: dict[str, str] = {}
        seen_labels: set[str] = set()
        for v in network.variables:
            if not v.label:
                continue
            if v.label in self._label_to_uri or v.label in seen_labels:
                self._label_to_uri.pop(v.label, None)  # ambiguous → drop
                seen_labels.add(v.label)
            else:
                self._label_to_uri[v.label] = v.uri

    # ── public async API ──────────────────────────────────────────────────────

    async def compute_posterior(
        self, evidence: dict[str, str], query: list[str]
    ) -> dict[str, dict[str, float]]:
        """P(query | evidence) as ``{variable_uri: {state: probability}}``.

        Args:
            evidence: ``{variable: observed_state}`` — variable as URI or label,
                state as a state label.
            query: variables (URI or label) to compute marginals for.

        Raises:
            BayesianEngineError: missing pgmpy, invalid evidence/query, or a
                model pgmpy rejects.
        """
        if not query:
            raise BayesianEngineError("query must name at least one variable.")
        return await asyncio.to_thread(self._posterior_sync, evidence, query)

    async def mpe(self, evidence: dict[str, str]) -> dict[str, str]:
        """Most probable explanation: ``{variable_uri: most_likely_state}`` for
        every non-evidence variable, given the evidence.

        Raises:
            BayesianEngineError: missing pgmpy, invalid evidence, or a model
                pgmpy rejects.
        """
        return await asyncio.to_thread(self._mpe_sync, evidence)

    # ── model construction ──────────────────────────────────────────────────────

    def _build_model(self) -> DiscreteBayesianNetwork:
        try:
            from pgmpy.factors.discrete import TabularCPD
            from pgmpy.models import DiscreteBayesianNetwork
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise BayesianEngineError(
                "Bayesian inference requires pgmpy. Install the optional extra: "
                "`pip install 'ontorag[bayes]'` (or `uv sync --extra bayes`)."
            ) from exc

        net = self._network
        cpd_vars = {c.variable for c in net.cpds}
        missing = [v.uri for v in net.variables if v.uri not in cpd_vars]
        if missing:
            raise BayesianEngineError(
                "Every variable needs a CPD for inference; missing CPD for: "
                + ", ".join(sorted(missing))
            )

        model = DiscreteBayesianNetwork()
        model.add_nodes_from([v.uri for v in net.variables])
        model.add_edges_from(
            [(ev, c.variable) for c in net.cpds for ev in c.evidence]
        )

        tabular_cpds = []
        for c in net.cpds:
            var = self._by_uri[c.variable]
            state_names = {c.variable: list(var.states)}
            for ev in c.evidence:
                state_names[ev] = list(self._by_uri[ev].states)
            tabular_cpds.append(
                TabularCPD(
                    variable=c.variable,
                    variable_card=var.cardinality,
                    values=c.values,
                    evidence=list(c.evidence) or None,
                    evidence_card=[self._by_uri[e].cardinality for e in c.evidence]
                    or None,
                    state_names=state_names,
                )
            )
        model.add_cpds(*tabular_cpds)

        try:
            model.check_model()
        except Exception as exc:  # pgmpy raises various error types
            raise BayesianEngineError(f"Invalid Bayesian network: {exc}") from exc
        return model

    def _ensure_model(self) -> DiscreteBayesianNetwork:
        if self._model is None:
            self._model = self._build_model()
        return self._model

    # ── resolution helpers ──────────────────────────────────────────────────────

    def _resolve_var(self, name: str) -> str:
        if name in self._by_uri:
            return name
        if name in self._label_to_uri:
            return self._label_to_uri[name]
        raise BayesianEngineError(f"Unknown variable: {name!r}.")

    def _resolve_evidence(self, evidence: dict[str, str]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for raw_var, state in evidence.items():
            uri = self._resolve_var(raw_var)
            valid = self._by_uri[uri].states
            if state not in valid:
                raise BayesianEngineError(
                    f"Variable {uri!r} has no state {state!r}; valid states: "
                    f"{list(valid)}."
                )
            resolved[uri] = state
        return resolved

    # ── sync workers (run via asyncio.to_thread) ─────────────────────────────────

    def _posterior_sync(
        self, evidence: dict[str, str], query: list[str]
    ) -> dict[str, dict[str, float]]:
        from pgmpy.inference import VariableElimination

        model = self._ensure_model()
        ev = self._resolve_evidence(evidence)
        q_uris = [self._resolve_var(v) for v in query]
        # A query variable that is also given as evidence is degenerate.
        overlap = [v for v in q_uris if v in ev]
        if overlap:
            raise BayesianEngineError(
                "Query variables cannot also be evidence: " + ", ".join(overlap)
            )

        infer = VariableElimination(model)
        result: Any = infer.query(
            variables=q_uris,
            evidence=ev or None,
            joint=False,
            show_progress=False,
        )

        # joint=False returns {var: DiscreteFactor}; be defensive about a single
        # DiscreteFactor return as well.
        factors = result if isinstance(result, dict) else {q_uris[0]: result}
        out: dict[str, dict[str, float]] = {}
        for var_uri, factor in factors.items():
            states = factor.state_names[var_uri]
            out[var_uri] = {
                str(state): float(prob)
                for state, prob in zip(states, factor.values)
            }
        return out

    def _mpe_sync(self, evidence: dict[str, str]) -> dict[str, str]:
        from pgmpy.inference import VariableElimination

        model = self._ensure_model()
        ev = self._resolve_evidence(evidence)
        query_vars = [v.uri for v in self._network.variables if v.uri not in ev]
        if not query_vars:
            return {}

        infer = VariableElimination(model)
        assignment: dict[str, Any] = infer.map_query(
            variables=query_vars,
            evidence=ev or None,
            show_progress=False,
        )
        return {str(k): str(v) for k, v in assignment.items()}
