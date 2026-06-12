"""MultiAgentLoop — v1.2 evaluator-optimizer wrapper over AgentLoop.

This is the orchestrator that glues Phase 1 (router) and Phase 2
(evaluator) on top of the existing :class:`~ontorag.chat.agent.AgentLoop`.

Per request:

1. Fetch the current schema and ask the router for a complexity tier.
2. If ``SIMPLE`` → forward straight to a single ``AgentLoop`` and exit.
   (Adaptive-RAG's "cost 0 for easy questions" guarantee.)
3. Otherwise, run an evaluator-optimizer loop:
   * one inner ``AgentLoop`` per iteration,
   * collect its ``tool_result`` and ``text`` events,
   * after each iteration ask the evaluator for a CRAG verdict
     (``SUFFICIENT`` / ``AMBIGUOUS`` / ``INSUFFICIENT``),
   * stop on ``SUFFICIENT`` or when the iteration budget runs out.

All inner SSE events pass through to the caller unchanged so the
existing chat UI keeps working; three new event types are interleaved
to expose the orchestration:

* ``route`` — router decision (once, at the start).
* ``iteration`` — emitted at the head of every inner iteration.
* ``evaluate`` — emitted after each iteration with the verdict and the
  three reflection-axis scores.

The wrapper is opt-in via :mod:`ontorag.chat.selector` (Phase 4); the
default ``AGENT_MODE=single`` path stays on the unchanged
``AgentLoop``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any

from ontorag.chat.agent import AgentLoop
from ontorag.chat.multi_agent.evaluator import Evaluator
from ontorag.chat.multi_agent.messages import (
    Complexity,
    EvaluationDecision,
    SufficientContext,
)
from ontorag.chat.multi_agent.router import route
from ontorag.llm.factory import LLMProvider
from ontorag.stores.base import GraphStore

logger = logging.getLogger(__name__)


# Default number of evaluator-optimizer iterations.
#
# v1.2 set this to 3. The first-run diagnostic showed avg iter = 3.00
# (= max) on every MULTI_STEP question, and the forced 3rd iteration
# regularly added ungrounded paraphrase that dragged RAGAS faithfulness
# down by -0.174 vs native. The v1.2.1 run with router expansion
# brought avg iter down to 2.40, and the 3-vs-2-iter difference rarely
# improved answer quality — questions that don't reach SUFFICIENT by
# iter 2 generally don't reach it at all.
#
# v1.2.2 reduces the default to 2. Combined with reverting the
# evaluator threshold back to 0.7 (see evaluator.py), this preserves
# the v1.2.1 router gains (faithfulness / citation / correctness)
# while removing both pathologies — the forced 3rd-iter paraphrase
# and the premature-SUFFICIENT relevancy regression.
_DEFAULT_MAX_ITERATIONS = 2

# Hard ceiling regardless of constructor argument. Defensive against
# pathological loop configurations slipping into production.
_HARD_MAX_ITERATIONS = 6

# Truncation length for "previous answer" snippets included in the
# follow-up prompt — keeps prompt size bounded across iterations.
_PREV_ANSWER_SNIPPET = 500

# IsUse threshold below which the follow-up prompt switches to a
# quote-anchored grounding-required format. Self-RAG-inspired
# behaviour change: when the candidate answer doesn't actually cite
# the retrieved evidence (low IsUse), the next iteration's prompt
# *requires* every claim to be paired with a specific entity URI or
# label from the tool results. Bounded experiment in the no-BN domain
# where IsSup is unavailable and IsUse is the sole grounding proxy.
#
# NOTE: must stay <= evaluator._IS_USE_NO_EVIDENCE (0.5) — the strict
# `<` below exempts zero-evidence iterations, where compute_is_use
# returns exactly that neutral score and demanding citations of
# nonexistent evidence would be wrong.
_T_QUOTE_ANCHORED_TRIGGER = 0.5


# Type alias for the agent-factory dependency injection seam used by
# tests to substitute a deterministic fake AgentLoop.
AgentFactory = Callable[[], AgentLoop]


class MultiAgentLoop:
    """Evaluator-optimizer wrapper that adds Persistence to ``AgentLoop``.

    The wrapper never modifies ``AgentLoop`` — it instantiates a fresh
    one per iteration and consumes its event stream. This isolates each
    iteration's prompt and history so a failing first round can't poison
    the second; the price is no cross-iteration tool cache, which is
    acceptable since iterations are gated by the evaluator.
    """

    DEFAULT_MAX_ITERATIONS = _DEFAULT_MAX_ITERATIONS

    def __init__(
        self,
        store: GraphStore,
        llm: LLMProvider,
        schema_context: str | None = None,
        initial_history: list[dict[str, Any]] | None = None,
        has_ontology_data: bool = False,
        bayes_engine: Any | None = None,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        """Construct the multi-agent loop.

        Args:
            store: Graph store passed through to inner ``AgentLoop``
                instances and used directly for ``get_schema()``.
            llm: LLM provider passed through.
            schema_context: Pre-formatted schema string for the inner
                agent's system prompt (mirrors ``AgentLoop`` semantics).
            initial_history: Reserved for future REPL support; currently
                unused — each iteration starts fresh.
            has_ontology_data: Forwarded to the inner ``AgentLoop`` so
                its forced-tool-use heuristic stays consistent.
            bayes_engine: Optional ``BayesianEngine`` — enables IsSup.
            max_iterations: Soft cap on evaluator iterations. Clamped
                to ``[1, _HARD_MAX_ITERATIONS]``.
            agent_factory: Optional dependency-injection seam — a
                zero-arg callable returning a fresh ``AgentLoop``. When
                ``None`` (default), the loop constructs ``AgentLoop``s
                from the other arguments. Tests use this to substitute
                a deterministic fake.
        """
        self._store = store
        self._llm = llm
        self._schema_context = schema_context
        self._initial_history = initial_history or []
        self._has_ontology_data = has_ontology_data
        self._bayes_engine = bayes_engine
        self._max_iterations = max(1, min(_HARD_MAX_ITERATIONS, max_iterations))
        self._agent_factory = agent_factory or self._default_agent_factory

    def _default_agent_factory(self) -> AgentLoop:
        return AgentLoop(
            store=self._store,
            llm=self._llm,
            schema_context=self._schema_context,
            has_ontology_data=self._has_ontology_data,
        )

    async def run(
        self, user_message: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run one user turn and yield SSE events until completion.

        The generator emits the same event vocabulary as ``AgentLoop``
        plus three new types — ``route``, ``iteration``, ``evaluate``.
        A single final ``done`` event closes the stream.
        """
        schema = await self._store.get_schema()
        route_decision = route(user_message, schema)
        yield {
            "type": "route",
            "complexity": route_decision.complexity.value,
            "rationale": route_decision.rationale,
            "matched_classes": list(route_decision.matched_classes),
            "hop_signals": list(route_decision.hop_signals),
            "reasoning_signals": list(route_decision.reasoning_signals),
        }

        if route_decision.complexity == Complexity.SIMPLE:
            # Adaptive-RAG short-circuit — no evaluator overhead.
            agent = self._agent_factory()
            async for event in agent.run(user_message):
                yield event
            return

        evaluator = Evaluator(schema=schema, bayes_engine=self._bayes_engine)
        accumulated_tool_results: list[dict[str, Any]] = []
        candidate_answer: str = ""
        last_decision: EvaluationDecision | None = None

        for i in range(self._max_iterations):
            yield {
                "type": "iteration",
                "iteration": i + 1,
                "max": self._max_iterations,
            }

            prompt = (
                user_message
                if i == 0
                else _make_followup_prompt(
                    user_message, last_decision, candidate_answer
                )
            )

            agent = self._agent_factory()
            iter_text: list[str] = []
            iter_tool_results: list[dict[str, Any]] = []

            async for event in agent.run(prompt):
                etype = event.get("type")
                if etype == "done":
                    # Suppress inner done events; emit one final done
                    # event after the outer loop terminates.
                    continue
                yield event
                if etype == "text":
                    iter_text.append(event.get("content", ""))
                elif etype == "tool_result":
                    iter_tool_results.append(
                        {
                            "tool": event.get("tool"),
                            "content": event.get("content"),
                        }
                    )

            candidate_answer = "".join(iter_text)
            accumulated_tool_results.extend(iter_tool_results)

            last_decision = await evaluator.evaluate(
                question=user_message,
                tool_results=accumulated_tool_results,
                candidate_answer=candidate_answer,
                route_decision=route_decision,
            )

            yield {
                "type": "evaluate",
                "verdict": last_decision.verdict.value,
                "axes": {
                    "rel": last_decision.axes.is_rel,
                    "use": last_decision.axes.is_use,
                    "sup": last_decision.axes.is_sup,
                },
                "rationale": last_decision.rationale,
            }

            if last_decision.verdict == SufficientContext.SUFFICIENT:
                break

        yield {"type": "done"}


def _make_followup_prompt(
    original: str,
    decision: EvaluationDecision | None,
    prev_answer: str,
) -> str:
    """Compose a follow-up prompt with a verdict-shaped hint.

    Three-layer prompt assembly:

    * **Verdict hint** — short directive depending on whether the last
      verdict was ``INSUFFICIENT`` (try different evidence) or
      ``AMBIGUOUS`` (gather more evidence).
    * **Quote-anchored grounding requirement** *(v1.2-experimental)* —
      injected only when the IsUse axis is below
      :data:`_T_QUOTE_ANCHORED_TRIGGER`. Forces the next iteration to
      pair every claim with a specific entity URI / label, banning
      ungrounded paraphrase. This is the Self-RAG IsSup gate adapted
      for the no-BN domain where IsUse is the support proxy.
    * **Previous answer snippet** — truncated to bound prompt growth.
    """
    if decision is None:
        return original

    if decision.verdict == SufficientContext.INSUFFICIENT:
        verdict_hint = (
            "이전 도구 호출 결과가 질문과 관련도가 낮습니다. "
            "다른 클래스 / 다른 도구 / 다른 URI로 접근해 새 evidence를 모아주세요."
        )
    else:  # AMBIGUOUS
        verdict_hint = (
            "이전 답변의 evidence가 일부 부족합니다. "
            "추가 도구 호출로 근거를 더 모아 답을 보강해주세요."
        )

    grounding_block = ""
    if decision.axes.is_use < _T_QUOTE_ANCHORED_TRIGGER:
        grounding_block = (
            "\n\n[근거 의무] 답변의 각 주장은 도구 호출 결과의 구체적 entity URI "
            "또는 label과 함께 제시하세요. 형식: '<주장> (출처: <URI 또는 label>)'. "
            "도구 결과로 뒷받침되지 않는 추정·일반 지식 기반 paraphrase는 금지. "
            "근거를 찾을 수 없으면 '근거 없음'이라고 명시하세요."
        )

    snippet = prev_answer[:_PREV_ANSWER_SNIPPET]
    return (
        f"{original}\n\n[추가 안내] {verdict_hint}"
        f"{grounding_block}\n이전 답변 초안: {snippet}"
    )
