"""Tests for ontorag.eval.metrics.ragas_wrapper.

The wrapper is exercised in three ways:

1. Pure dataclass / input-routing checks that need no LLM and no RAGAS
   installed.
2. Mocked-RAGAS path that verifies we call into the library correctly
   and parse its output, without making real API calls.
3. A live integration test guarded by both ``OPENAI_API_KEY`` and the
   presence of the ``ragas`` package — skipped by default.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

import pytest

from ontorag.eval.baselines.protocol import (
    BaselineError,
    MissingBaselineDependencyError,
)
from ontorag.eval.metrics.ragas_wrapper import (
    RagasScore,
    evaluate_with_ragas,
)


# ── RagasScore dataclass ──────────────────────────────────────────────────────


class TestRagasScore:
    def test_defaults_are_none(self):
        s = RagasScore()
        assert s.faithfulness is None
        assert s.answer_correctness is None
        assert s.answer_relevancy is None
        assert s.judge_model is None
        assert s.extra == {}

    def test_to_dict_round_trip(self):
        s = RagasScore(
            faithfulness=0.9,
            answer_correctness=0.85,
            judge_model="gpt-4o-mini",
        )
        d = s.to_dict()
        assert d["faithfulness"] == 0.9
        assert d["answer_correctness"] == 0.85
        assert d["answer_relevancy"] is None
        assert d["judge_model"] == "gpt-4o-mini"

    def test_computed_metrics_lists_only_set_fields(self):
        s = RagasScore(faithfulness=0.9, answer_relevancy=0.8)
        assert set(s.computed_metrics()) == {"faithfulness", "answer_relevancy"}


# ── evaluate_with_ragas — input routing ───────────────────────────────────────


class TestEvaluateWithRagasInputRouting:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(BaselineError, match="OPENAI_API_KEY"):
            evaluate_with_ragas(
                question="Q", answer="A", reference_answer="A"
            )

    def test_returns_empty_score_when_nothing_computable(self, monkeypatch):
        """No contexts → no faithfulness. No reference_answer →
        no answer_correctness. answer_relevancy needs neither but is
        excluded by metrics filter."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        score = evaluate_with_ragas(
            question="Q",
            answer="A",
            contexts=None,
            reference_answer=None,
            metrics=["faithfulness", "answer_correctness"],
        )
        assert score.faithfulness is None
        assert score.answer_correctness is None
        assert score.answer_relevancy is None
        # Should not attempt to import ragas at all
        assert "ragas" not in sys.modules or score.judge_model is not None

    def test_missing_ragas_dependency_raises(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        with patch(
            "ontorag.eval.metrics.ragas_wrapper._require_ragas",
            side_effect=MissingBaselineDependencyError(
                "Install with: uv sync --extra bench"
            ),
        ):
            with pytest.raises(MissingBaselineDependencyError, match="bench"):
                evaluate_with_ragas(
                    question="Q",
                    answer="A",
                    reference_answer="A",
                    contexts=["ctx"],
                )


# ── Mocked RAGAS path ─────────────────────────────────────────────────────────


class TestEvaluateWithRagasMocked:
    def test_calls_ragas_and_parses_score(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        fake_ragas_module = types.ModuleType("ragas_fake")

        def fake_call_ragas(
            *,
            ragas,
            question,
            answer,
            reference_answer,
            contexts,
            computable,
            judge_model,
        ):
            return RagasScore(
                faithfulness=0.95 if "faithfulness" in computable else None,
                answer_correctness=0.85
                if "answer_correctness" in computable
                else None,
                answer_relevancy=0.9
                if "answer_relevancy" in computable
                else None,
                judge_model=judge_model,
            )

        with patch(
            "ontorag.eval.metrics.ragas_wrapper._require_ragas",
            return_value=fake_ragas_module,
        ), patch(
            "ontorag.eval.metrics.ragas_wrapper._call_ragas",
            side_effect=fake_call_ragas,
        ):
            score = evaluate_with_ragas(
                question="Who is the CEO of Aurora Tech?",
                answer="Alice Kim",
                reference_answer="Alice Kim",
                contexts=["Aurora Tech CEO: Alice Kim"],
            )
            assert score.faithfulness == 0.95
            assert score.answer_correctness == 0.85
            assert score.answer_relevancy == 0.9
            assert score.judge_model == "gpt-4o-mini"

    def test_metric_filter_limits_computation(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        fake = types.ModuleType("ragas_fake")
        captured = {}

        def fake_call_ragas(*, computable, judge_model, **_):
            captured["computable"] = computable
            return RagasScore(
                answer_relevancy=0.7 if "answer_relevancy" in computable else None,
                judge_model=judge_model,
            )

        with patch(
            "ontorag.eval.metrics.ragas_wrapper._require_ragas",
            return_value=fake,
        ), patch(
            "ontorag.eval.metrics.ragas_wrapper._call_ragas",
            side_effect=fake_call_ragas,
        ):
            score = evaluate_with_ragas(
                question="Q",
                answer="A",
                reference_answer="A",
                contexts=["ctx"],
                metrics=["answer_relevancy"],
            )
        assert captured["computable"] == {"answer_relevancy"}
        assert score.answer_relevancy == 0.7
        assert score.faithfulness is None
        assert score.answer_correctness is None


# ── Live integration (skipped by default) ─────────────────────────────────────


class TestRagasLive:
    def test_live_faithfulness(self):
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set")
        try:
            import ragas  # noqa: F401, PLC0415
        except ImportError:
            pytest.skip("ragas not installed (uv sync --extra bench)")

        score = evaluate_with_ragas(
            question="Who is the CEO of Aurora Tech?",
            answer="Alice Kim is the CEO of Aurora Tech.",
            reference_answer="Alice Kim",
            contexts=["Aurora Tech CEO: Alice Kim"],
            metrics=["faithfulness", "answer_correctness", "answer_relevancy"],
        )
        # Live LLM may yield slightly different scores; check shape only.
        assert isinstance(score, RagasScore)
        assert score.faithfulness is not None
