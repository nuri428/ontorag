"""Tests for ontorag.eval.orchestrator + mock baselines.

The orchestrator is the missing piece that connects goldset, baselines,
and metrics into a single end-to-end run. Mock baselines make the test
deterministic without LLM API calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import Graph
from typer.testing import CliRunner

from ontorag.cli import app
from ontorag.eval.baselines.mocks import (
    OntoragMockBaseline,
    VectorRAGMockBaseline,
)
from ontorag.eval.goldset import Goldset
from ontorag.eval.orchestrator import BenchRunner, BenchResult, QuestionResult

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMERCE = REPO_ROOT / "examples" / "commerce"

runner = CliRunner()


@pytest.fixture()
def commerce_graph() -> Graph:
    g = Graph()
    g.parse(COMMERCE / "schema.ttl", format="turtle")
    g.parse(COMMERCE / "data.ttl", format="turtle")
    return g


@pytest.fixture()
def commerce_goldset() -> Goldset:
    return Goldset.load(COMMERCE / "goldset.jsonl")


# ── Mock baselines ────────────────────────────────────────────────────────────


class TestOntoragMockBaseline:
    async def test_returns_gold_answer(self, commerce_goldset, commerce_graph):
        b = OntoragMockBaseline(commerce_goldset, commerce_graph)
        q = commerce_goldset.questions[0]
        ans = await b.answer(q.question_en)
        assert ans.text == q.gold_answer_en
        assert ans.tool_calls >= 1

    async def test_cited_triples_nonempty_for_uri_results(
        self, commerce_goldset, commerce_graph
    ):
        """Questions whose gold_sparql returns at least one URI binding
        should produce non-empty cited_triples."""
        b = OntoragMockBaseline(commerce_goldset, commerce_graph)
        # Q001: SELECT ?ceo WHERE { co:Org_AuroraTech co:hasCEO ?ceo }
        # → URI binding → cited_triples should not be empty
        q1 = next(q for q in commerce_goldset if q.id == "Q001")
        ans = await b.answer(q1.question_en)
        assert ans.cited_triples


class TestVectorRAGMockBaseline:
    async def test_deterministic_bucket(self, commerce_goldset):
        b1 = VectorRAGMockBaseline(commerce_goldset)
        b2 = VectorRAGMockBaseline(commerce_goldset)
        q = commerce_goldset.questions[0]
        a1 = await b1.answer(q.question_en)
        a2 = await b2.answer(q.question_en)
        assert a1.text == a2.text  # same hash → same bucket

    async def test_no_cited_triples(self, commerce_goldset):
        b = VectorRAGMockBaseline(commerce_goldset)
        for q in commerce_goldset.questions[:5]:
            ans = await b.answer(q.question_en)
            assert ans.cited_triples == []  # vector RAG: never cites triples


# ── BenchRunner ───────────────────────────────────────────────────────────────


class TestBenchRunner:
    async def test_runs_all_questions(self, commerce_goldset, commerce_graph):
        baseline = OntoragMockBaseline(commerce_goldset, commerce_graph)
        result = await BenchRunner(
            commerce_goldset, baseline, commerce_graph
        ).run()
        assert isinstance(result, BenchResult)
        assert result.total_questions == len(commerce_goldset)
        assert len(result.results) == len(commerce_goldset)

    async def test_aggregate_contains_expected_keys(
        self, commerce_goldset, commerce_graph
    ):
        baseline = OntoragMockBaseline(commerce_goldset, commerce_graph)
        result = await BenchRunner(
            commerce_goldset, baseline, commerce_graph
        ).run()
        agg = result.aggregate
        assert "avg_latency_ms" in agg
        assert "avg_tool_calls" in agg
        assert "citation_provided_count" in agg
        assert "per_difficulty" in agg

    async def test_ontorag_mock_has_zero_hallucination(
        self, commerce_goldset, commerce_graph
    ):
        """Perfect-retrieval mock cites only real triples — hallucination = 0."""
        baseline = OntoragMockBaseline(commerce_goldset, commerce_graph)
        result = await BenchRunner(
            commerce_goldset, baseline, commerce_graph
        ).run()
        # avg_hallucination_rate is None when no cited_triples; here we
        # expect it to be defined and 0.0
        assert result.aggregate["avg_hallucination_rate"] == 0.0

    async def test_vector_mock_no_citations(
        self, commerce_goldset, commerce_graph
    ):
        baseline = VectorRAGMockBaseline(commerce_goldset)
        result = await BenchRunner(
            commerce_goldset, baseline, commerce_graph
        ).run()
        assert result.aggregate["citation_provided_count"] == 0
        assert result.aggregate["citation_provided_rate"] == 0.0

    async def test_question_result_fields(
        self, commerce_goldset, commerce_graph
    ):
        baseline = OntoragMockBaseline(commerce_goldset, commerce_graph)
        result = await BenchRunner(
            commerce_goldset, baseline, commerce_graph
        ).run()
        row = result.results[0]
        assert isinstance(row, QuestionResult)
        assert row.question_id
        assert row.baseline_answer
        assert row.tool_calls >= 0


# ── CLI: ontorag eval bench ───────────────────────────────────────────────────


class TestEvalBenchCLI:
    def test_bench_with_ontorag_mock(self, tmp_path):
        out = tmp_path / "ontorag.json"
        result = runner.invoke(
            app,
            [
                "eval", "bench",
                str(COMMERCE / "goldset.jsonl"),
                "--baseline", "ontorag_mock",
                "--schema", str(COMMERCE / "schema.ttl"),
                "--data", str(COMMERCE / "data.ttl"),
                "--output", str(out),
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["baseline_name"] == "ontorag_mock"
        assert data["total_questions"] == 20

    def test_bench_with_vector_mock(self, tmp_path):
        out = tmp_path / "vector.json"
        result = runner.invoke(
            app,
            [
                "eval", "bench",
                str(COMMERCE / "goldset.jsonl"),
                "--baseline", "vector_rag_mock",
                "--schema", str(COMMERCE / "schema.ttl"),
                "--data", str(COMMERCE / "data.ttl"),
                "--output", str(out),
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["baseline_name"] == "vector_rag_mock"
        # vector mock never cites triples
        assert data["aggregate"]["citation_provided_count"] == 0

    def test_unknown_baseline_fails(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "eval", "bench",
                str(COMMERCE / "goldset.jsonl"),
                "--baseline", "nonexistent",
                "--schema", str(COMMERCE / "schema.ttl"),
                "--data", str(COMMERCE / "data.ttl"),
            ],
        )
        assert result.exit_code != 0


# ── BenchRunner + with_ragas (mocked, no real RAGAS call) ────────────────────


class TestBenchRunnerWithRagas:
    """RAGAS integration is enabled via with_ragas=True. We don't make
    real API calls here — we mock evaluate_with_ragas at its import site
    inside the orchestrator."""

    async def test_with_ragas_populates_score_fields(
        self, commerce_goldset, commerce_graph, monkeypatch
    ):
        from ontorag.eval.metrics.ragas_wrapper import RagasScore

        def fake_eval(question, answer, reference_answer=None, contexts=None,
                      metrics=None, judge_model="gpt-4o-mini"):
            return RagasScore(
                faithfulness=0.92,
                answer_correctness=0.88,
                answer_relevancy=0.91,
                judge_model=judge_model,
            )

        # The orchestrator imports inside _ragas_scores, so patch the
        # module attribute used at call time.
        import ontorag.eval.metrics.ragas_wrapper as rw
        monkeypatch.setattr(rw, "evaluate_with_ragas", fake_eval)

        baseline = OntoragMockBaseline(commerce_goldset, commerce_graph)
        result = await BenchRunner(
            commerce_goldset, baseline, commerce_graph, with_ragas=True
        ).run()
        assert result.aggregate["avg_ragas_faithfulness"] == pytest.approx(0.92)
        assert result.aggregate["avg_ragas_answer_correctness"] == pytest.approx(0.88)
        # All per-question rows should have RAGAS scores
        assert all(r.ragas_faithfulness is not None for r in result.results)

    async def test_with_ragas_disabled_by_default(
        self, commerce_goldset, commerce_graph
    ):
        baseline = OntoragMockBaseline(commerce_goldset, commerce_graph)
        result = await BenchRunner(
            commerce_goldset, baseline, commerce_graph
        ).run()
        assert all(r.ragas_faithfulness is None for r in result.results)
        assert result.aggregate["avg_ragas_faithfulness"] is None


# ── LangChain baseline wiring via the CLI ─────────────────────────────────────


class TestLangChainCLIWiring:
    """Verifies the CLI's --baseline langchain path produces a clear,
    actionable error when prerequisites are missing — not a stack trace."""

    def test_langchain_without_api_key_exits_cleanly(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = runner.invoke(
            app,
            [
                "eval", "bench",
                str(COMMERCE / "goldset.jsonl"),
                "--baseline", "langchain",
                "--schema", str(COMMERCE / "schema.ttl"),
                "--data", str(COMMERCE / "data.ttl"),
            ],
        )
        assert result.exit_code != 0
        # Either a typer BadParameter (preferred) or surfaced error;
        # in any case, no Python traceback in user-visible output.
        combined = result.stdout + (result.stderr or "")
        # Heuristic: the word OPENAI_API_KEY or "bench" should appear
        # so the user knows what to fix.
        assert (
            "OPENAI_API_KEY" in combined
            or "bench" in combined
            or "langchain" in combined.lower()
        )
