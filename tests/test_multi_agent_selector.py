"""Unit tests for the v1.2 AGENT_MODE selector + multi-agent baseline.

Covers:

* :mod:`ontorag.chat.selector` env-var routing and constructor wiring.
* :class:`ontorag.eval.baselines.ontorag_multiagent.OntoragMultiagentBaseline`
  event-stream collection, focused on the new ``route`` / ``iteration``
  / ``evaluate`` events ending up in ``BaselineAnswer.extra``.

No live LLM or graph store is required — the inner
:class:`MultiAgentLoop` is fed a scripted fake :class:`AgentLoop` and
the store's ``get_schema`` is mocked.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from rdflib import Graph

from ontorag.chat.multi_agent.loop import MultiAgentLoop
from ontorag.chat.selector import (
    AGENT_MODE_MULTI,
    AGENT_MODE_SINGLE,
    get_agent_mode,
    make_chat_agent,
)
from ontorag.eval.baselines.ontorag_multiagent import OntoragMultiagentBaseline
from ontorag.stores.base import ClassSummary, SchemaResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def _schema(*specs: tuple[str, str | None]) -> SchemaResult:
    return SchemaResult(
        total_classes=len(specs),
        total_properties=0,
        namespaces={"ex": "http://example.org/"},
        classes=[
            ClassSummary(uri=uri, label=label, property_count=0)
            for uri, label in specs
        ],
    )


class FakeAgentLoop:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.received_message: str | None = None

    async def run(self, msg: str) -> AsyncGenerator[dict[str, Any], None]:
        self.received_message = msg
        for e in self._events:
            yield e


# ── Selector ──────────────────────────────────────────────────────────────────


class TestGetAgentMode:
    """Env-var lookup defaults and validation."""

    def test_unset_defaults_to_single(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MODE", None)
            assert get_agent_mode() == AGENT_MODE_SINGLE

    def test_single_value(self) -> None:
        with patch.dict(os.environ, {"AGENT_MODE": "single"}):
            assert get_agent_mode() == AGENT_MODE_SINGLE

    def test_multi_value(self) -> None:
        with patch.dict(os.environ, {"AGENT_MODE": "multi"}):
            assert get_agent_mode() == AGENT_MODE_MULTI

    def test_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"AGENT_MODE": "MULTI"}):
            assert get_agent_mode() == AGENT_MODE_MULTI

    def test_whitespace_tolerated(self) -> None:
        with patch.dict(os.environ, {"AGENT_MODE": "  multi  "}):
            assert get_agent_mode() == AGENT_MODE_MULTI

    def test_invalid_falls_back_to_single(self) -> None:
        with patch.dict(os.environ, {"AGENT_MODE": "swarm"}):
            assert get_agent_mode() == AGENT_MODE_SINGLE


class TestMakeChatAgent:
    """make_chat_agent dispatches to the right concrete class."""

    def test_default_returns_single(self) -> None:
        from ontorag.chat.agent import AgentLoop

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_MODE", None)
            agent = make_chat_agent(store=AsyncMock(), llm=AsyncMock())
            assert isinstance(agent, AgentLoop)

    def test_multi_returns_multi(self) -> None:
        with patch.dict(os.environ, {"AGENT_MODE": "multi"}):
            agent = make_chat_agent(store=AsyncMock(), llm=AsyncMock())
            assert isinstance(agent, MultiAgentLoop)

    def test_multi_forwards_max_iterations(self) -> None:
        with patch.dict(os.environ, {"AGENT_MODE": "multi"}):
            agent = make_chat_agent(
                store=AsyncMock(), llm=AsyncMock(), max_iterations=2
            )
            assert isinstance(agent, MultiAgentLoop)
            assert agent._max_iterations == 2


# ── Multi-agent baseline ──────────────────────────────────────────────────────


def _patched_baseline(
    scripts: list[list[dict[str, Any]]],
    schema: SchemaResult,
) -> tuple[OntoragMultiagentBaseline, list[FakeAgentLoop]]:
    """Build a baseline whose inner MultiAgentLoop uses a scripted factory."""
    store = AsyncMock()
    store.get_schema = AsyncMock(return_value=schema)
    store.aclose = AsyncMock()

    created: list[FakeAgentLoop] = []
    script_iter = iter(scripts)

    def factory() -> FakeAgentLoop:
        agent = FakeAgentLoop(next(script_iter))
        created.append(agent)
        return agent

    baseline = OntoragMultiagentBaseline(
        store=store,
        llm=AsyncMock(),
        graph=Graph(),
        schema_context="(schema-context)",
        has_ontology_data=True,
    )

    original_init = MultiAgentLoop.__init__

    def patched_init(self: MultiAgentLoop, **kwargs: Any) -> None:
        kwargs["agent_factory"] = factory
        original_init(self, **kwargs)

    baseline._patcher = patch.object(  # type: ignore[attr-defined]
        MultiAgentLoop, "__init__", patched_init
    )
    baseline._patcher.start()  # type: ignore[attr-defined]
    return baseline, created


class TestMultiagentBaseline:
    @pytest.mark.asyncio
    async def test_simple_route_short_circuits(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        scripts = [
            [
                {"type": "text", "content": "hi"},
                {"type": "done"},
            ]
        ]
        baseline, _ = _patched_baseline(scripts, schema)
        try:
            answer = await baseline.answer("hi there")
        finally:
            baseline._patcher.stop()  # type: ignore[attr-defined]

        assert answer.text == "hi"
        assert answer.extra["route"]["complexity"] == "simple"
        assert answer.extra["iterations"] == 0  # no iteration in SIMPLE path
        assert answer.extra["evaluations"] == []

    @pytest.mark.asyncio
    async def test_multi_step_records_iterations_and_verdicts(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        scripts = [
            [
                {
                    "type": "tool_call",
                    "tool": "find_entities",
                    "content": {"class_uri": "http://example.org/Pokemon"},
                },
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
        baseline, agents = _patched_baseline(scripts, schema)
        try:
            answer = await baseline.answer("Pokemon 비교")
        finally:
            baseline._patcher.stop()  # type: ignore[attr-defined]

        assert answer.extra["route"]["complexity"] == "multi_step"
        # One iteration consumed
        assert answer.extra["iterations"] == 1
        # One evaluator verdict, sufficient (full coverage + full citation)
        assert len(answer.extra["evaluations"]) == 1
        assert answer.extra["evaluations"][0]["verdict"] == "sufficient"
        # Tool call sequence captured
        assert answer.tool_calls == 1
        assert "find_entities" in answer.extra["tool_call_sequence"]
        # Latency was measured
        assert answer.latency_ms > 0
