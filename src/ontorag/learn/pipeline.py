from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from ontorag.learn import taxonomy as _taxonomy_mod
from ontorag.learn import term_typing as _term_typing_mod
from ontorag.learn import relation as _relation_mod
from ontorag.learn import shacl as _shacl
from ontorag.learn._utils import mint_uri, structured_call
from ontorag.learn.base import (
    ExtractedTriple,
    PopulationResult,
    TaxonomyRelation,
    TermTypingResult,
)
from ontorag.learn.shacl import ShaclViolation
from ontorag.learn.column_mapper import (
    ColumnMapping,
    MappingFile,
    compute_schema_hash,
    load_mapping,
    mint_subject_uri,
    propose_mapping,
    save_mapping,
    validate_mapping_hash,
)
from ontorag.learn.structured_reader import read_structured
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
                "maxItems": 50,
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


def _resolve_namespace(class_uri: str | None, schema: SchemaResult) -> str:
    """Derive a URI namespace for subject minting from class_uri prefix or schema."""
    if class_uri:
        prefix = class_uri.split(":")[0]
        if prefix in schema.namespaces:
            return schema.namespaces[prefix]
    if schema.namespaces:
        return next(iter(schema.namespaces.values()))
    return "http://example.org/"


def _build_graph(
    triples: list[ExtractedTriple],
    typings: list[TermTypingResult],
    schema: SchemaResult,
) -> Any:
    """Build an rdflib Graph from typings + triples. Returned as Any to keep
    rdflib import optional at module-load time.
    """
    try:
        from rdflib import Graph, Literal, Namespace, URIRef
        from rdflib.namespace import RDF, RDFS
    except ImportError as exc:
        raise ImportError("rdflib is required for RDF serialization") from exc

    g = Graph()

    for prefix, uri_str in schema.namespaces.items():
        try:
            g.bind(prefix, Namespace(uri_str))
        except Exception as exc:  # pragma: no cover
            logger.debug("namespace bind skipped %r: %s", prefix, exc)

    for typing in typings:
        subj_uri = mint_uri(typing.term, schema)
        g.add((URIRef(subj_uri), RDF.type, URIRef(typing.class_uri)))
        g.add((URIRef(subj_uri), RDFS.label, Literal(typing.term)))

    for triple in triples:
        subj_uri = triple.subject_uri or mint_uri(triple.subject_label, schema)
        g.add((URIRef(subj_uri), RDFS.label, Literal(triple.subject_label)))

        pred = URIRef(triple.predicate_uri)

        if triple.object_uri:
            g.add((URIRef(subj_uri), pred, URIRef(triple.object_uri)))
        elif triple.object_value is not None:
            g.add((URIRef(subj_uri), pred, Literal(triple.object_value)))

    return g


def _serialize_to_ttl(
    triples: list[ExtractedTriple],
    typings: list[TermTypingResult],
    schema: SchemaResult,
) -> str:
    """Serialize accepted triples + type assertions to Turtle format."""
    return _build_graph(triples, typings, schema).serialize(format="turtle")


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
        return await _taxonomy_mod.discover_taxonomy(
            self._llm, schema, text, candidate_classes
        )

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
        shapes_path: str | Path | None = None,
    ) -> PopulationResult:
        """Run A+B+C in sequence; optionally load accepted triples to Fuseki.

        Args:
            text: Source text to process.
            auto_load: If True, serialize accepted triples to TTL and load into the store.
            min_confidence: Minimum confidence to include a result.
            shapes_path: Optional SHACL shapes file. When provided and the file
                exists, generated triples are validated before load; violations
                are dropped and recorded in PopulationResult.violations.

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
        taxonomy = await _taxonomy_mod.discover_taxonomy(
            self._llm, schema, text, candidate_classes=None
        )
        taxonomy = [t for t in taxonomy if t.confidence >= min_confidence]

        # 4. Task C — relation extraction
        entity_labels = [t.term for t in typings]
        triples = await _relation_mod.extract_relations(
            self._llm,
            schema,
            text,
            entities=entity_labels or None,
            min_confidence=min_confidence,
        )

        loaded: int | None = None
        violations: list[ShaclViolation] = []
        if auto_load and (triples or typings):
            loaded, violations = await self._load_triples(
                triples, typings, schema, shapes_path=shapes_path
            )

        return PopulationResult(
            term_typings=typings,
            taxonomy_proposals=taxonomy,
            triples=triples,
            triples_loaded=loaded,
            violations=violations,
        )

    async def _extract_terms(self, text: str, schema: SchemaResult) -> list[str]:
        """Extract entity labels from text using a structured LLM call."""
        messages = [{"role": "user", "content": f"Text:\n{text[:2000]}"}]
        try:
            raw = await structured_call(
                self._llm, messages, _TERM_EXTRACTION_TOOL, system=_TERM_SYSTEM
            )
            return [t for t in raw.get("terms", []) if isinstance(t, str) and t.strip()]
        except Exception as exc:
            logger.warning("_extract_terms failed: %s", exc)
            return []

    async def populate_from_structured(
        self,
        path: str | Path,
        class_uri: str | None = None,
        id_column: str | None = None,
        batch_size: int = 50,
        min_confidence: float = 0.7,
        auto_load: bool = False,
        shapes_path: str | Path | None = None,
    ) -> PopulationResult:
        """Populate ABox from a structured file (CSV/JSON/JSONL).

        Columns are mapped to TBox property URIs via LLM (propose_mapping).
        The mapping is cached in a sidecar file (<filename>.mapping.json) and
        reused on subsequent runs if the schema hash is still valid.

        When the cache is valid, no LLM calls are made — triples are built
        directly from the mapping.  When the cache is absent or stale,
        propose_mapping is called once per batch.

        Args:
            path: Path to the structured data file (.csv, .json, .jsonl).
            class_uri: TBox class URI for the rows (e.g. "pk:Pokemon"). Optional.
            id_column: Column whose value forms the subject URI slug. If None,
                a deterministic uuid5 is used (idempotent across re-runs).
            batch_size: Rows per batch.
            min_confidence: Minimum confidence threshold for a column mapping.
            auto_load: If True, load each batch's triples into the graph store.

        Returns:
            PopulationResult with all generated triples.
        """
        path = Path(path)
        rows = read_structured(path)
        if not rows:
            return PopulationResult()

        schema = await self._store.get_schema()
        if not schema.properties:
            raise ValueError(
                "TBox에 속성이 없습니다. 먼저 스키마를 로드하세요: ontorag load schema <파일>"
            )
        mapping_path = path.parent / (path.name + ".mapping.json")

        # --- Load or invalidate cached column mapping ---
        mapping: MappingFile | None = None
        if mapping_path.exists():
            try:
                candidate = load_mapping(mapping_path)
                if validate_mapping_hash(candidate, schema):
                    mapping = candidate
            except Exception:
                pass

        use_cache = mapping is not None

        if not use_cache:
            mapping = MappingFile(
                schema_hash=compute_schema_hash(schema),
                class_uri=class_uri,
                id_column=id_column,
                columns=[],
                last_row=0,
            )

        col_map: dict[str, ColumnMapping] = {
            cm.column_name: cm for cm in mapping.columns
        }
        effective_id_column = id_column if id_column is not None else mapping.id_column
        namespace = _resolve_namespace(class_uri or mapping.class_uri, schema)

        all_triples: list[ExtractedTriple] = []
        total_loaded: int | None = 0 if auto_load else None
        all_violations: list[ShaclViolation] = []

        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start : batch_start + batch_size]

            if not use_cache:
                # Propose mapping for this batch (1 LLM call per batch)
                columns = list(batch[0].keys())
                col_mappings = await propose_mapping(
                    self._llm,
                    schema,
                    columns,
                    class_uri=class_uri,
                    filename=path.name,
                )
                if col_mappings:
                    mapping.columns = col_mappings
                    col_map = {cm.column_name: cm for cm in col_mappings}
                else:
                    logger.warning(
                        "propose_mapping returned no columns for batch at row %d — skipping cache write",
                        batch_start,
                    )

            batch_triples: list[ExtractedTriple] = []
            for row_idx, row in enumerate(batch, start=batch_start):
                subject_uri = mint_subject_uri(
                    row,
                    id_column=effective_id_column,
                    namespace=namespace,
                    filepath=str(path),
                    row_index=row_idx,
                )
                subject_label = (
                    str(row[effective_id_column])
                    if effective_id_column and effective_id_column in row
                    else str(row_idx)
                )
                for col_name, value in row.items():
                    cm = col_map.get(col_name)
                    if cm is None or cm.predicate_uri is None:
                        continue
                    if cm.confidence < min_confidence:
                        continue
                    # Skip null/empty values — avoids "None"^^xsd:string literals
                    if value is None or (isinstance(value, str) and not value.strip()):
                        continue
                    batch_triples.append(
                        ExtractedTriple(
                            subject_label=subject_label,
                            subject_uri=subject_uri,
                            predicate_uri=cm.predicate_uri,
                            object_uri=None,
                            object_value=str(value),
                            confidence=cm.confidence,
                        )
                    )

            all_triples.extend(batch_triples)

            if auto_load and batch_triples:
                loaded, batch_violations = await self._load_triples(
                    batch_triples, [], schema, shapes_path=shapes_path
                )
                total_loaded = (total_loaded or 0) + loaded
                all_violations.extend(batch_violations)

            mapping.last_row = batch_start + len(batch)
            if mapping.columns:
                save_mapping(mapping, mapping_path)

        return PopulationResult(
            triples=all_triples,
            triples_loaded=total_loaded,
            violations=all_violations,
        )

    async def _load_triples(
        self,
        triples: list[ExtractedTriple],
        typings: list[TermTypingResult],
        schema: SchemaResult,
        shapes_path: str | Path | None = None,
    ) -> tuple[int, list[ShaclViolation]]:
        """Serialize triples, optionally validate against SHACL, and load."""
        graph = _build_graph(triples, typings, schema)

        violations: list[ShaclViolation] = []
        if shapes_path is not None:
            sp = Path(shapes_path)
            if sp.exists():
                graph, violations = _shacl.validate(graph, sp)
            else:
                logger.warning(
                    "shapes_path %s does not exist — skipping SHACL validation", sp
                )

        if len(graph) == 0:
            return 0, violations

        ttl = graph.serialize(format="turtle")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ttl", delete=False, encoding="utf-8"
        ) as f:
            f.write(ttl)
            tmp_path = f.name
        try:
            result = await self._store.load_rdf(tmp_path, mode="data")
            return result.triples_loaded, violations
        finally:
            Path(tmp_path).unlink(missing_ok=True)
