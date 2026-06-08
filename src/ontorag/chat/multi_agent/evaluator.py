"""3-axis sufficient-context evaluator (Self-RAG-inspired, learning-free).

The evaluator answers a single question after each round of tool calls:
*"Do we have enough to answer well, or should we keep looking?"*

It scores three axes, then collapses them into a CRAG-style verdict
(``SUFFICIENT`` / ``AMBIGUOUS`` / ``INSUFFICIENT``) that the multi-agent
loop (Phase 3) uses to decide whether to respond, iterate, or re-plan.

Axes:

* **IsRel — Relevance.** How much of the question's TBox-class
  intention is covered by the tool results so far. Computed
  deterministically by matching tool-result URIs against the schema's
  class list (route_decision's matched_classes act as the target set).
* **IsUse — Utility / Citation completeness.** Whether the candidate
  answer actually surfaces the retrieved evidence. Heuristic — count
  how many tool-result local-names and labels appear in the candidate
  answer text, saturated at ~5 cited entities.
* **IsSup — Support (optional).** Posterior entropy reduction over
  one or more query variables when a Bayesian network is active. ``None``
  when no BN engine / no query variables are supplied; in that case
  the verdict uses only IsRel + IsUse.

All three axes are normalised to ``[0, 1]``. The CRAG branching
thresholds (``_T_SUFFICIENT`` = 0.7, ``_T_INSUFFICIENT`` = 0.3) lean
toward looping once more rather than under-answering — ontorag prefers
honest completeness over false confidence.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Any

from ontorag.chat.multi_agent.messages import (
    EvaluationAxes,
    EvaluationDecision,
    RouteDecision,
    SufficientContext,
)
from ontorag.stores.base import SchemaResult

logger = logging.getLogger(__name__)


# CRAG-style branching thresholds.
#
# v1.2 first-run diagnostic on the multi-hop pokemon goldset showed
# 0/15 SUFFICIENT verdicts with _T_SUFFICIENT=0.7 — the loop hit max
# iterations on every MULTI_STEP question, and each forced extra
# iteration added ungrounded paraphrase that dragged RAGAS faithfulness
# down by -0.174 against the single-agent baseline. The threshold was
# unreachable in the real domain.
#
# v1.2.1 — lowered _T_SUFFICIENT to 0.6 so SUFFICIENT is reachable on
# realistically-grounded answers. _T_INSUFFICIENT stays at 0.3; the
# middle band (ambiguous) is narrowed by 0.1 rather than widened, so
# the loop still iterates when an axis is actually weak.
_T_SUFFICIENT = 0.6
_T_INSUFFICIENT = 0.3

# IsUse saturation point. Most well-grounded answers cite ~3–5 entities;
# beyond that, more citations don't improve trustworthiness.
_IS_USE_SATURATION = 5

# Minimum local-name length to count as a "named" entity match. Avoids
# matching very short generic tokens.
_MIN_NAME_LEN = 3


def compute_is_rel(
    tool_results: Iterable[dict[str, Any]],
    route_decision: RouteDecision,
    schema: SchemaResult,
) -> tuple[float, tuple[str, ...]]:
    """Score relevance: target TBox classes ↔ tool-result classes.

    Strategy: for each tool result we (a) walk the content for entity URIs
    that *are* TBox classes, and (b) inspect the tool's args for keys like
    ``class_uri`` that point at a class. Coverage = matched / target.

    Args:
        tool_results: List of records with at least a ``content`` field
            (the raw tool output) and optionally ``args``.
        route_decision: From the Phase 1 router — supplies the target set
            of class local-names mentioned in the question.
        schema: Current TBox snapshot, used to recognise class URIs.

    Returns:
        ``(score, matched_classes_in_results)``. When the question matched
        no TBox class, returns ``(1.0, ())`` — the relevance dimension
        does not apply, so we don't penalise.
    """
    target = set(route_decision.matched_classes)
    if not target:
        return 1.0, ()

    class_uri_to_local = {cls.uri: _local_name(cls.uri) for cls in schema.classes}

    seen: set[str] = set()
    for result in tool_results:
        content = result.get("content")
        uris: list[str] = []
        _collect_uris(content, uris)
        for uri in uris:
            local = class_uri_to_local.get(uri)
            if local and local in target:
                seen.add(local)
        args = result.get("args")
        if isinstance(args, dict):
            for key, value in args.items():
                if "class" in key.lower() and isinstance(value, str):
                    local = _local_name(value)
                    if local in target:
                        seen.add(local)

    coverage = len(seen) / len(target)
    return coverage, tuple(sorted(seen))


def compute_is_use(
    candidate_answer: str,
    tool_results: Iterable[dict[str, Any]],
) -> tuple[float, tuple[str, ...]]:
    """Score utility: how much retrieved evidence the answer actually cites.

    Heuristic — count distinct entity URIs / labels from tool results
    whose local-name or label substring appears in the candidate answer
    (case-insensitive). Saturates at :data:`_IS_USE_SATURATION` entities
    so a wall of citations doesn't dominate the score.

    Args:
        candidate_answer: The LLM's response text after the last tool round.
        tool_results: Same shape as :func:`compute_is_rel`.

    Returns:
        ``(score, cited_uris_or_labels)``. ``score = 0.5`` when there is
        no evidence to cite — a neutral signal rather than a penalty.
    """
    uris: list[str] = []
    labels: list[str] = []
    for result in tool_results:
        content = result.get("content")
        _collect_uris(content, uris)
        _collect_labels(content, labels)

    distinct_uris = {u for u in uris if len(_local_name(u)) >= _MIN_NAME_LEN}
    distinct_labels = {label for label in labels if len(label) >= _MIN_NAME_LEN}

    if not distinct_uris and not distinct_labels:
        return 0.5, ()

    answer = candidate_answer.lower()
    cited: set[str] = set()
    for uri in distinct_uris:
        if _local_name(uri).lower() in answer:
            cited.add(uri)
    for label in distinct_labels:
        if label.lower() in answer:
            cited.add(label)

    n_target = min(_IS_USE_SATURATION, len(distinct_uris) + len(distinct_labels))
    if n_target == 0:
        return 0.5, ()
    score = min(1.0, len(cited) / n_target)
    return score, tuple(sorted(cited))


def decide(axes: EvaluationAxes) -> tuple[SufficientContext, str]:
    """Collapse the three axes into a CRAG-style verdict.

    Rule:

    * ``SUFFICIENT``  iff every *available* axis ≥ :data:`_T_SUFFICIENT`.
    * ``INSUFFICIENT`` iff *any* available axis < :data:`_T_INSUFFICIENT`.
    * ``AMBIGUOUS`` otherwise — the loop should iterate once more.
    """
    items: list[tuple[str, float]] = [
        ("rel", axes.is_rel),
        ("use", axes.is_use),
    ]
    if axes.is_sup is not None:
        items.append(("sup", axes.is_sup))

    summary = " ".join(f"{name}={score:.2f}" for name, score in items)

    if all(score >= _T_SUFFICIENT for _, score in items):
        return SufficientContext.SUFFICIENT, f"all axes ≥ {_T_SUFFICIENT}: {summary}"

    weak = [(n, s) for n, s in items if s < _T_INSUFFICIENT]
    if weak:
        weak_summary = " ".join(f"{n}={s:.2f}" for n, s in weak)
        return (
            SufficientContext.INSUFFICIENT,
            f"axis below {_T_INSUFFICIENT}: {weak_summary}",
        )

    return SufficientContext.AMBIGUOUS, f"middle band: {summary}"


class Evaluator:
    """Self-RAG-inspired 3-axis sufficient-context evaluator.

    The evaluator is stateless modulo the injected schema and (optional)
    Bayesian engine. Each call to :meth:`evaluate` scores the three axes
    against a single round's tool results and the candidate answer.
    """

    def __init__(
        self,
        schema: SchemaResult,
        bayes_engine: Any | None = None,
    ) -> None:
        """Construct an evaluator.

        Args:
            schema: Current TBox snapshot — used for IsRel.
            bayes_engine: Optional ``BayesianEngine`` instance. When
                provided AND ``bn_query`` / ``bn_evidence_*`` arguments
                are passed to :meth:`evaluate`, IsSup is computed.
        """
        self._schema = schema
        self._bayes = bayes_engine

    async def evaluate(
        self,
        question: str,
        tool_results: list[dict[str, Any]],
        candidate_answer: str,
        route_decision: RouteDecision,
        *,
        bn_query: list[str] | None = None,
        bn_evidence_before: dict[str, str] | None = None,
        bn_evidence_after: dict[str, str] | None = None,
    ) -> EvaluationDecision:
        """Score the three axes and emit a verdict.

        Args:
            question: Original natural-language question (for logging).
            tool_results: Records of the latest tool round.
            candidate_answer: The LLM's response after the latest round.
            route_decision: Phase 1 router output.
            bn_query: List of BN query-variable names (URI or label).
            bn_evidence_before: BN evidence prior to the latest round.
            bn_evidence_after: BN evidence including the latest round.

        Returns:
            A frozen :class:`EvaluationDecision` ready to drive the
            Phase 3 loop branching.
        """
        rel, matched = compute_is_rel(tool_results, route_decision, self._schema)
        use, cited = compute_is_use(candidate_answer, tool_results)

        sup: float | None = None
        if (
            self._bayes is not None
            and bn_query
            and bn_evidence_before is not None
            and bn_evidence_after is not None
        ):
            sup = await self._entropy_reduction(
                bn_query, bn_evidence_before, bn_evidence_after
            )

        axes = EvaluationAxes(is_rel=rel, is_use=use, is_sup=sup)
        verdict, rationale = decide(axes)

        logger.debug(
            "evaluate: verdict=%s rel=%.2f use=%.2f sup=%s",
            verdict.value,
            rel,
            use,
            f"{sup:.2f}" if sup is not None else "n/a",
        )

        return EvaluationDecision(
            axes=axes,
            verdict=verdict,
            rationale=rationale,
            matched_classes_in_results=matched,
            cited_uris=cited,
        )

    async def _entropy_reduction(
        self,
        query_vars: list[str],
        evidence_before: dict[str, str],
        evidence_after: dict[str, str],
    ) -> float:
        """Normalised entropy reduction from before → after evidence.

        Returns ``1 - H_after / H_before``, clamped to ``[0, 1]``. When
        ``H_before == 0`` (already certain), returns 1.0.
        """
        h_before = await self._sum_entropy(query_vars, evidence_before)
        h_after = await self._sum_entropy(query_vars, evidence_after)
        if h_before <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - h_after / h_before))

    async def _sum_entropy(
        self,
        query_vars: list[str],
        evidence: dict[str, str],
    ) -> float:
        """Sum of Shannon entropies (bits) over the query variable marginals."""
        assert self._bayes is not None  # caller-guaranteed
        result = await self._bayes.compute_posterior(evidence, query_vars)
        total = 0.0
        for dist in result.values():
            for prob in dist.values():
                if prob > 0:
                    total -= prob * math.log2(prob)
        return total


def _collect_uris(value: Any, into: list[str]) -> None:
    """Walk an arbitrary tool-result value and append entity URIs.

    Mirrors the logic in ``ontorag.eval.baselines.ontorag_native`` so
    eval and runtime IsRel see the same URIs. Recognises HTTP(S) and
    ``urn:`` URIs in standard ontorag keys.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"uri", "start_uri", "end_uri", "s", "o", "source", "target"}:
                if isinstance(item, str) and item.startswith(
                    ("http://", "https://", "urn:")
                ):
                    into.append(item)
            else:
                _collect_uris(item, into)
    elif isinstance(value, list):
        for item in value:
            _collect_uris(item, into)


def _collect_labels(value: Any, into: list[str]) -> None:
    """Walk a tool-result value and append label-like string values."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"label", "name", "title"} and isinstance(item, str):
                into.append(item)
            else:
                _collect_labels(item, into)
    elif isinstance(value, list):
        for item in value:
            _collect_labels(item, into)


def _local_name(uri: str) -> str:
    """Return the fragment after the last ``#`` or ``/`` in ``uri``."""
    for sep in ("#", "/"):
        if sep in uri:
            return uri.rsplit(sep, 1)[-1]
    return uri
