"""BenchRunner — orchestrates goldset × baseline × metrics.

Given a :class:`Goldset` and a :class:`RAGBaseline`, runs every question
through the baseline, computes per-question metrics, and returns a
structured :class:`BenchResult` ready for JSON serialisation and
Markdown reporting.

This is the missing piece that connects everything Phase B has built:
goldset (B-1/2), baselines (B-6), metrics (B-4), CLI (B-5). Without
this orchestrator, no end-to-end benchmark number can be produced.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any

from rdflib import Graph

from ontorag.eval.baselines.protocol import BaselineAnswer, RAGBaseline  # noqa: F401
from ontorag.eval.goldset import Difficulty, Goldset, GoldsetQuestion
from ontorag.eval.metrics.citation import citation_coverage
from ontorag.eval.metrics.hallucination import hallucination_rate
from ontorag.eval.metrics.inference import system_uses_inference_features


@dataclass
class QuestionResult:
    """Per-question evaluation outcome — one row in the bench report."""

    question_id: str
    difficulty: str
    category: str
    uses_inference: bool
    question_text: str
    gold_answer: str
    baseline_answer: str
    baseline_cited_triple_count: int
    tool_calls: int
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    # Metric values (None if not computed for this row)
    hallucination_rate: float | None = None
    citation_coverage: float | None = None
    uses_property_path: bool | None = None
    # RAGAS LLM-as-judge metrics (only set when with_ragas=True)
    ragas_faithfulness: float | None = None
    ragas_answer_correctness: float | None = None
    ragas_answer_relevancy: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchResult:
    """Whole-run aggregate result with per-question detail and rollups."""

    baseline_name: str
    baseline_version: str
    goldset_path: str
    graph_triples: int
    total_questions: int
    language: str
    results: list[QuestionResult] = field(default_factory=list)
    aggregate: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_name": self.baseline_name,
            "baseline_version": self.baseline_version,
            "goldset_path": self.goldset_path,
            "graph_triples": self.graph_triples,
            "total_questions": self.total_questions,
            "language": self.language,
            "results": [asdict(r) for r in self.results],
            "aggregate": dict(self.aggregate),
        }


class BenchRunner:
    """Run every goldset question through one baseline and collect metrics.

    Args:
        goldset: Loaded :class:`Goldset`.
        baseline: Any object satisfying :class:`RAGBaseline`.
        graph: The reference RDF graph (schema + data) used by metrics
            that need to verify triples (hallucination).
        language: Which question form to feed the baseline — ``"en"``
            or ``"ko"``.
    """

    def __init__(
        self,
        goldset: Goldset,
        baseline: RAGBaseline,
        graph: Graph,
        *,
        language: str = "en",
        goldset_path: str = "",
        with_ragas: bool = False,
    ) -> None:
        self.goldset = goldset
        self.baseline = baseline
        self.graph = graph
        self.language = language
        self.goldset_path = goldset_path
        self.with_ragas = with_ragas

    async def run(self) -> BenchResult:
        """Execute the benchmark and return the aggregated result."""
        results: list[QuestionResult] = []
        for q in self.goldset:
            row = await self._run_one(q)
            results.append(row)

        aggregate = self._aggregate(results)
        return BenchResult(
            baseline_name=self.baseline.name,
            baseline_version=self.baseline.version,
            goldset_path=self.goldset_path,
            graph_triples=len(self.graph),
            total_questions=len(self.goldset),
            language=self.language,
            results=results,
            aggregate=aggregate,
        )

    async def _run_one(self, q: GoldsetQuestion) -> QuestionResult:
        question_text = (
            q.question_en if self.language == "en" else q.question_ko
        )
        gold_answer = (
            q.gold_answer_en if self.language == "en" else q.gold_answer_ko
        )
        start = time.perf_counter()
        answer: BaselineAnswer = await self.baseline.answer(question_text)
        elapsed_ms = (time.perf_counter() - start) * 1000

        hall_rate: float | None = None
        if answer.cited_triples:
            hall_rate = hallucination_rate(answer.cited_triples, self.graph)
        else:
            # Vector RAG: nothing to hallucinate at the triple level. The
            # comparison story is "no citation available", not "perfect".
            hall_rate = None

        cite_cov: float | None = None
        if answer.cited_triples:
            cite_cov = citation_coverage(answer.text, answer.cited_triples)
        else:
            cite_cov = None

        # Syntactic check via the baseline's "extra" field if present
        uses_pp: bool | None = None
        if "sparql_query" in answer.extra:
            uses_pp = system_uses_inference_features(answer.extra["sparql_query"])

        # Optional RAGAS LLM-as-judge (only if explicitly requested + answer present)
        ragas_f, ragas_c, ragas_r = None, None, None
        if self.with_ragas and answer.text:
            ragas_f, ragas_c, ragas_r = self._ragas_scores(
                question_text=question_text,
                baseline_answer=answer.text,
                gold_answer=gold_answer,
                contexts=self._extract_contexts(answer),
            )

        return QuestionResult(
            question_id=q.id,
            difficulty=q.difficulty.value,
            category=q.category,
            uses_inference=q.uses_inference,
            question_text=question_text,
            gold_answer=gold_answer,
            baseline_answer=answer.text,
            baseline_cited_triple_count=len(answer.cited_triples),
            tool_calls=answer.tool_calls,
            latency_ms=answer.latency_ms or elapsed_ms,
            prompt_tokens=answer.prompt_tokens,
            completion_tokens=answer.completion_tokens,
            hallucination_rate=hall_rate,
            citation_coverage=cite_cov,
            uses_property_path=uses_pp,
            ragas_faithfulness=ragas_f,
            ragas_answer_correctness=ragas_c,
            ragas_answer_relevancy=ragas_r,
            extra={k: v for k, v in answer.extra.items() if k != "sparql_query"},
        )

    @staticmethod
    def _extract_contexts(answer: BaselineAnswer) -> list[str]:
        """Pull retrieval context strings out of the baseline's extra dict.

        Vector baselines surface their retrieved chunks under
        ``extra["retrieved_chunks"]``. Ontology baselines may not have a
        natural context payload; in that case we synthesise one from
        cited triples so RAGAS Faithfulness has something to judge against.
        """
        chunks = answer.extra.get("retrieved_chunks")
        if isinstance(chunks, list) and chunks:
            return [str(c) for c in chunks]
        if answer.cited_triples:
            return [
                f"{s} {p} {o}" for (s, p, o) in answer.cited_triples
            ]
        return []

    def _ragas_scores(
        self,
        *,
        question_text: str,
        baseline_answer: str,
        gold_answer: str,
        contexts: list[str],
    ) -> tuple[float | None, float | None, float | None]:
        """Call RAGAS wrapper; degrade gracefully if it errors / is missing."""
        try:
            from ontorag.eval.metrics.ragas_wrapper import (  # noqa: PLC0415
                evaluate_with_ragas,
            )
        except ImportError:
            return None, None, None
        try:
            score = evaluate_with_ragas(
                question=question_text,
                answer=baseline_answer,
                reference_answer=gold_answer,
                contexts=contexts,
            )
        except Exception:  # noqa: BLE001 — non-fatal; record None
            return None, None, None
        return (
            score.faithfulness,
            score.answer_correctness,
            score.answer_relevancy,
        )

    def _aggregate(self, results: list[QuestionResult]) -> dict[str, Any]:
        """Compute roll-up averages, hallucination counts, and per-tier numbers."""
        def _safe_mean(vals: list[float | None]) -> float | None:
            xs = [v for v in vals if v is not None]
            return mean(xs) if xs else None

        agg: dict[str, Any] = {
            "avg_latency_ms": _safe_mean([r.latency_ms for r in results]),
            "avg_tool_calls": _safe_mean(
                [float(r.tool_calls) for r in results]
            ),
            "total_prompt_tokens": sum(r.prompt_tokens for r in results),
            "total_completion_tokens": sum(r.completion_tokens for r in results),
            "avg_hallucination_rate": _safe_mean(
                [r.hallucination_rate for r in results]
            ),
            "avg_citation_coverage": _safe_mean(
                [r.citation_coverage for r in results]
            ),
            "citation_provided_count": sum(
                1 for r in results if r.baseline_cited_triple_count > 0
            ),
            "citation_provided_rate": (
                sum(1 for r in results if r.baseline_cited_triple_count > 0)
                / len(results)
                if results
                else 0.0
            ),
            "avg_ragas_faithfulness": _safe_mean(
                [r.ragas_faithfulness for r in results]
            ),
            "avg_ragas_answer_correctness": _safe_mean(
                [r.ragas_answer_correctness for r in results]
            ),
            "avg_ragas_answer_relevancy": _safe_mean(
                [r.ragas_answer_relevancy for r in results]
            ),
        }

        # Per-difficulty rollup
        per_diff: dict[str, dict[str, Any]] = {}
        for diff in Difficulty:
            subset = [r for r in results if r.difficulty == diff.value]
            if not subset:
                continue
            per_diff[diff.value] = {
                "count": len(subset),
                "avg_hallucination": _safe_mean(
                    [r.hallucination_rate for r in subset]
                ),
                "avg_citation": _safe_mean(
                    [r.citation_coverage for r in subset]
                ),
                "citation_provided": sum(
                    1 for r in subset if r.baseline_cited_triple_count > 0
                ),
            }
        agg["per_difficulty"] = per_diff
        return agg
