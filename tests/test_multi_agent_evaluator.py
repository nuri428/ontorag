"""Unit tests for the v1.2 multi-agent 3-axis evaluator.

The deterministic axes (IsRel, IsUse) are pure functions, so most
coverage is direct calls with hand-built fixtures. The optional IsSup
axis exercises a mock BayesianEngine that returns precomputed marginals
— no pgmpy required.
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ontorag.chat.multi_agent.evaluator import (
    Evaluator,
    compute_is_rel,
    compute_is_use,
    decide,
)
from ontorag.chat.multi_agent.messages import (
    Complexity,
    EvaluationAxes,
    RouteDecision,
    SufficientContext,
)
from ontorag.stores.base import ClassSummary, SchemaResult


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


def _route(complexity: Complexity, *matched: str) -> RouteDecision:
    return RouteDecision(
        complexity=complexity,
        rationale="test",
        matched_classes=tuple(matched),
    )


class TestComputeIsRel:
    """IsRel — TBox class coverage of tool results."""

    def test_no_matched_classes_returns_perfect(self) -> None:
        """Question matched no class → relevance not applicable → 1.0."""
        schema = _schema(("http://example.org/Pokemon", None))
        route_dec = _route(Complexity.SIMPLE)  # no matched_classes
        score, matched = compute_is_rel([], route_dec, schema)
        assert score == 1.0
        assert matched == ()

    def test_full_coverage_from_class_uri_in_content(self) -> None:
        schema = _schema(
            ("http://example.org/Pokemon", None),
            ("http://example.org/Type", None),
        )
        route_dec = _route(Complexity.MULTI_STEP, "Pokemon", "Type")
        tool_results = [
            {
                "tool": "find_entities",
                "content": {"uri": "http://example.org/Pokemon"},
            },
            {
                "tool": "describe_entity",
                "content": {"uri": "http://example.org/Type"},
            },
        ]
        score, matched = compute_is_rel(tool_results, route_dec, schema)
        assert score == 1.0
        assert set(matched) == {"Pokemon", "Type"}

    def test_partial_coverage(self) -> None:
        schema = _schema(
            ("http://example.org/Pokemon", None),
            ("http://example.org/Type", None),
        )
        route_dec = _route(Complexity.MULTI_STEP, "Pokemon", "Type")
        tool_results = [
            {"tool": "find_entities", "content": {"uri": "http://example.org/Pokemon"}},
        ]
        score, matched = compute_is_rel(tool_results, route_dec, schema)
        assert score == 0.5
        assert matched == ("Pokemon",)

    def test_coverage_via_tool_args_class_uri(self) -> None:
        """Tool args containing class_uri count toward coverage."""
        schema = _schema(("http://example.org/Pokemon", None))
        route_dec = _route(Complexity.SINGLE_STEP, "Pokemon")
        tool_results = [
            {
                "tool": "count_entities",
                "args": {"class_uri": "http://example.org/Pokemon"},
                "content": {"count": 42},
            }
        ]
        score, matched = compute_is_rel(tool_results, route_dec, schema)
        assert score == 1.0
        assert matched == ("Pokemon",)

    def test_unrelated_uris_dont_contribute(self) -> None:
        schema = _schema(
            ("http://example.org/Pokemon", None),
            ("http://example.org/Move", None),
        )
        route_dec = _route(Complexity.SINGLE_STEP, "Pokemon")
        tool_results = [
            {
                "tool": "find_entities",
                "content": {"uri": "http://example.org/Move"},  # not target
            }
        ]
        score, matched = compute_is_rel(tool_results, route_dec, schema)
        assert score == 0.0
        assert matched == ()


class TestComputeIsUse:
    """IsUse — citation completeness of the candidate answer."""

    def test_empty_evidence_returns_neutral(self) -> None:
        score, cited = compute_is_use("anything", [])
        assert score == 0.5
        assert cited == ()

    def test_no_citation(self) -> None:
        tool_results = [
            {"content": {"uri": "http://example.org/Pikachu", "label": "Pikachu"}},
        ]
        score, cited = compute_is_use("The answer involves something.", tool_results)
        assert score == 0.0
        assert cited == ()

    def test_full_citation(self) -> None:
        tool_results = [
            {"content": {"uri": "http://example.org/Pikachu", "label": "Pikachu"}},
        ]
        score, cited = compute_is_use(
            "Pikachu is an Electric-type Pokemon.", tool_results
        )
        assert score == 1.0
        assert len(cited) >= 1

    def test_saturation_at_five(self) -> None:
        """A wall of evidence is capped at the saturation point."""
        tool_results = [
            {
                "content": [
                    {"uri": f"http://example.org/Entity{i}", "label": f"Entity{i}"}
                    for i in range(20)
                ]
            }
        ]
        # Cite all 20 entities by name
        answer = " ".join(f"Entity{i}" for i in range(20))
        score, cited = compute_is_use(answer, tool_results)
        assert score == 1.0
        assert len(cited) >= 5

    def test_case_insensitive_citation(self) -> None:
        tool_results = [{"content": {"label": "Pikachu"}}]
        score, _ = compute_is_use("pikachu wins", tool_results)
        assert score > 0.0


class TestDecide:
    """CRAG-style three-way branching."""

    def test_all_high_is_sufficient(self) -> None:
        axes = EvaluationAxes(is_rel=0.9, is_use=0.8, is_sup=0.75)
        verdict, _ = decide(axes)
        assert verdict == SufficientContext.SUFFICIENT

    def test_any_below_low_is_insufficient(self) -> None:
        axes = EvaluationAxes(is_rel=0.9, is_use=0.2, is_sup=0.9)
        verdict, _ = decide(axes)
        assert verdict == SufficientContext.INSUFFICIENT

    def test_middle_band_is_ambiguous(self) -> None:
        axes = EvaluationAxes(is_rel=0.5, is_use=0.5, is_sup=0.5)
        verdict, _ = decide(axes)
        assert verdict == SufficientContext.AMBIGUOUS

    def test_no_sup_axis_uses_only_two(self) -> None:
        """Optional IsSup is correctly skipped when None."""
        axes = EvaluationAxes(is_rel=0.9, is_use=0.9, is_sup=None)
        verdict, _ = decide(axes)
        assert verdict == SufficientContext.SUFFICIENT

    def test_rationale_lists_scores(self) -> None:
        axes = EvaluationAxes(is_rel=0.5, is_use=0.5)
        _, rationale = decide(axes)
        assert "rel=" in rationale and "use=" in rationale


class TestEvaluatorWithoutBN:
    """End-to-end evaluator with no Bayesian engine."""

    @pytest.mark.asyncio
    async def test_full_flow(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        evaluator = Evaluator(schema=schema)
        route_dec = _route(Complexity.SINGLE_STEP, "Pokemon")
        tool_results = [
            {
                "tool": "find_entities",
                "content": {"uri": "http://example.org/Pokemon", "label": "Pikachu"},
            }
        ]
        decision = await evaluator.evaluate(
            question="What Pokemon are there?",
            tool_results=tool_results,
            candidate_answer="Pikachu is one of the Pokemon.",
            route_decision=route_dec,
        )
        assert decision.verdict == SufficientContext.SUFFICIENT
        assert decision.axes.is_sup is None
        assert decision.axes.is_rel == 1.0
        assert decision.axes.is_use > 0.0
        assert "Pokemon" in decision.matched_classes_in_results

    @pytest.mark.asyncio
    async def test_insufficient_when_results_empty(self) -> None:
        schema = _schema(("http://example.org/Pokemon", None))
        evaluator = Evaluator(schema=schema)
        route_dec = _route(Complexity.SINGLE_STEP, "Pokemon")
        decision = await evaluator.evaluate(
            question="Anything?",
            tool_results=[],
            candidate_answer="I don't know.",
            route_decision=route_dec,
        )
        # IsRel = 0 (target has 1 class, none covered) → insufficient
        assert decision.verdict == SufficientContext.INSUFFICIENT
        assert decision.axes.is_rel == 0.0


class TestEvaluatorWithBN:
    """IsSup axis exercises a mock BayesianEngine — no pgmpy required."""

    @pytest.mark.asyncio
    async def test_perfect_certainty_yields_max_score(self) -> None:
        """Going from uniform prior to certain posterior → IsSup = 1.0."""
        engine = AsyncMock()
        # First call (before): uniform over 2 states, entropy = 1 bit
        # Second call (after): certain (one state has p=1), entropy = 0
        engine.compute_posterior.side_effect = [
            {"http://x": {"yes": 0.5, "no": 0.5}},
            {"http://x": {"yes": 1.0, "no": 0.0}},
        ]
        schema = _schema(("http://example.org/Pokemon", None))
        evaluator = Evaluator(schema=schema, bayes_engine=engine)
        route_dec = _route(Complexity.MULTI_STEP, "Pokemon")
        decision = await evaluator.evaluate(
            question="확률은?",
            tool_results=[
                {"content": {"uri": "http://example.org/Pokemon", "label": "x"}}
            ],
            candidate_answer="x is the answer.",
            route_decision=route_dec,
            bn_query=["http://x"],
            bn_evidence_before={},
            bn_evidence_after={"http://obs": "1"},
        )
        assert decision.axes.is_sup == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_no_reduction_yields_zero_sup(self) -> None:
        """Same uniform before and after → IsSup = 0.0."""
        engine = AsyncMock()
        engine.compute_posterior.side_effect = [
            {"http://x": {"yes": 0.5, "no": 0.5}},
            {"http://x": {"yes": 0.5, "no": 0.5}},
        ]
        schema = _schema(("http://example.org/Pokemon", None))
        evaluator = Evaluator(schema=schema, bayes_engine=engine)
        route_dec = _route(Complexity.MULTI_STEP, "Pokemon")
        decision = await evaluator.evaluate(
            question="확률은?",
            tool_results=[
                {"content": {"uri": "http://example.org/Pokemon", "label": "x"}}
            ],
            candidate_answer="answer",
            route_decision=route_dec,
            bn_query=["http://x"],
            bn_evidence_before={},
            bn_evidence_after={"http://obs": "1"},
        )
        assert decision.axes.is_sup == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_partial_reduction(self) -> None:
        """Half-reduced entropy → IsSup ≈ 0.5."""
        engine = AsyncMock()
        # H_before = 1 bit (uniform binary)
        # H_after = ~0.469 bits (0.9 / 0.1)
        engine.compute_posterior.side_effect = [
            {"http://x": {"yes": 0.5, "no": 0.5}},
            {"http://x": {"yes": 0.9, "no": 0.1}},
        ]
        schema = _schema(("http://example.org/Pokemon", None))
        evaluator = Evaluator(schema=schema, bayes_engine=engine)
        route_dec = _route(Complexity.MULTI_STEP, "Pokemon")
        decision = await evaluator.evaluate(
            question="q",
            tool_results=[
                {"content": {"uri": "http://example.org/Pokemon", "label": "x"}}
            ],
            candidate_answer="answer",
            route_decision=route_dec,
            bn_query=["http://x"],
            bn_evidence_before={},
            bn_evidence_after={"http://obs": "1"},
        )
        # 1 - H_after/H_before  where H_after ≈ 0.469
        expected = 1.0 - (-0.9 * math.log2(0.9) - 0.1 * math.log2(0.1)) / 1.0
        assert decision.axes.is_sup == pytest.approx(expected, abs=0.01)

    @pytest.mark.asyncio
    async def test_missing_bn_params_skips_sup(self) -> None:
        """Engine present but no bn_query → IsSup stays None."""
        engine = AsyncMock()
        schema = _schema(("http://example.org/Pokemon", None))
        evaluator = Evaluator(schema=schema, bayes_engine=engine)
        route_dec = _route(Complexity.SINGLE_STEP, "Pokemon")
        decision = await evaluator.evaluate(
            question="q",
            tool_results=[
                {"content": {"uri": "http://example.org/Pokemon", "label": "x"}}
            ],
            candidate_answer="answer",
            route_decision=route_dec,
        )
        assert decision.axes.is_sup is None
        engine.compute_posterior.assert_not_called()
