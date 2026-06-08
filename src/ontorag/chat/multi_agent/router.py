"""TBox-aware complexity router for the v1.2 multi-agent loop.

Inspired by Adaptive-RAG (Jeong et al., NAACL 2024) — classify a
question into ``SIMPLE / SINGLE_STEP / MULTI_STEP`` so the heavy
evaluator-optimizer loop only fires when it pays off.

Implementation note — Adaptive-RAG learns a small LM classifier from
noisy labels. Here we use a heuristic that combines:

* **TBox class matches** — how many distinct classes from the current
  schema the question mentions (multi-hop signal).
* **Linguistic hop markers** — Korean and English markers for
  conjunction / comparison / ranking / chaining.
* **Reasoning markers** — Korean and English markers for
  probability / cause / intervention / counterfactual.

The heuristic is intentionally cheap (regex + substring) so the router
runs in microseconds. A trained classifier can replace this later
without changing the protocol.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from ontorag.chat.multi_agent.messages import Complexity, RouteDecision
from ontorag.stores.base import SchemaResult

logger = logging.getLogger(__name__)


# Korean + English multi-hop indicators. Patterns are anchored with
# word boundaries where they apply to single English words; Korean
# patterns rely on the substring being distinctive enough.
#
# The Korean set was tuned against the v1.2 multi-hop goldset
# (examples/pokemon/goldset_multihop.jsonl) — the v1.2 first-run
# benchmark showed 66.7% SIMPLE on questions that were *designed* to
# be multi-hop, because the router had no signal for Korean grouping,
# threshold, inverse, superlative, or two-stage compound phrasing.
_HOP_PATTERNS: tuple[str, ...] = (
    # ── English (kept as-is) ──
    r"\band\b",
    r"first .{1,30} then",
    r"compared? (?:to|with)",
    r"top \d+",
    r"chain",
    r"path",
    r"vs\.?",
    # ── Korean (existing) ──
    r"그리고",
    r"먼저 .{1,30} 다음",
    r"이후",
    r"비교",
    r"대비",
    r"상위 \d+",
    r"경로",
    # ── Korean grouping / per-X aggregation (v1.2.1) ──
    r"타입별|유형별|종류별|지역별|클래스별",
    r"각각",
    # ── Korean threshold comparison (v1.2.1) ──
    r"\d+\s*(?:이상|이하|초과|미만)",
    # ── Korean equality / shared-attribute join (v1.2.1) ──
    r"(?:과|와)\s*같은",
    # ── Korean superlative (v1.2.1) ──
    r"가장\s*(?:많은|적은|흔한|큰|작은|높은|낮은|긴|짧은|빠른|느린|강한|약한)",
    # ── Korean completeness / exhaustive (v1.2.1) ──
    r"모두\s*(?:알려|보여|찾|나열)",
    # ── Korean existential cross-class (v1.2.1) ──
    r"한\s*\S{1,3}\s*라도|하나라도",
    # ── Korean inverse direction (v1.2.1) ──
    r"역방향|반대로|반대\s*방향",
    # ── Korean set inclusion (v1.2.1) ──
    r"포함",
    # ── Korean two-stage compound question (v1.2.1) ──
    # "X이고, 그 X" / "X이며, 그 X" — second clause depends on first
    r"(?:이고|이며)[\s,]+그\s*\S",
    # ── Korean relational origin (v1.2.1) ──
    r"출신",
)
_HOP_RE = re.compile("|".join(_HOP_PATTERNS), re.IGNORECASE)


# Probabilistic / causal / counterfactual reasoning indicators.
_REASONING_PATTERNS: tuple[str, ...] = (
    r"if .{1,40} (?:then|would)",
    r"만약",
    r"\b다면",
    r"probability",
    r"확률",
    r"가능성",
    r"cause(?:d|s)?",
    r"\b원인",
    r"because",
    r"때문",
    r"intervene",
    r"개입",
    r"counterfactual",
    r"반사실",
    r"posterior",
    r"prior probability",
    r"do\(",  # do-calculus notation
)
_REASONING_RE = re.compile("|".join(_REASONING_PATTERNS), re.IGNORECASE)


# Minimum class-name length to consider for substring matching. Avoids
# matching short generic tokens that happen to coincide with English
# words (e.g. a class called "Has").
_MIN_CLASS_NAME_LEN = 3

# How many TBox classes mentioned together trigger MULTI_STEP.
_DEFAULT_MULTI_HOP_THRESHOLD = 2


def route(
    question: str,
    schema: SchemaResult,
    *,
    multi_hop_threshold: int = _DEFAULT_MULTI_HOP_THRESHOLD,
) -> RouteDecision:
    """Classify question complexity.

    Decision order — first match wins:

    1. **Reasoning markers present** → ``MULTI_STEP``. Probabilistic and
       causal questions always benefit from the evaluator loop because
       the answer depends on a numeric quantity (posterior, intervention
       effect) where over-/under-confidence matters.
    2. **≥ ``multi_hop_threshold`` TBox classes OR any hop marker** →
       ``MULTI_STEP``.
    3. **One TBox class match** → ``SINGLE_STEP``.
    4. **No match, no signal** → ``SIMPLE``.

    Args:
        question: Natural-language question. Korean and English are both
            handled by the same heuristic.
        schema: Current TBox snapshot (from ``store.get_schema()``).
        multi_hop_threshold: Minimum TBox class count that promotes a
            question to ``MULTI_STEP``. Default 2.

    Returns:
        A :class:`RouteDecision` carrying the tier and the evidence
        that drove it (matched classes, signals). The rationale is
        surfaced as part of the SSE ``route`` event.
    """
    matched = _match_tbox_classes(question, schema.classes)
    hops = _find_signals(question, _HOP_RE)
    reasoning = _find_signals(question, _REASONING_RE)

    if reasoning:
        decision = RouteDecision(
            complexity=Complexity.MULTI_STEP,
            rationale=f"reasoning signal: {reasoning[0]!r}",
            matched_classes=tuple(matched),
            reasoning_signals=tuple(reasoning),
        )
    elif len(matched) >= multi_hop_threshold or hops:
        rationale_parts: list[str] = []
        if len(matched) >= multi_hop_threshold:
            rationale_parts.append(f"{len(matched)} TBox classes")
        if hops:
            rationale_parts.append(f"hop signal: {hops[0]!r}")
        decision = RouteDecision(
            complexity=Complexity.MULTI_STEP,
            rationale=" + ".join(rationale_parts),
            matched_classes=tuple(matched),
            hop_signals=tuple(hops),
        )
    elif matched:
        decision = RouteDecision(
            complexity=Complexity.SINGLE_STEP,
            rationale=f"single TBox class: {matched[0]}",
            matched_classes=tuple(matched),
        )
    else:
        decision = RouteDecision(
            complexity=Complexity.SIMPLE,
            rationale="no TBox class match, no signal",
        )

    logger.debug(
        "route: complexity=%s rationale=%s",
        decision.complexity.value,
        decision.rationale,
    )
    return decision


def _match_tbox_classes(
    question: str,
    classes: Iterable[object],
) -> list[str]:
    """Return ordered, de-duplicated TBox class local-names mentioned.

    Both URI local-name and ``rdfs:label`` are candidates. Matching is
    case-insensitive substring; the candidate must be at least
    :data:`_MIN_CLASS_NAME_LEN` characters to avoid false positives on
    generic short tokens.
    """
    q = question.lower()
    seen: set[str] = set()
    matched: list[str] = []
    for cls in classes:
        uri = getattr(cls, "uri", None)
        if not uri:
            continue
        local = _local_name(uri)
        label = getattr(cls, "label", None)
        for candidate in (local, label):
            if candidate and len(candidate) >= _MIN_CLASS_NAME_LEN:
                if candidate.lower() in q:
                    if local not in seen:
                        seen.add(local)
                        matched.append(local)
                    break
    return matched


def _find_signals(question: str, pattern: re.Pattern[str]) -> list[str]:
    """Return all distinct surface forms matched by ``pattern``."""
    seen: set[str] = set()
    out: list[str] = []
    for m in pattern.finditer(question):
        s = m.group(0).lower()
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _local_name(uri: str) -> str:
    """Return the fragment after the last ``#`` or ``/`` in ``uri``."""
    for sep in ("#", "/"):
        if sep in uri:
            return uri.rsplit(sep, 1)[-1]
    return uri
