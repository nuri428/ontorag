"""v1.2 multi-agent loop — evaluator-optimizer pattern over the
existing :class:`~ontorag.chat.agent.AgentLoop`.

The pieces synthesise three RAG-specific papers without inheriting any
of their training-time dependencies:

* **Adaptive-RAG** (Jeong et al., NAACL 2024) — complexity routing.
  Implemented as a TBox-aware heuristic in :mod:`.router`.
* **Self-RAG** (Asai et al., NAACL 2024) — 3-axis reflection
  (relevance / support / utility). Implemented without learned tokens
  in :mod:`.evaluator` (Phase 2).
* **CRAG** (Yan et al., ICLR 2024) — sufficient / ambiguous /
  insufficient branching. Implemented in :mod:`.loop` (Phase 3).

The whole module is opt-in via ``AGENT_MODE=multi`` (see
:mod:`ontorag.chat.selector`); ``AGENT_MODE=single`` (the default)
keeps the existing single-agent path untouched.
"""

from __future__ import annotations

from ontorag.chat.multi_agent.evaluator import (
    Evaluator,
    compute_is_rel,
    compute_is_use,
    decide,
)
from ontorag.chat.multi_agent.messages import (
    Complexity,
    EvaluationAxes,
    EvaluationDecision,
    RouteDecision,
    SufficientContext,
)
from ontorag.chat.multi_agent.router import route

__all__ = [
    "Complexity",
    "EvaluationAxes",
    "EvaluationDecision",
    "Evaluator",
    "RouteDecision",
    "SufficientContext",
    "compute_is_rel",
    "compute_is_use",
    "decide",
    "route",
]
