from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class TermTypingResult:
    """Task A output: a text mention mapped to a TBox class."""

    term: str
    class_uri: str
    label: str
    confidence: float
    reasoning: str | None = None


@dataclass
class TaxonomyRelation:
    """Task B output: proposed rdfs:subClassOf relation."""

    child_term: str
    parent_uri: str
    confidence: float


@dataclass
class ExtractedTriple:
    """Task C output: proposed RDF triple (ABox assertion).

    predicate_uri and class_uri must exist in the current TBox —
    validated before return. subject_uri=None means a new entity; the
    pipeline mints a URI using the TBox primary namespace.
    """

    subject_label: str
    predicate_uri: str
    confidence: float
    subject_uri: str | None = None
    object_uri: str | None = None
    object_value: str | None = None


@dataclass
class PopulationResult:
    """Full A+B+C pipeline result.

    triples_loaded is set only when auto_load=True and load succeeded.
    """

    term_typings: list[TermTypingResult] = field(default_factory=list)
    taxonomy_proposals: list[TaxonomyRelation] = field(default_factory=list)
    triples: list[ExtractedTriple] = field(default_factory=list)
    triples_loaded: int | None = None


class OntologyLearner(Protocol):
    """LLMs4OL pipeline — all tasks backed by LLM prompting against the live TBox."""

    async def type_term(
        self,
        term: str,
        context: str | None = None,
        top_k: int = 3,
    ) -> list[TermTypingResult]:
        """Task A: rank TBox classes for a text mention."""
        ...

    async def discover_taxonomy(
        self,
        text: str,
        candidate_classes: list[str] | None = None,
    ) -> list[TaxonomyRelation]:
        """Task B: propose rdfs:subClassOf relations from text evidence."""
        ...

    async def extract_relations(
        self,
        text: str,
        entities: list[str] | None = None,
        min_confidence: float = 0.7,
    ) -> list[ExtractedTriple]:
        """Task C: propose object/data property triples from text."""
        ...

    async def populate_from_text(
        self,
        text: str,
        auto_load: bool = False,
        min_confidence: float = 0.7,
    ) -> PopulationResult:
        """Run A+B+C in sequence; optionally load accepted triples to Fuseki."""
        ...
