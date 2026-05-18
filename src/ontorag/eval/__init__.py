"""ontorag evaluation harness — RAGAS + goldset benchmark.

Phase B of the post-v0.3.2 roadmap. Provides goldset loading, SPARQL
validation, and (in subsequent modules) metric computation for comparing
ontology-aware RAG against vector baselines.
"""

from __future__ import annotations

from ontorag.eval.goldset import (
    Difficulty,
    Goldset,
    GoldsetQuestion,
    GoldsetValidationError,
)

__all__ = [
    "Difficulty",
    "Goldset",
    "GoldsetQuestion",
    "GoldsetValidationError",
]
