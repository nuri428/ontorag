"""Shared data types for the v1.2 multi-agent loop.

Frozen dataclasses are used so router / evaluator / loop can hand
decisions to each other without worrying about mutation.

Design — three complexity tiers borrowed from Adaptive-RAG (Jeong et
al., NAACL 2024), but the classifier itself is TBox-aware heuristic
rather than a learned model. See :mod:`ontorag.chat.multi_agent.router`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Complexity(str, Enum):
    """Question complexity tier as decided by the router.

    Values:
        SIMPLE: No retrieval needed, or a single direct lookup. The
            response can come from a single ``get_class_detail`` /
            ``describe_entity`` call. Stays in the existing single-agent
            loop with minimal turns.
        SINGLE_STEP: Standard tool-use loop. One TBox class is involved
            but the answer needs a couple of tool calls. Existing
            ``AgentLoop`` handles this well — no evaluator needed.
        MULTI_STEP: Multiple TBox classes OR an explicit reasoning
            signal (probability / causal / counterfactual). Activates
            the evaluator-optimizer loop with iterative sufficient-context
            checking.
    """

    SIMPLE = "simple"
    SINGLE_STEP = "single_step"
    MULTI_STEP = "multi_step"


@dataclass(frozen=True)
class RouteDecision:
    """Output of the complexity router.

    Attributes:
        complexity: Chosen tier — drives whether the multi-agent loop
            activates (``MULTI_STEP``) or the request short-circuits to
            the existing single-agent ``AgentLoop``.
        rationale: Short human-readable explanation, surfaced in the
            SSE ``route`` event so users can see why a tier was picked.
        matched_classes: TBox class local-names mentioned in the
            question (post-normalisation). Empty when no class matched.
        hop_signals: Multi-hop linguistic markers detected (e.g.
            ``"비교"``, ``"top 5"``, conjunctions). Empty if none.
        reasoning_signals: Probabilistic / causal / counterfactual
            markers (e.g. ``"확률"``, ``"만약"``, ``"intervene"``).
    """

    complexity: Complexity
    rationale: str
    matched_classes: tuple[str, ...] = field(default_factory=tuple)
    hop_signals: tuple[str, ...] = field(default_factory=tuple)
    reasoning_signals: tuple[str, ...] = field(default_factory=tuple)


class SufficientContext(str, Enum):
    """CRAG-style three-way branching verdict from the evaluator.

    Values:
        SUFFICIENT: All available axes are above the high threshold. The
            candidate answer should be returned without further looping.
        AMBIGUOUS: Mixed signal — at least one axis is in the middle
            band. Trigger one more search/tool-call round and re-evaluate.
        INSUFFICIENT: At least one axis is below the low threshold. The
            current search direction is unproductive — re-plan with a
            different tool path or different ontology scope.
    """

    SUFFICIENT = "sufficient"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class EvaluationAxes:
    """Three reflection axes inspired by Self-RAG (Asai et al., NAACL 2024).

    Self-RAG used learned tokens (`[IsRel]`, `[IsSup]`, `[IsUse]`) emitted
    by a fine-tuned base model. Here the same three dimensions are scored
    deterministically against the OWL schema and the active BN — no
    additional training required.

    Attributes:
        is_rel: Relevance score in ``[0, 1]`` — how well the tool results
            cover the TBox classes the question is about.
        is_use: Utility score in ``[0, 1]`` — how much of the retrieved
            evidence actually surfaces in the candidate answer (citation
            completeness).
        is_sup: Support score in ``[0, 1]`` or ``None``. Measures the
            posterior entropy reduction from the latest tool calls when
            a Bayesian network is active; ``None`` when no BN is in play.
    """

    is_rel: float
    is_use: float
    is_sup: float | None = None


@dataclass(frozen=True)
class EvaluationDecision:
    """Combined evaluator output handed to the multi-agent loop."""

    axes: EvaluationAxes
    verdict: SufficientContext
    rationale: str
    matched_classes_in_results: tuple[str, ...] = field(default_factory=tuple)
    cited_uris: tuple[str, ...] = field(default_factory=tuple)
