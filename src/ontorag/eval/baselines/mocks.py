"""Mock baselines for deterministic simulation without API costs.

Two mocks live here:

* :class:`OntoragMockBaseline` — simulates a *perfect* ontology-RAG
  system. Uses the goldset's ``gold_sparql`` to fetch ground-truth
  triples from the graph; returns them as ``cited_triples`` alongside
  the gold answer. Demonstrates the upper bound of ontology RAG.

* :class:`VectorRAGMockBaseline` — simulates a *realistic* vector RAG
  system. Returns the gold answer ~70 % of the time, hallucinates ~20 %
  of the time, and answers "I don't know" ~10 % of the time.
  ``cited_triples`` is always empty (vector RAG produces text-only
  citations of chunks, not triples).

Together they let the orchestrator + report tooling produce a complete
"ontorag vs vector RAG" comparison story without spending API tokens —
useful for development, CI demonstration, and blog post drafting before
real API calls are budgeted.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from rdflib import Graph

from ontorag.eval.baselines.protocol import BaselineAnswer
from ontorag.eval.goldset import Goldset, GoldsetQuestion


def _query_to_cited_triples(
    question: GoldsetQuestion, graph: Graph
) -> list[tuple[Any, Any, Any]]:
    """Run gold_sparql and recover supporting triples from each URI binding.

    Returns rdflib Node tuples (URIRef/Literal) — NOT plain strings —
    so the hallucination metric's ``(s, p, o) in graph`` check works
    correctly (string-coerced Literals lose their datatype/lang and
    become non-equal to the asserted Literal).

    For aggregation queries (COUNT) result rows hold ints, not URIs,
    so cited_triples ends up empty — that's correct: an aggregate
    summarises many triples, none of which is a unique citation.
    """
    from rdflib import URIRef  # noqa: PLC0415

    cited: list[tuple[Any, Any, Any]] = []
    try:
        rows = list(graph.query(question.gold_sparql))
    except Exception:
        return []

    for row in rows:
        for cell in row:
            if cell is None:
                continue
            if isinstance(cell, URIRef):
                for triple in graph.triples((cell, None, None)):
                    cited.append(triple)
                if len(cited) >= 20:
                    break
        if len(cited) >= 20:
            break

    return cited[:20]


class OntoragMockBaseline:
    """Perfect-retrieval mock — simulates an ideal ontorag tool agent.

    Constructed with the goldset + graph; answers are computed once at
    init time so :meth:`answer` is O(1).
    """

    name = "ontorag_mock"
    version = "0.1.0"

    def __init__(self, goldset: Goldset, graph: Graph, *, language: str = "en") -> None:
        self._language = language
        self._answers: dict[str, BaselineAnswer] = {}
        for q in goldset:
            cited = _query_to_cited_triples(q, graph)
            text = q.gold_answer_en if language == "en" else q.gold_answer_ko
            self._answers[self._key(q)] = BaselineAnswer(
                text=text,
                cited_triples=cited,
                tool_calls=2 if q.uses_inference else 1,
                prompt_tokens=300 + len(cited) * 30,
                completion_tokens=80,
                latency_ms=180.0,
                extra={"simulated": True, "baseline_kind": "ontology_perfect"},
            )

    def _key(self, q: GoldsetQuestion) -> str:
        return q.question_en if self._language == "en" else q.question_ko

    async def answer(self, question: str) -> BaselineAnswer:
        # Pretend we did work
        time.sleep(0.001)
        if question in self._answers:
            return self._answers[question]
        # Unknown question — return a graceful empty answer
        return BaselineAnswer(
            text="(no goldset entry matched this question)",
            cited_triples=[],
            tool_calls=0,
            latency_ms=10.0,
            extra={"simulated": True, "matched": False},
        )

    async def close(self) -> None:
        return None


class VectorRAGMockBaseline:
    """Realistic vector RAG mock — lossy, no triple citations.

    Behaviour is *deterministic given the question text*: a stable hash
    of the question selects one of three outcomes (correct / hallucinated
    / unknown). This gives reproducible mock numbers without needing a
    seed parameter.
    """

    name = "vector_rag_mock"
    version = "0.1.0"

    def __init__(self, goldset: Goldset, *, language: str = "en") -> None:
        self._language = language
        self._gold: dict[str, GoldsetQuestion] = {}
        for q in goldset:
            self._gold[self._key(q)] = q

    def _key(self, q: GoldsetQuestion) -> str:
        return q.question_en if self._language == "en" else q.question_ko

    @staticmethod
    def _bucket(question: str) -> str:
        """Deterministic 0–99 bucket from the question hash."""
        h = hashlib.sha256(question.encode("utf-8")).hexdigest()
        n = int(h[:4], 16) % 100
        # 70% correct / 20% hallucinated / 10% unknown
        if n < 70:
            return "correct"
        if n < 90:
            return "hallucinated"
        return "unknown"

    async def answer(self, question: str) -> BaselineAnswer:
        time.sleep(0.001)
        q = self._gold.get(question)
        if q is None:
            return BaselineAnswer(
                text="(no chunk retrieved for this question)",
                cited_triples=[],
                tool_calls=0,
                latency_ms=120.0,
                extra={"simulated": True, "matched": False},
            )

        bucket = self._bucket(question)
        gold_text = (
            q.gold_answer_en if self._language == "en" else q.gold_answer_ko
        )

        if bucket == "correct":
            text = gold_text
            extra: dict[str, Any] = {"bucket": "correct"}
        elif bucket == "hallucinated":
            # Plausible-sounding but wrong answer — flip details
            text = f"Based on retrieved context, the answer appears to be: {self._hallucinate(gold_text)}"
            extra = {"bucket": "hallucinated"}
        else:  # unknown
            text = "I don't have enough information from the retrieved context to answer this question."
            extra = {"bucket": "unknown"}

        return BaselineAnswer(
            text=text,
            cited_triples=[],  # vector RAG cites chunks, not triples
            tool_calls=0,
            prompt_tokens=520,  # k=5 chunks ~100 tokens each + question + scaffold
            completion_tokens=60,
            latency_ms=420.0,  # embedding lookup + LLM generation
            extra={
                **extra,
                "simulated": True,
                "baseline_kind": "vector_rag_lossy",
                "retrieved_chunks_count": 5,
            },
        )

    @staticmethod
    def _hallucinate(gold: str) -> str:
        """Generate a plausible-but-wrong variant of the gold answer."""
        if not gold:
            return "an alternative entity"
        # Simple: swap a number to 'approximately twice' it, prepend "approximately"
        # This is *intentionally* fake — it's a mock, not a real LLM.
        digits = "".join(c for c in gold if c.isdigit())
        if digits:
            wrong = str(int(digits) + 7)
            return gold.replace(digits, wrong, 1)
        return f"approximately {gold} (estimated)"

    async def close(self) -> None:
        return None
