"""Goldset — benchmark questions with ground-truth SPARQL and answers.

A goldset is a JSONL file where each line describes one benchmark question.
Each question carries:

* the natural-language prompt (Korean + English),
* the canonical SPARQL query that produces the ground truth,
* the ground-truth answer in both languages,
* the URIs of triples that must be retrieved to answer correctly,
* a flag for whether OWL inference is required.

These fields drive RAGAS-style metrics (Context Precision/Recall) and
ontorag-specific metrics (Inference Utilization, SPARQL Correctness).
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Iterator

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parser import parseQuery


class Difficulty(str, Enum):
    """Question difficulty tier — drives goldset coverage policy and reporting."""

    easy = "easy"
    medium = "medium"
    hard = "hard"
    trap = "trap"


class GoldsetValidationError(ValueError):
    """Raised when a goldset file fails structural or SPARQL validation."""


class GoldsetQuestion(BaseModel):
    """A single benchmark question.

    `gold_triples` holds the URIs (or short triple descriptions) that the
    retriever must surface for a correct answer. RAGAS Context Recall is
    the fraction of these present in retrieval output.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=r"^Q\d{3,}$",
        description="Identifier of the form Qnnn (zero-padded, ≥3 digits).",
    )
    difficulty: Difficulty
    category: str = Field(
        min_length=1,
        description="Free-form pattern label (single_entity, filter_join, "
        "multi_hop, reverse_lookup, transitive_inference, counting, "
        "hallucination_trap, …). Used for per-pattern accuracy reports.",
    )

    question_ko: str = Field(min_length=1)
    question_en: str = Field(min_length=1)
    gold_sparql: str = Field(min_length=1)
    gold_answer_ko: str = Field(min_length=1)
    gold_answer_en: str = Field(min_length=1)

    gold_triples: list[str] = Field(
        default_factory=list,
        description="Triple URIs or short representations required to answer. "
        "Empty list is valid for trap questions (correct answer = absence).",
    )

    uses_inference: bool = False
    inference_type: str | None = Field(
        default=None,
        description="When uses_inference=True, the OWL/RDFS feature exercised "
        "(owl:TransitiveProperty, rdfs:subClassOf, owl:inverseOf, …).",
    )

    source: str | None = Field(
        default=None,
        description="Canonical source identifier (e.g. 'T0360'). None when the "
        "question is not tied to a specific source.",
    )

    trap_note: str | None = Field(
        default=None,
        description="Required when difficulty=trap. Explains the trap mechanism.",
    )

    @field_validator("gold_sparql")
    @classmethod
    def _validate_sparql_syntax(cls, value: str) -> str:
        """Reject ill-formed SPARQL at load time, not at evaluation time."""
        try:
            parseQuery(value)
        except Exception as e:  # noqa: BLE001 — rdflib raises generic Exception
            raise ValueError(f"Invalid SPARQL syntax: {e}") from e
        return value

    @model_validator(mode="after")
    def _cross_field_invariants(self):
        """Cross-field rules: inference flag consistency + trap explanation."""
        if self.inference_type is not None and not self.uses_inference:
            raise ValueError(
                "inference_type is set but uses_inference=False. "
                "Set uses_inference=True or clear inference_type."
            )
        if self.difficulty == Difficulty.trap and not self.trap_note:
            raise ValueError(
                "difficulty=trap requires a non-empty trap_note explaining the "
                "hallucination pattern this question probes."
            )
        return self

    def prepared_query(self):
        """Return the rdflib prepared query object (parse + plan, no execute)."""
        return prepareQuery(self.gold_sparql)


class Goldset(BaseModel):
    """A collection of GoldsetQuestion entries loaded from JSONL.

    Use `Goldset.load(path)` to construct from disk. Iteration yields
    questions in file order. Use `by_difficulty()` / `by_category()` for
    sliced reporting.
    """

    model_config = ConfigDict(extra="forbid")

    questions: list[GoldsetQuestion] = Field(default_factory=list)

    @field_validator("questions")
    @classmethod
    def _unique_ids(cls, value: list[GoldsetQuestion]) -> list[GoldsetQuestion]:
        ids = [q.id for q in value]
        if len(ids) != len(set(ids)):
            from collections import Counter

            duplicates = [k for k, v in Counter(ids).items() if v > 1]
            raise ValueError(f"Duplicate question IDs: {duplicates}")
        return value

    @classmethod
    def load(cls, path: str | Path) -> "Goldset":
        """Load and validate a JSONL goldset file.

        Raises:
            GoldsetValidationError: if any line is malformed, fails schema
                validation, or contains invalid SPARQL syntax. Line numbers
                are 1-based and refer to the source file.
        """
        path = Path(path)
        if not path.exists():
            raise GoldsetValidationError(f"Goldset file not found: {path}")

        questions: list[GoldsetQuestion] = []
        with path.open(encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as e:
                    raise GoldsetValidationError(
                        f"{path.name}:{line_no} invalid JSON: {e.msg}"
                    ) from e
                try:
                    questions.append(GoldsetQuestion(**data))
                except Exception as e:  # noqa: BLE001
                    raise GoldsetValidationError(
                        f"{path.name}:{line_no} validation failed: {e}"
                    ) from e

        try:
            return cls(questions=questions)
        except Exception as e:  # noqa: BLE001 — wrap duplicate-ID and other set-level checks
            raise GoldsetValidationError(f"{path.name}: {e}") from e

    def __len__(self) -> int:
        return len(self.questions)

    def __iter__(self) -> Iterator[GoldsetQuestion]:  # type: ignore[override]
        return iter(self.questions)

    def by_difficulty(self, difficulty: Difficulty | str) -> list[GoldsetQuestion]:
        d = Difficulty(difficulty) if isinstance(difficulty, str) else difficulty
        return [q for q in self.questions if q.difficulty == d]

    def by_category(self, category: str) -> list[GoldsetQuestion]:
        return [q for q in self.questions if q.category == category]

    def distribution(self) -> dict[Difficulty, int]:
        """Count of questions per difficulty tier."""
        return {d: len(self.by_difficulty(d)) for d in Difficulty}
