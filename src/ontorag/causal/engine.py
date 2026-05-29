"""Causal inference engine — pgmpy CausalInference wrapper (v0.8.1+).

Pearl's causal hierarchy on top of the v0.7 probabilistic layer:

- **Rung 2 (intervention)** — ``do_query(do, query, evidence)`` computes
  ``P(query | do(X=x), evidence)`` via graph surgery. pgmpy applies the proper
  back-door adjustment automatically from the BN structure, so as long as the
  confounders are present in the BN, ``do(X)`` de-confounds (differs from the
  observational ``see(X)``).
- **identification** — ``identify(treatment, outcome)`` reports valid back-door
  / front-door adjustment sets and identifiability using the *causal DAG*
  (which may include latent confounders absent from the quantified BN).
- **Rung 3 (counterfactual)** — added in v0.8.2 (twin-network).

**Over-claim guard:** the causal DAG is user-supplied. ontorag computes these
queries *assuming the DAG is correct*; it does not validate causal semantics.

The quantified model (DAG + CPTs) is the BayesNetwork from the probabilistic
layer; the CausalModel supplies the causal DAG + latent markers. pgmpy is the
optional ``[bayes]`` extra; sync calls run in a worker thread.
"""

from __future__ import annotations

import asyncio
import itertools
from math import prod
from typing import TYPE_CHECKING, Any

from ontorag.bayes.engine import BayesianEngineError, build_discrete_bn
from ontorag.core.bayes import BayesNetwork
from ontorag.core.causal import CausalModel

if TYPE_CHECKING:
    from pgmpy.models import DiscreteBayesianNetwork

# Cap on the canonical-SCM response space enumerated for counterfactuals
# (product over variables of cardinality ** num_parent_configs). Keeps Rung-3
# tractable; larger models should reduce variable cardinality / parents.
_CF_RESPONSE_CAP = 2_000_000


class CausalEngineError(RuntimeError):
    """Raised when a causal query cannot run (missing pgmpy, invalid model,
    invalid do/query/evidence, or an unidentifiable effect)."""


class CausalEngine:
    """Causal queries over a quantified BN + a causal DAG.

    Args:
        bn: The quantified Bayesian network (DAG + CPTs).
        causal: Optional causal DAG with latent markers. When omitted, the BN's
            own structure is used as the causal DAG (all variables observed).
    """

    def __init__(self, bn: BayesNetwork, causal: CausalModel | None = None) -> None:
        self._bn = bn
        self._causal = causal
        self._model: DiscreteBayesianNetwork | None = None
        self._by_uri = {v.uri: v for v in bn.variables}
        self._label_to_uri: dict[str, str] = {}
        seen: set[str] = set()
        for v in bn.variables:
            if not v.label:
                continue
            if v.label in self._label_to_uri or v.label in seen:
                self._label_to_uri.pop(v.label, None)
                seen.add(v.label)
            else:
                self._label_to_uri[v.label] = v.uri

    # ── public async API ──────────────────────────────────────────────────────

    async def do_query(
        self,
        do: dict[str, str],
        query: list[str],
        evidence: dict[str, str] | None = None,
    ) -> dict[str, dict[str, float]]:
        """P(query | do(intervention), evidence) — interventional (Rung 2).

        Args:
            do: ``{variable: state}`` interventions (graph surgery).
            query: variables (URI or label) to get distributions for.
            evidence: optional observed ``{variable: state}`` conditioned after
                the intervention.
        """
        if not do:
            raise CausalEngineError("do must name at least one intervention.")
        if not query:
            raise CausalEngineError("query must name at least one variable.")
        return await asyncio.to_thread(self._do_sync, do, query, evidence or {})

    async def explain_do(
        self, do: dict[str, str], query: list[str], evidence: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """``do_query`` plus a back-door explanation of *why* it differs from
        observing — the adjustment set(s) used per (intervention → query) pair.

        Returns ``{distribution, adjustment, explanation}`` where ``adjustment``
        maps ``"do_var → query_var"`` to the back-door set the surgery adjusts
        over, and ``explanation`` is a one-line human summary. Pure read; reuses
        ``_do_sync`` and ``get_minimal_adjustment_set`` (no new inference path).
        """
        if not do:
            raise CausalEngineError("do must name at least one intervention.")
        if not query:
            raise CausalEngineError("query must name at least one variable.")
        return await asyncio.to_thread(self._explain_do_sync, do, query, evidence or {})

    async def identify(self, treatment: str, outcome: str) -> dict[str, Any]:
        """Report adjustment sets / identifiability for treatment → outcome,
        using the causal DAG (latent confounders respected)."""
        return await asyncio.to_thread(self._identify_sync, treatment, outcome)

    async def counterfactual(
        self,
        observed: dict[str, str],
        intervention: dict[str, str],
        query: list[str],
    ) -> dict[str, dict[str, float]]:
        """Counterfactual (Rung 3): P(query in a world where ``intervention``
        held, given we actually observed ``observed``).

        Computed by abduction-action-prediction over the **canonical
        independent-noise SCM** consistent with the CPTs: each variable's
        per-parent-configuration response is independent with probability equal
        to the CPT. Counterfactuals are *not* uniquely identified by the CPTs
        alone — this is one standard, documented SCM choice.

        Args:
            observed: the factual evidence actually observed ``{variable: state}``.
            intervention: the counterfactual antecedent ``{variable: state}``
                ("had these variables been …").
            query: variables (URI or label) to compute in the counterfactual world.

        Raises:
            CausalEngineError: invalid inputs, a variable without a CPD, an
                impossible observation, or a response space exceeding the cap.
        """
        if not intervention:
            raise CausalEngineError("intervention must name at least one variable.")
        if not query:
            raise CausalEngineError("query must name at least one variable.")
        return await asyncio.to_thread(
            self._counterfactual_sync, observed, intervention, query
        )

    # ── helpers ────────────────────────────────────────────────────────────────

    def _ensure_model(self) -> DiscreteBayesianNetwork:
        if self._model is None:
            try:
                self._model = build_discrete_bn(self._bn)
            except BayesianEngineError as exc:
                raise CausalEngineError(str(exc)) from exc
        return self._model

    def _resolve_var(self, name: str) -> str:
        if name in self._by_uri:
            return name
        if name in self._label_to_uri:
            return self._label_to_uri[name]
        raise CausalEngineError(f"Unknown variable: {name!r}.")

    def _resolve_assignment(self, mapping: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for raw, state in mapping.items():
            uri = self._resolve_var(raw)
            valid = self._by_uri[uri].states
            if state not in valid:
                raise CausalEngineError(
                    f"Variable {uri!r} has no state {state!r}; valid: {list(valid)}."
                )
            out[uri] = state
        return out

    def _causal_dag(self) -> DiscreteBayesianNetwork:
        from pgmpy.models import DiscreteBayesianNetwork

        if self._causal is not None:
            dag = DiscreteBayesianNetwork(
                list(self._causal.edges), latents=set(self._causal.latent_uris)
            )
            dag.add_nodes_from([v.uri for v in self._causal.variables])
        else:
            edges = [(ev, c.variable) for c in self._bn.cpds for ev in c.evidence]
            dag = DiscreteBayesianNetwork(edges)
            dag.add_nodes_from([v.uri for v in self._bn.variables])
        return dag

    # ── sync workers ─────────────────────────────────────────────────────────

    def _do_sync(
        self, do: dict[str, str], query: list[str], evidence: dict[str, str]
    ) -> dict[str, dict[str, float]]:
        from pgmpy.inference import CausalInference

        model = self._ensure_model()
        do_r = self._resolve_assignment(do)
        ev_r = self._resolve_assignment(evidence)
        q = [self._resolve_var(v) for v in query]

        clash = (set(q) & set(do_r)) | (set(q) & set(ev_r)) | (set(do_r) & set(ev_r))
        if clash:
            raise CausalEngineError(
                "do / query / evidence variables must be disjoint: "
                + ", ".join(sorted(clash))
            )

        ci = CausalInference(model)
        try:
            factor = ci.query(
                variables=q,
                do=do_r,
                evidence=ev_r or None,
                show_progress=False,
            )
        except Exception as exc:  # pgmpy raises various error types
            raise CausalEngineError(f"do-query failed: {exc}") from exc

        out: dict[str, dict[str, float]] = {}
        for var in q:
            marg = (
                factor
                if len(q) == 1
                else factor.marginalize([x for x in q if x != var], inplace=False)
            )
            states = marg.state_names[var]
            out[var] = {str(s): float(p) for s, p in zip(states, marg.values)}
        return out

    def _explain_do_sync(
        self, do: dict[str, str], query: list[str], evidence: dict[str, str]
    ) -> dict[str, Any]:
        from pgmpy.inference import CausalInference

        distribution = self._do_sync(do, query, evidence)
        do_r = self._resolve_assignment(do)
        q = [self._resolve_var(v) for v in query]
        ci = CausalInference(self._causal_dag())

        adjustment: dict[str, list[str]] = {}
        adjusted_vars: set[str] = set()
        for dv in do_r:
            for qv in q:
                try:
                    bset = ci.get_minimal_adjustment_set(dv, qv)
                except Exception:  # noqa: BLE001 — pair may be trivially unconfounded
                    bset = set()
                cols = sorted(bset) if bset else []
                adjustment[f"{dv} → {qv}"] = cols
                adjusted_vars.update(cols)

        if adjusted_vars:
            short = ", ".join(sorted(v.split("#")[-1].split("/")[-1] for v in adjusted_vars))
            explanation = (
                f"do(X) cuts incoming edges to the intervened variable(s) and "
                f"back-door adjusts over {{{short}}}, so the result removes the "
                f"confounding that plain observation (see) would include — this is "
                f"why do ≠ see."
            )
        else:
            explanation = (
                "No back-door adjustment was needed (no confounders between the "
                "intervention and query in the DAG), so do equals see here."
            )
        return {
            "distribution": distribution,
            "adjustment": adjustment,
            "explanation": explanation,
        }

    def _identify_sync(self, treatment: str, outcome: str) -> dict[str, Any]:
        from pgmpy.inference import CausalInference

        t = self._resolve_var(treatment)
        o = self._resolve_var(outcome)
        ci = CausalInference(self._causal_dag())

        # pgmpy returns an (possibly empty) set when no covariate adjustment is
        # needed, and None when the effect is not back-door identifiable — both
        # are valid answers, not errors. Only a genuine computation failure (e.g.
        # a malformed DAG) raises; surface that instead of masking it as
        # "not identifiable". treatment/outcome are pre-validated by _resolve_var.
        try:
            backdoor = ci.get_minimal_adjustment_set(t, o)
            frontdoor = ci.get_all_frontdoor_adjustment_sets(t, o)
        except Exception as exc:  # pragma: no cover - pgmpy internal failure
            raise CausalEngineError(
                f"Identification of {t!r} → {o!r} failed: {exc}"
            ) from exc

        backdoor_set = sorted(backdoor) if backdoor else []
        frontdoor_sets = [sorted(s) for s in frontdoor] if frontdoor else []
        return {
            "treatment": t,
            "outcome": o,
            "identifiable": backdoor is not None or bool(frontdoor_sets),
            "backdoor_adjustment_set": backdoor_set,
            "frontdoor_adjustment_sets": frontdoor_sets,
        }

    # ── counterfactual (Rung 3, canonical-SCM abduction-action-prediction) ──────

    def _topo_order(self) -> list[str]:
        """Topological order of BN variables (parents before children)."""
        uris = [v.uri for v in self._bn.variables]
        parents: dict[str, list[str]] = {u: [] for u in uris}
        children: dict[str, list[str]] = {u: [] for u in uris}
        for c in self._bn.cpds:
            for ev in c.evidence:
                parents[c.variable].append(ev)
                children[ev].append(c.variable)
        indeg = {u: len(parents[u]) for u in uris}
        queue = sorted(u for u in uris if indeg[u] == 0)
        order: list[str] = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for ch in sorted(children[n]):
                indeg[ch] -= 1
                if indeg[ch] == 0:
                    queue.append(ch)
            queue.sort()
        if len(order) != len(uris):
            raise CausalEngineError("BN structure is cyclic; cannot evaluate.")
        return order

    @staticmethod
    def _col_index(evidence: list[str], cards: dict[str, int], assign: dict[str, int]) -> int:
        """Column index of a parent assignment in a CPD values matrix.

        Matches the pgmpy TabularCPD layout used elsewhere: evidence listed in
        order, with the LAST evidence variable varying fastest.
        """
        idx = 0
        for i, e in enumerate(evidence):
            weight = 1
            for j in range(i + 1, len(evidence)):
                weight *= cards[evidence[j]]
            idx += assign[e] * weight
        return idx

    def _counterfactual_sync(
        self,
        observed: dict[str, str],
        intervention: dict[str, str],
        query: list[str],
    ) -> dict[str, dict[str, float]]:
        bn = self._bn
        cpd_by_var = {c.variable: c for c in bn.cpds}
        missing = [v.uri for v in bn.variables if v.uri not in cpd_by_var]
        if missing:
            raise CausalEngineError(
                "Counterfactuals need a CPD for every variable; missing: "
                + ", ".join(sorted(missing))
            )

        order = self._topo_order()
        cards = {v.uri: v.cardinality for v in bn.variables}
        sidx = {v.uri: {s: i for i, s in enumerate(v.states)} for v in bn.variables}

        obs = self._resolve_assignment(observed)
        interv = self._resolve_assignment(intervention)
        q = [self._resolve_var(v) for v in query]
        obs_idx = {u: sidx[u][s] for u, s in obs.items()}
        interv_idx = {u: sidx[u][s] for u, s in interv.items()}

        # Per-variable response-function space: a function maps each parent
        # configuration (column) to a value index. P(response) is the canonical
        # independent product of the CPT column probabilities.
        total = 1
        var_funcs: dict[str, list[tuple[int, ...]]] = {}
        func_weight: dict[str, dict[tuple[int, ...], float]] = {}
        for u in order:
            cpd = cpd_by_var[u]
            ncols = len(cpd.values[0])
            c = cards[u]
            total *= c**ncols
            if total > _CF_RESPONSE_CAP:
                raise CausalEngineError(
                    "Counterfactual response space exceeds the cap "
                    f"({_CF_RESPONSE_CAP}); reduce variable cardinality/parents."
                )
            funcs = list(itertools.product(range(c), repeat=ncols))
            var_funcs[u] = funcs
            func_weight[u] = {
                g: prod(cpd.values[g[col]][col] for col in range(ncols)) for g in funcs
            }

        total_consistent = 0.0
        dist: dict[str, dict[int, float]] = {v: {} for v in q}

        for combo in itertools.product(*(var_funcs[u] for u in order)):
            assign_g = dict(zip(order, combo))
            weight = prod(func_weight[u][assign_g[u]] for u in order)
            if weight == 0.0:
                continue

            # Abduction: evaluate the factual world; keep R consistent with obs.
            factual: dict[str, int] = {}
            consistent = True
            for u in order:
                cpd = cpd_by_var[u]
                col = self._col_index(
                    cpd.evidence, cards, {e: factual[e] for e in cpd.evidence}
                )
                factual[u] = assign_g[u][col]
                if u in obs_idx and factual[u] != obs_idx[u]:
                    consistent = False
                    break
            if not consistent:
                continue
            total_consistent += weight

            # Action + prediction: evaluate the counterfactual world.
            cf: dict[str, int] = {}
            for u in order:
                if u in interv_idx:
                    cf[u] = interv_idx[u]
                    continue
                cpd = cpd_by_var[u]
                col = self._col_index(
                    cpd.evidence, cards, {e: cf[e] for e in cpd.evidence}
                )
                cf[u] = assign_g[u][col]
            for v in q:
                dist[v][cf[v]] = dist[v].get(cf[v], 0.0) + weight

        if total_consistent == 0.0:
            raise CausalEngineError(
                "The observation has probability 0 under the model; no "
                "counterfactual is defined for an impossible observation."
            )

        out: dict[str, dict[str, float]] = {}
        for v in q:
            states = self._by_uri[v].states
            out[v] = {
                states[i]: dist[v].get(i, 0.0) / total_consistent
                for i in range(len(states))
            }
        return out
