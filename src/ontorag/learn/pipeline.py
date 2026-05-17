from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from ontorag.learn import taxonomy as _taxonomy_mod
from ontorag.learn import term_typing as _term_typing_mod
from ontorag.learn import relation as _relation_mod
from ontorag.learn._utils import mint_uri
from ontorag.learn.base import (
    ExtractedTriple,
    OntologyLearner,
    PopulationResult,
    TaxonomyRelation,
    TermTypingResult,
)
from ontorag.stores.base import GraphStore, SchemaResult

logger = logging.getLogger(__name__)

_TERM_EXTRACTION_TOOL: dict[str, Any] = {
    "name": "report_entity_terms",
    "description": "Report noun phrases and named entities found in the text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "terms": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Distinct entity labels / noun phrases.",
            },
        },
        "required": ["terms"],
    },
}

_TERM_SYSTEM = (
    "Extract all distinct noun phrases and named entities from the text. "
    "Return each term only once. Respond using the provided tool."
)


def _serialize_to_ttl(
    triples: list[ExtractedTriple],
    typings: list[TermTypingResult],
    schema: SchemaResult,
) -> str:
    """Serialize accepted triples + type assertions to Turtle format.

    Args:
        triples: Task C triples, already filtered by confidence.
        typings: Task A results (used to emit rdf:type assertions).
        schema: Current TBox for namespace extraction.

    Returns:
        Turtle RDF string.
    """
    try:
        from rdflib import Graph, Literal, Namespace, URIRef
        from rdflib.namespace import RDF, RDFS, XSD
    except ImportError as exc:
        raise ImportError("rdflib is required for RDF serialization") from exc

    g = Graph()

    # Bind namespaces
    for prefix, uri_str in schema.namespaces.items():
        try:
            g.bind(prefix, Namespace(uri_str))
        except Exception:
            pass

    # Emit rdf:type assertions from Task A
    for typing in typings:
        subj_uri = typing_subject_uri(typing, schema)
        g.add((URIRef(subj_uri), RDF.type, URIRef(typing.class_uri)))
        g.add((URIRef(subj_uri), RDFS.label, Literal(typing.term)))

    # Emit triples from Task C
    for triple in triples:
        subj_uri = triple.subject_uri or mint_uri(triple.subject_label, schema)
        g.add((URIRef(subj_uri), RDFS.label, Literal(triple.subject_label)))

        pred = URIRef(triple.predicate_uri)

        if triple.object_uri:
            g.add((URIRef(subj_uri), pred, URIRef(triple.object_uri)))
        elif triple.object_value is not None:
            g.add((URIRef(subj_uri), pred, Literal(triple.object_value)))

    return g.serialize(format="turtle")


def typing_subject_uri(typing: TermTypingResult, schema: SchemaResult) -> str:
    """Mint or return the subject URI for a TermTypingResult."""
    return mint_uri(typing.term, schema)


class LLMOntologyLearner:
    """Concrete OntologyLearner backed by an LLM provider and a GraphStore.

    All tasks fetch the live TBox at call time — no stale schema cache.
    """

    def __init__(self, store: GraphStore, llm: Any) -> None:
        self._store = store
        self._llm = llm

    async def type_term(
        self,
        term: str,
        context: str | None = None,
        top_k: int = 3,
    ) -> list[TermTypingResult]:
        """Task A: rank TBox classes for a text mention."""
        schema = await self._store.get_schema()
        return await _term_typing_mod.type_term(self._llm, schema, term, context, top_k)

    async def discover_taxonomy(
        self,
        text: str,
        candidate_classes: list[str] | None = None,
    ) -> list[TaxonomyRelation]:
        """Task B: propose rdfs:subClassOf from text evidence."""
        schema = await self._store.get_schema()
        return await _taxonomy_mod.discover_taxonomy(self._llm, schema, text, candidate_classes)

    async def extract_relations(
        self,
        text: str,
        entities: list[str] | None = None,
        min_confidence: float = 0.7,
    ) -> list[ExtractedTriple]:
        """Task C: propose object/data property triples from text."""
        schema = await self._store.get_schema()
        return await _relation_mod.extract_relations(
            self._llm, schema, text, entities, min_confidence
        )

    async def populate_from_text(
        self,
        text: str,
        auto_load: bool = False,
        min_confidence: float = 0.7,
    ) -> PopulationResult:
        """Run A+B+C in sequence; optionally load accepted triples to Fuseki.

        Args:
            text: Source text to process.
            auto_load: If True, serialize accepted triples to TTL and load into the store.
            min_confidence: Minimum confidence to include a result.

        Returns:
            PopulationResult with all task outputs. triples_loaded is set when auto_load=True.
        """
        schema = await self._store.get_schema()

        # 1. Extract entity terms from text (shared step for A and C)
        terms = await self._extract_terms(text, schema)

        # 2. Task A — type each term
        typings: list[TermTypingResult] = []
        for term in terms:
            results = await _term_typing_mod.type_term(
                self._llm, schema, term, context=text[:500], top_k=1
            )
            for r in results:
                if r.confidence >= min_confidence:
                    typings.append(r)

        # 3. Task B — taxonomy discovery
        taxonomy = await _taxonomy_mod.discover_taxonomy(self._llm, schema, text, candidate_classes=None)
        taxonomy = [t for t in taxonomy if t.confidence >= min_confidence]

        # 4. Task C — relation extraction
        entity_labels = [t.term for t in typings]
        triples = await _relation_mod.extract_relations(
            self._llm, schema, text, entities=entity_labels or None, min_confidence=min_confidence
        )

        result = PopulationResult(
            term_typings=typings,
            taxonomy_proposals=taxonomy,
            triples=triples,
        )

        if auto_load and (triples or typings):
            loaded = await self._load_triples(triples, typings, schema)
            result.triples_loaded = loaded

        return result

    async def _extract_terms(self, text: str, schema: SchemaResult) -> list[str]:
        """Extract entity labels from text using a structured LLM call."""
        from ontorag.learn._utils import structured_call

        messages = [{"role": "user", "content": f"Text:\n{text[:2000]}"}]
        try:
            raw = await structured_call(
                self._llm, messages, _TERM_EXTRACTION_TOOL, system=_TERM_SYSTEM
            )
            return [t for t in raw.get("terms", []) if isinstance(t, str) and t.strip()]
        except Exception as exc:
            logger.warning("_extract_terms failed: %s", exc)
            return []

    async def _load_triples(
        self,
        triples: list[ExtractedTriple],
        typings: list[TermTypingResult],
        schema: SchemaResult,
    ) -> int:
        """Serialize triples to a temp TTL file and load into the store."""
        ttl = _serialize_to_ttl(triples, typings, schema)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ttl", delete=False, encoding="utf-8"
        ) as f:
            f.write(ttl)
            tmp_path = f.name
        try:
            result = await self._store.load_rdf(tmp_path, mode="data")
            return result.triples_loaded
        finally:
            Path(tmp_path).unlink(missing_ok=True)
