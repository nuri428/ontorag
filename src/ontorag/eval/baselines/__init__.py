"""RAG baselines for comparison against ontorag.

A *baseline* is any retrieval+generation system that can answer a
goldset question. Defining a common Protocol makes ontorag and external
systems (LangChain + Chroma, LlamaIndex, GraphRAG, …) measurable
side-by-side using the same evaluation code.

External baselines are optional dependencies — import errors at module
load are surfaced as ``MissingBaselineDependencyError`` so the CLI can
print a clear "install with `uv sync --extra bench`" hint.
"""

from __future__ import annotations

from ontorag.eval.baselines.protocol import (
    BaselineAnswer,
    BaselineError,
    MissingBaselineDependencyError,
    RAGBaseline,
)

__all__ = [
    "BaselineAnswer",
    "BaselineError",
    "MissingBaselineDependencyError",
    "RAGBaseline",
]
