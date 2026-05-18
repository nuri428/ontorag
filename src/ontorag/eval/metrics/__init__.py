"""Metric implementations for the ontorag evaluation harness.

Two categories live here:

1. RAGAS-derived metrics (LLM-as-judge) — Faithfulness, Answer Correctness,
   Context Precision/Recall. Module: ``ragas_wrapper`` (Phase B-3).
2. ontorag-specific metrics — measure features that vector RAG cannot:
   SPARQL Correctness, Inference Utilization, Hallucinated Triples,
   Citation Coverage. Modules: ``sparql_eq``, ``inference``, ``hallucination``,
   ``citation`` (Phase B-4).

Each metric is a pure function: it takes structured inputs (goldset row +
system output) and returns a float in [0, 1] or a structured result dict.
"""

from __future__ import annotations

from ontorag.eval.metrics.sparql_eq import (
    SparqlResultSet,
    sparql_result_equivalent,
    sparql_result_jaccard,
)

__all__ = [
    "SparqlResultSet",
    "sparql_result_equivalent",
    "sparql_result_jaccard",
]
