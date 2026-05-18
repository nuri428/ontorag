"""Common Protocol for RAG baselines used in the evaluation harness.

A baseline is anything that can answer a single natural-language
question. Concrete baselines wrap ontorag itself, LangChain+vector DBs,
LlamaIndex, GraphRAG, etc. The harness runs every baseline against the
same goldset and feeds the answers through the same metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class BaselineError(RuntimeError):
    """Generic baseline-side error (configuration, runtime, etc.)."""


class MissingBaselineDependencyError(BaselineError):
    """Raised when a baseline's optional dependencies are not installed.

    The message instructs the user how to install them — e.g.
    ``uv sync --extra bench``.
    """


@dataclass
class BaselineAnswer:
    """Structured output of a single baseline answer to one question.

    Fields:
        text: Final natural-language answer.
        cited_triples: For ontology-aware baselines, the triples (as
            ``(s, p, o)`` strings or URIs) used to derive the answer.
            Vector baselines return an empty list — they have no
            triples to cite.
        tool_calls: Number of tool invocations the baseline made.
            Vector baselines return 0.
        prompt_tokens: Approximate tokens sent to the LLM(s).
        completion_tokens: Approximate tokens received.
        latency_ms: Wall-clock time of the full answer pipeline.
        extra: Free-form per-baseline metadata (e.g. raw retrieved
            chunks for a vector baseline; tool-call sequence for an
            ontology baseline).
    """

    text: str
    cited_triples: list[tuple[str, str, str]] = field(default_factory=list)
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RAGBaseline(Protocol):
    """Anything that can answer a natural-language question with grounding.

    Implementations may be sync or async at construction (which is
    typically slow — index building, model loading) but ``answer`` is
    async so it can be ``await``ed inside the evaluation loop.

    ``name`` and ``version`` are surfaced in the eval report so different
    baseline configurations are distinguishable.
    """

    name: str
    version: str

    async def answer(self, question: str) -> BaselineAnswer:
        """Return a single :class:`BaselineAnswer` for the given question."""
        ...

    async def close(self) -> None:
        """Release resources (close HTTP clients, persist indexes, …).

        Default implementations may no-op. Always called at the end of
        an evaluation run via ``async with`` or ``try/finally``.
        """
        ...
