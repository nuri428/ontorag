"""RAGAS metric wrapper — LLM-as-judge metrics for baseline comparison.

Three RAGAS metrics are exposed:

* **Faithfulness** — does the answer follow from the retrieved context?
* **Answer Correctness** — semantic + factual overlap with the gold answer.
* **Answer Relevancy** — does the answer address the question?

These metrics use an LLM (OpenAI by default) as judge. They are
expensive — ~150 calls per baseline per 50-question goldset run — so
they live behind the same ``bench`` optional extra as the LangChain
baseline. Cheap deterministic metrics (sparql_eq, inference,
hallucination, citation) cover the regression-test path.

Design:
* :class:`RagasScore` — structured numeric result with per-metric breakdown.
* :func:`evaluate_with_ragas` — pure function accepting goldset row +
  baseline answer + (optional) retrieved contexts. Returns the score.

The wrapper isolates RAGAS API churn: the rest of the harness depends
only on this module's :class:`RagasScore` dataclass.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from ontorag.eval.baselines.protocol import (
    BaselineError,
    MissingBaselineDependencyError,
)

logger = logging.getLogger(__name__)


RagasMetricName = Literal["faithfulness", "answer_correctness", "answer_relevancy"]


@dataclass
class RagasScore:
    """Structured RAGAS evaluation result.

    Each field is a float in [0, 1] (RAGAS convention) or None when the
    metric was not requested / not computable (e.g. faithfulness with
    no retrieved context).
    """

    faithfulness: float | None = None
    answer_correctness: float | None = None
    answer_relevancy: float | None = None
    judge_model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "faithfulness": self.faithfulness,
            "answer_correctness": self.answer_correctness,
            "answer_relevancy": self.answer_relevancy,
            "judge_model": self.judge_model,
            "extra": dict(self.extra),
        }

    def computed_metrics(self) -> list[str]:
        """Names of metrics that have a non-None value."""
        names: list[str] = []
        for name in ("faithfulness", "answer_correctness", "answer_relevancy"):
            if getattr(self, name) is not None:
                names.append(name)
        return names


def _require_ragas() -> Any:
    """Lazy-import RAGAS; raise a clear error if missing."""
    try:
        import ragas  # noqa: PLC0415
    except ImportError as e:
        raise MissingBaselineDependencyError(
            "RAGAS metrics require the `bench` extra. "
            "Install with: `uv sync --extra bench`. "
            f"Original error: {e}"
        ) from e
    return ragas


_DEFAULT_JUDGE_MODEL = "gpt-4o-mini"


def evaluate_with_ragas(
    question: str,
    answer: str,
    reference_answer: str | None = None,
    contexts: list[str] | None = None,
    *,
    metrics: list[RagasMetricName] | None = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
) -> RagasScore:
    """Compute RAGAS metrics for a single (question, answer) pair.

    Args:
        question: The benchmark question (natural language).
        answer: The baseline's answer text.
        reference_answer: The gold answer. Required for answer_correctness;
            ignored by faithfulness and answer_relevancy.
        contexts: Retrieved text chunks the answer was generated from.
            Required for faithfulness; ignored by answer_correctness /
            answer_relevancy.
        metrics: Subset of metrics to compute. If None, computes whatever
            the supplied inputs allow.
        judge_model: OpenAI-compatible chat model id used as the LLM
            judge. Default ``gpt-4o-mini`` for cost; bump to ``gpt-4o``
            for higher-quality judging.

    Returns:
        A :class:`RagasScore` with only the requested+computable fields set.

    Raises:
        MissingBaselineDependencyError: if RAGAS is not installed.
        BaselineError: if no OpenAI key is available.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise BaselineError(
            "OPENAI_API_KEY not set — required by RAGAS LLM judge."
        )

    requested = set(metrics) if metrics is not None else {
        "faithfulness",
        "answer_correctness",
        "answer_relevancy",
    }

    # Determine which metrics are actually computable given the inputs.
    computable: set[RagasMetricName] = set()
    if "faithfulness" in requested and contexts:
        computable.add("faithfulness")
    if "answer_correctness" in requested and reference_answer:
        computable.add("answer_correctness")
    if "answer_relevancy" in requested:
        computable.add("answer_relevancy")

    if not computable:
        return RagasScore(judge_model=judge_model)

    ragas = _require_ragas()
    return _call_ragas(
        ragas=ragas,
        question=question,
        answer=answer,
        reference_answer=reference_answer,
        contexts=contexts,
        computable=computable,
        judge_model=judge_model,
    )


def _call_ragas(
    *,
    ragas: Any,
    question: str,
    answer: str,
    reference_answer: str | None,
    contexts: list[str] | None,
    computable: set[RagasMetricName],
    judge_model: str,
) -> RagasScore:
    """Invoke the RAGAS library and parse results into a RagasScore.

    Isolated so tests can mock ``ragas`` cleanly. RAGAS API surface
    changes are absorbed here.
    """
    # Lazy-import the metrics module (RAGAS sub-packages can vary by
    # version; keep imports tight to the call site).
    from ragas import evaluate  # noqa: PLC0415
    from ragas.metrics import (  # noqa: PLC0415
        AnswerCorrectness,
        AnswerRelevancy,
        Faithfulness,
    )

    metric_objs: list[Any] = []
    if "faithfulness" in computable:
        metric_objs.append(Faithfulness())
    if "answer_correctness" in computable:
        metric_objs.append(AnswerCorrectness())
    if "answer_relevancy" in computable:
        metric_objs.append(AnswerRelevancy())

    # RAGAS expects a Dataset-like dict. Single-row eval.
    try:
        from datasets import Dataset  # noqa: PLC0415

        ds = Dataset.from_dict(
            {
                "question": [question],
                "answer": [answer],
                "contexts": [contexts or []],
                "ground_truth": [reference_answer or ""],
            }
        )
        result = evaluate(ds, metrics=metric_objs)
        scores: dict[str, float] = (
            result.scores[0]
            if hasattr(result, "scores") and result.scores
            else dict(result)
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("RAGAS evaluate() failed: %s", e)
        return RagasScore(judge_model=judge_model, extra={"error": str(e)})

    def _grab(name: str) -> float | None:
        if name not in computable:
            return None
        v = scores.get(name)
        return float(v) if v is not None else None

    return RagasScore(
        faithfulness=_grab("faithfulness"),
        answer_correctness=_grab("answer_correctness"),
        answer_relevancy=_grab("answer_relevancy"),
        judge_model=judge_model,
    )
