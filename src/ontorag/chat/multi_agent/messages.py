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
