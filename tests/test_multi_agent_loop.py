"""Unit tests for the v1.2 MultiAgentLoop orchestrator.

The inner ``AgentLoop`` is substituted via the ``agent_factory``
constructor seam with a deterministic fake that yields a scripted
event sequence. The store's ``get_schema`` is the only real surface
mocked here; everything else runs through the real router + evaluator
so the orchestration is exercised end-to-end without LLM / DB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ontorag.chat.multi_agent.loop import MultiAgentLoop, _make_followup_prompt
from ontorag.chat.multi_agent.messages import (
    EvaluationAxes,
    EvaluationDecision,
    SufficientContext,
)
from ontorag.stores.base import ClassSummary, SchemaResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _schema(*class_specs: tuple[str, str | None]) -> SchemaResult:
    classes = [
        ClassSummary(uri=uri, label=label, property_count=0)
        for uri, label in class_specs
    ]
    return SchemaResult(
        total_classes=len(classes),
        total_properties=0,
        namespaces={"ex": "http://example.org/"},
        classes=classes,
    )


def _mock_store(schema: SchemaResult) -> AsyncMock:
    store = AsyncMock()
    store.get_schema = AsyncMock(return_value=schema)
    return store


class FakeAgentLoop:
    """Stand-in for ``AgentLoop`` — yields a scripted event sequence.

    Each instance can be reused only once; the iteration list is
    consumed lazily so ``run()`` yields each event in order.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.received_message: str | None = None

    async def run(self, user_message: str) -> AsyncGenerator[dict[str, Any], None]:
        self.received_message = user_message
        for event in self._events:
            yield event


class ScriptedFactory:
    """Yields successive ``FakeAgentLoop`` instances from a list of scripts."""

    def __init__(self, scripts: list[list[dict[str, Any]]]) -> None:
        self._scripts = scripts
        self._index = 0
        self.created: list[FakeAgentLoop] = []

    def __call__(self) -> FakeAgentLoop:
        if self._index >= len(self._scripts):
            raise AssertionError("FakeAgentLoop factory exhausted")
        agent = FakeAgentLoop(self._scripts[self._index])
        self._index += 1
        self.created.append(agent)
        return agent


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRouteAndShortCircuit:
    """Route event is always emitted; SIMPLE skips the evaluator loop."""

    @pytest.mark.asyncio
    async def test_simple_route_forwards_agent_events(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        store = _mock_store(schema)
        factory = ScriptedFactory(
            [
                [
                    {"type": "text", "content": "Hello."},
                    {"type": "done"},
                ]
            ]
        )
        loop = MultiAgentLoop(
            store=store,
            llm=AsyncMock(),
            agent_factory=factory,
        )
        events = [e async for e in loop.run("hi")]
        types = [e["type"] for e in events]

        # First event is route
        assert events[0]["type"] == "route"
        assert events[0]["complexity"] == "simple"
        # Inner agent events (text) forwarded; final done from inner
        # passes through because SIMPLE doesn't suppress it.
        assert "text" in types
        assert types[-1] == "done"
        # No evaluator events
        assert "evaluate" not in types
        assert "iteration" not in types

    @pytest.mark.asyncio
    async def test_route_event_carries_router_evidence(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        store = _mock_store(schema)
        factory = ScriptedFactory([[{"type": "done"}]])
        loop = MultiAgentLoop(
            store=store, llm=AsyncMock(), agent_factory=factory
        )
        # Unrelated question → SIMPLE → short-circuit, only one inner agent
        events = [e async for e in loop.run("hi there")]
        route_event = events[0]
        assert route_event["type"] == "route"
        assert route_event["complexity"] == "simple"
        assert "matched_classes" in route_event
        assert "hop_signals" in route_event
        assert "reasoning_signals" in route_event


class TestEvaluatorLoop:
    """MULTI_STEP routes engage the evaluator-optimizer loop."""

    @pytest.mark.asyncio
    async def test_sufficient_first_iteration_stops_immediately(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        store = _mock_store(schema)
        # Inner agent yields a tool_result that fully covers the
        # target class + a text answer that cites the entity.
        factory = ScriptedFactory(
            [
                [
                    {
                        "type": "tool_result",
                        "tool": "find_entities",
                        "content": {
                            "uri": "http://example.org/Pokemon",
                            "label": "Pikachu",
                        },
                    },
                    {"type": "text", "content": "Pikachu is a Pokemon."},
                    {"type": "done"},
                ]
            ]
        )
        loop = MultiAgentLoop(
            store=store,
            llm=AsyncMock(),
            agent_factory=factory,
        )
        # "Pokemon Type 비교" forces MULTI_STEP via the hop signal
        events = [e async for e in loop.run("Pokemon 비교해줘")]
        types = [e["type"] for e in events]

        assert types[0] == "route"
        # One iteration + one evaluate + final done
        assert types.count("iteration") == 1
        assert types.count("evaluate") == 1
        evaluate_event = next(e for e in events if e["type"] == "evaluate")
        assert evaluate_event["verdict"] == "sufficient"
        # Only one inner agent created
        assert len(factory.created) == 1
        # Final event is outer done (not inner)
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_insufficient_triggers_second_iteration(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        store = _mock_store(schema)
        # First iteration: no useful tool result → insufficient
        # Second iteration: full evidence → sufficient
        factory = ScriptedFactory(
            [
                [
                    {"type": "text", "content": "I don't know."},
                    {"type": "done"},
                ],
                [
                    {
                        "type": "tool_result",
                        "tool": "find_entities",
                        "content": {
                            "uri": "http://example.org/Pokemon",
                            "label": "Pikachu",
                        },
                    },
                    {"type": "text", "content": "Pikachu is a Pokemon found."},
                    {"type": "done"},
                ],
            ]
        )
        loop = MultiAgentLoop(
            store=store,
            llm=AsyncMock(),
            agent_factory=factory,
            max_iterations=3,
        )
        events = [e async for e in loop.run("Pokemon 비교")]
        types = [e["type"] for e in events]

        assert types.count("iteration") == 2
        assert types.count("evaluate") == 2
        # Two agents created
        assert len(factory.created) == 2
        # Second iteration's prompt should include the follow-up hint
        second_prompt = factory.created[1].received_message
        assert second_prompt is not None
        assert "[추가 안내]" in second_prompt
        # Final outer done
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_exhausts_max_iterations_when_never_sufficient(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        store = _mock_store(schema)
        # Every iteration yields evidence-poor results → never sufficient
        empty_script = [
            {"type": "text", "content": "nope"},
            {"type": "done"},
        ]
        factory = ScriptedFactory([empty_script, empty_script, empty_script])
        loop = MultiAgentLoop(
            store=store,
            llm=AsyncMock(),
            agent_factory=factory,
            max_iterations=3,
        )
        events = [e async for e in loop.run("Pokemon 비교")]
        types = [e["type"] for e in events]
        assert types.count("iteration") == 3
        assert types.count("evaluate") == 3
        # Final state was insufficient, but loop still ends gracefully
        assert types[-1] == "done"
        # Verdicts may be insufficient or ambiguous, never sufficient
        for e in events:
            if e["type"] == "evaluate":
                assert e["verdict"] != "sufficient"


class TestInnerEventForwarding:
    """All inner event types must pass through unchanged."""

    @pytest.mark.asyncio
    async def test_text_and_tool_events_forwarded(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        store = _mock_store(schema)
        factory = ScriptedFactory(
            [
                [
                    {"type": "thinking", "content": "..."},
                    {"type": "tool_call", "tool": "find_entities"},
                    {
                        "type": "tool_result",
                        "tool": "find_entities",
                        "content": {
                            "uri": "http://example.org/Pokemon",
                            "label": "Pikachu",
                        },
                    },
                    {"type": "text", "content": "Pikachu, a Pokemon."},
                    {"type": "done"},
                ]
            ]
        )
        loop = MultiAgentLoop(
            store=store, llm=AsyncMock(), agent_factory=factory
        )
        events = [e async for e in loop.run("Pokemon 비교")]
        types = [e["type"] for e in events]
        # Every inner type was passed through (except the inner 'done')
        assert "thinking" in types
        assert "tool_call" in types
        assert "tool_result" in types
        assert "text" in types
        # Only one 'done' at the very end
        assert types.count("done") == 1

    @pytest.mark.asyncio
    async def test_inner_done_suppressed_in_multi_iteration(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        store = _mock_store(schema)
        empty_script = [{"type": "text", "content": ""}, {"type": "done"}]
        factory = ScriptedFactory([empty_script, empty_script])
        loop = MultiAgentLoop(
            store=store,
            llm=AsyncMock(),
            agent_factory=factory,
            max_iterations=2,
        )
        events = [e async for e in loop.run("Pokemon 비교")]
        # Even though each inner script yields a done, only ONE makes it out
        assert sum(1 for e in events if e["type"] == "done") == 1


class TestMaxIterationClamp:
    """Constructor clamps max_iterations to a safe range."""

    def test_clamps_to_at_least_one(self) -> None:
        loop = MultiAgentLoop(
            store=AsyncMock(),
            llm=AsyncMock(),
            max_iterations=0,
            agent_factory=lambda: FakeAgentLoop([]),
        )
        assert loop._max_iterations == 1

    def test_clamps_to_hard_max(self) -> None:
        loop = MultiAgentLoop(
            store=AsyncMock(),
            llm=AsyncMock(),
            max_iterations=1000,
            agent_factory=lambda: FakeAgentLoop([]),
        )
        assert loop._max_iterations <= 6

    def test_v122_default_is_two(self) -> None:
        """v1.2.2 dropped the default from 3 to 2 — the v1.2 first run
        showed iter 3 rarely improved answer quality while regularly
        introducing ungrounded paraphrase. Guards against accidental
        revert.
        """
        loop = MultiAgentLoop(
            store=AsyncMock(),
            llm=AsyncMock(),
            agent_factory=lambda: FakeAgentLoop([]),
        )
        assert loop._max_iterations == 2
        assert MultiAgentLoop.DEFAULT_MAX_ITERATIONS == 2


class TestFollowupPrompt:
    """The follow-up prompt helper composes the right hint."""

    def test_no_decision_returns_original(self) -> None:
        out = _make_followup_prompt("question", None, "prev")
        assert out == "question"

    def test_insufficient_hint(self) -> None:
        decision = EvaluationDecision(
            axes=EvaluationAxes(is_rel=0.1, is_use=0.5),
            verdict=SufficientContext.INSUFFICIENT,
            rationale="x",
        )
        out = _make_followup_prompt("question", decision, "prev")
        assert "관련도가 낮습니다" in out
        assert "이전 답변 초안: prev" in out

    def test_ambiguous_hint(self) -> None:
        decision = EvaluationDecision(
            axes=EvaluationAxes(is_rel=0.5, is_use=0.5),
            verdict=SufficientContext.AMBIGUOUS,
            rationale="x",
        )
        out = _make_followup_prompt("question", decision, "prev")
        assert "evidence가 일부 부족합니다" in out

    def test_prev_answer_snippet_truncated(self) -> None:
        decision = EvaluationDecision(
            axes=EvaluationAxes(is_rel=0.5, is_use=0.5),
            verdict=SufficientContext.AMBIGUOUS,
            rationale="x",
        )
        long_prev = "x" * 2000
        out = _make_followup_prompt("question", decision, long_prev)
        # Snippet bounded to 500 chars; total prompt should not contain 2000 x's
        assert out.count("x") < 1000

    def test_quote_anchored_fires_when_isuse_low(self) -> None:
        """v1.2-experimental — IsUse < 0.5 triggers grounding requirement.

        Implements the Self-RAG IsSup-gate idea adapted for no-BN domains:
        when the candidate answer doesn't cite the retrieved evidence
        (low IsUse), the next iteration's prompt forces every claim to
        carry a source URI/label, banning ungrounded paraphrase.
        """
        decision = EvaluationDecision(
            axes=EvaluationAxes(is_rel=1.0, is_use=0.3),
            verdict=SufficientContext.AMBIGUOUS,
            rationale="x",
        )
        out = _make_followup_prompt("question", decision, "prev")
        assert "[근거 의무]" in out
        assert "출처" in out
        assert "근거 없음" in out  # explicit fallback when no evidence found

    def test_quote_anchored_skipped_when_isuse_high(self) -> None:
        """High IsUse means the answer already cites evidence — no extra hint."""
        decision = EvaluationDecision(
            axes=EvaluationAxes(is_rel=1.0, is_use=0.8),
            verdict=SufficientContext.AMBIGUOUS,
            rationale="x",
        )
        out = _make_followup_prompt("question", decision, "prev")
        assert "[근거 의무]" not in out
        # Base verdict hint still present
        assert "[추가 안내]" in out

    def test_quote_anchored_at_boundary(self) -> None:
        """IsUse exactly at the threshold (0.5) does NOT fire — strict <."""
        decision = EvaluationDecision(
            axes=EvaluationAxes(is_rel=1.0, is_use=0.5),
            verdict=SufficientContext.AMBIGUOUS,
            rationale="x",
        )
        out = _make_followup_prompt("question", decision, "prev")
        assert "[근거 의무]" not in out
