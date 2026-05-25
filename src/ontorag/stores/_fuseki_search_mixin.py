from __future__ import annotations

"""jena-text (Lucene) full-text search mixin for FusekiStore.

Implements ``search_text()`` using Apache Jena's ``text:query`` SPARQL
property function.  The text index is created via the assembler config
(``docker/fuseki/config.ttl``) — no application-side index management
is needed; Jena updates the index synchronously on every GSP write.

This is a *capability*, not part of the GraphStore protocol.  Backends
without ``search_text`` receive a 501 via the route's ``getattr`` guard.

Asymmetry vs Neo4j:
  - Neo4j discovers all string-valued properties dynamically and indexes
    them all (``ontorag_fulltext`` index covers everything found after
    each load_rdf).
  - Fuseki's assembler requires a static predicate list.  We index the
    four standard annotation predicates: ``rdfs:label``, ``rdfs:comment``,
    ``skos:prefLabel``, ``skos:definition``.  Domain-specific predicates
    (e.g. ``pk:name``) are not indexed here; instances must use one of
    the four standard predicates to be searchable.
  - Scores are Lucene TF-IDF/BM25 floats — not normalised to 0-1.
    This is the same as Neo4j (both use Lucene under the hood).
"""

import logging
import re
from typing import TYPE_CHECKING, Any

from ontorag.core.ontology import data_graph_uri, schema_graph_uri, validate_ontology_id
from ontorag.stores.base import SearchHit

if TYPE_CHECKING:
    from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)

# Legacy default graph URIs — used when ontology=None (union default graph).
_DATA = "urn:ontorag:data"
_SCHEMA = "urn:ontorag:schema"

# Vocabulary type URIs that should not be reported as the instance's class_uri.
# These appear as rdf:type triples for ontology classes themselves, not for
# ABox instances — mirrors _TBOX_TYPE_URIS in the Neo4j mixin.
_TBOX_TYPE_URIS: frozenset[str] = frozenset(
    {
        "http://www.w3.org/2002/07/owl#Class",
        "http://www.w3.org/2002/07/owl#ObjectProperty",
        "http://www.w3.org/2002/07/owl#DatatypeProperty",
        "http://www.w3.org/2002/07/owl#AnnotationProperty",
        "http://www.w3.org/2002/07/owl#TransitiveProperty",
        "http://www.w3.org/2002/07/owl#FunctionalProperty",
        "http://www.w3.org/2002/07/owl#InverseFunctionalProperty",
        "http://www.w3.org/2002/07/owl#SymmetricProperty",
        "http://www.w3.org/2002/07/owl#AsymmetricProperty",
        "http://www.w3.org/2002/07/owl#ReflexiveProperty",
        "http://www.w3.org/2002/07/owl#IrreflexiveProperty",
        "http://www.w3.org/2002/07/owl#Ontology",
        "http://www.w3.org/2000/01/rdf-schema#Class",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property",
    }
)

# Characters that would break out of the SPARQL string literal surrounding
# the Lucene query string.  We escape backslash first so the escaping of
# other characters does not double-escape it.
_SPARQL_STRING_ESCAPE: list[tuple[str, str]] = [
    ("\\", "\\\\"),  # backslash must come first
    ('"', '\\"'),
    ("\n", "\\n"),
    ("\r", "\\r"),
    ("\t", "\\t"),
]

# Characters that are special in Lucene query syntax.  We do NOT auto-escape
# them — callers are expected to pass intentional Lucene queries (e.g.
# "pika*" uses a wildcard intentionally).  We only strip characters that
# cannot appear safely in a SPARQL string literal at all.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _escape_lucene_query_for_sparql(query: str) -> str:
    """Escape a Lucene query string so it is safe inside a SPARQL string literal.

    Escapes SPARQL string-breaking characters (backslash, double-quote,
    newline, carriage-return, tab) using standard SPARQL string escape
    sequences.  Strips ASCII control characters that have no valid meaning
    in a query string.

    Lucene special characters (``+ - && || ! ( ) { } [ ] ^ ~ * ? : /``)
    are intentionally left unescaped — callers may want wildcard or phrase
    queries.

    Args:
        query: Raw Lucene query string from the user/LLM.

    Returns:
        Escaped string safe for embedding inside ``"..."`` in SPARQL.

    Raises:
        ValueError: If the query is empty after stripping control characters.
    """
    # Strip control characters first.
    cleaned = _CONTROL_CHAR_RE.sub("", query)
    for raw, escaped in _SPARQL_STRING_ESCAPE:
        cleaned = cleaned.replace(raw, escaped)
    if not cleaned.strip():
        raise ValueError("Query string is empty after sanitisation.")
    return cleaned


class _FusekiSearchMixin:
    """jena-text full-text search capability mixed into FusekiStore.

    Not part of the GraphStore protocol — exposed as an optional capability.
    The MCP route guards with ``getattr(store, "search_text", None)``.
    """

    # Provided by FusekiStore at runtime.
    _sparql_select: Any

    async def search_text(
        self: "FusekiStore",
        query: str,
        class_uri: str | None = None,
        limit: int = 20,
        ontology: str | None = None,
    ) -> list[SearchHit]:
        """Search ontology instance data using jena-text (Lucene) BM25 scoring.

        Uses ``text:query`` SPARQL property function backed by the Lucene
        index defined in the assembler config.  The query string is embedded
        as a SPARQL string literal (escaped, never raw-interpolated).

        If ``class_uri`` is provided, results are restricted to instances
        of that class or any of its subclasses using the same cross-graph
        ``rdfs:subClassOf*`` pattern as ``find_entities``.

        Results match only predicates indexed in the assembler config:
        ``rdfs:label``, ``rdfs:comment``, ``skos:prefLabel``,
        ``skos:definition``.

        Args:
            query: Lucene query string (e.g. "피카츄", "pika*").
            class_uri: Optional full URI of a class to restrict results.
            limit: Maximum number of hits to return (default 20, max 200).
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            List of SearchHit ordered by Lucene score descending, or an
            empty list when no matches are found.

        Raises:
            ValueError: If the query string is empty or contains only
                        control characters, or ontology id is invalid.
            httpx.HTTPStatusError: If Fuseki returns an HTTP error.
        """
        ontology = validate_ontology_id(ontology)
        safe_query = _escape_lucene_query_for_sparql(query)

        # Over-fetch so deduplication (a node hit for multiple triples)
        # does not under-deliver the caller's requested limit.
        internal_limit = max(limit * 5, limit + 50)

        try:
            rows = await self._run_text_query(
                safe_query, class_uri, internal_limit, ontology=ontology
            )
        except Exception as exc:
            logger.warning(
                "search_text: SPARQL query failed (%s); returning empty results.", exc
            )
            return []

        if not rows:
            return []

        # De-duplicate by URI — keep the highest-scoring row per entity and
        # prefer a non-vocabulary rdf:type for the reported class_uri.
        seen: dict[str, SearchHit] = {}
        for row in rows:
            uri = row.get("inst", {}).get("value")
            if not uri:
                continue
            score_raw = row.get("score", {}).get("value")
            if score_raw is None:
                continue
            score = float(score_raw)

            label_binding = row.get("label", {})
            label: str | None = label_binding.get("value") if label_binding else None

            type_binding = row.get("type", {})
            raw_type: str | None = type_binding.get("value") if type_binding else None
            cls_hit: str | None = (
                raw_type
                if raw_type and raw_type not in _TBOX_TYPE_URIS
                else None
            )

            existing = seen.get(uri)
            if existing is not None:
                # Keep the best score; backfill class_uri if we now have one.
                if cls_hit and existing.class_uri is None:
                    existing = existing.model_copy(update={"class_uri": cls_hit})
                    seen[uri] = existing
                if existing.score >= score:
                    continue

            # Preserve an already-resolved class_uri if this winning row lacks one.
            resolved_cls = cls_hit or (existing.class_uri if existing else None)

            seen[uri] = SearchHit(
                uri=uri,
                label=label,
                class_uri=resolved_cls,
                score=score,
            )

        ordered = sorted(seen.values(), key=lambda h: h.score, reverse=True)
        return ordered[:limit]

    async def _run_text_query(
        self: "FusekiStore",
        safe_query: str,
        class_uri: str | None,
        internal_limit: int,
        ontology: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute the jena-text SPARQL query and return raw result rows.

        The query string is embedded as a pre-escaped SPARQL literal — it
        has already been processed by ``_escape_lucene_query_for_sparql``
        before being passed here.

        Args:
            safe_query: SPARQL-safe (escaped) Lucene query string.
            class_uri: Optional class URI for subClassOf-aware filtering.
            internal_limit: Over-fetch cap before Python-side deduplication.
            ontology: Validated ontology id or None for union default graph.

        Returns:
            Raw SPARQL result bindings (list of dicts).
        """
        from ontorag.core.sparql import uri_ref  # local to break circular import

        # Resolve graph URIs for scoped queries.
        # ontology=None: no GRAPH wrapper (union default graph).
        # ontology=id: scope data and schema queries to per-ontology graphs.
        if ontology is not None:
            data_g: str | None = data_graph_uri(ontology)
            schema_g: str | None = schema_graph_uri(ontology)
        else:
            data_g = None
            schema_g = None

        def _g(uri: str | None, body: str) -> str:
            """Local helper to emit GRAPH <uri> { body } or just { body }."""
            if uri is None:
                return f"{{ {body} }}"
            return f"GRAPH <{uri}> {{ {body} }}"

        if class_uri is not None:
            # Validate class_uri — uri_ref raises ValueError on unsafe input.
            safe_cls = uri_ref(class_uri)
            sparql = f"""PREFIX text: <http://jena.apache.org/text#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?inst ?score ?label ?type WHERE {{
  (?inst ?score) text:query ("{safe_query}" {internal_limit}) .
  {_g(data_g, f"?inst rdf:type ?itype .")}
  {{ {_g(schema_g, f"?itype rdfs:subClassOf* {safe_cls} .")} }}
  UNION
  {{ FILTER(?itype = {safe_cls}) }}
  OPTIONAL {{ {_g(data_g, "?inst rdfs:label ?label .")} }}
  OPTIONAL {{
    {_g(data_g, "?inst rdf:type ?type . FILTER(!isBlank(?type))")}
  }}
}}
ORDER BY DESC(?score)
LIMIT {internal_limit}"""
        else:
            sparql = f"""PREFIX text: <http://jena.apache.org/text#>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?inst ?score ?label ?type WHERE {{
  (?inst ?score) text:query ("{safe_query}" {internal_limit}) .
  {_g(data_g, "?inst rdf:type ?anytype .")}
  OPTIONAL {{ {_g(data_g, "?inst rdfs:label ?label .")} }}
  OPTIONAL {{
    {_g(data_g, "?inst rdf:type ?type . FILTER(!isBlank(?type))")}
  }}
}}
ORDER BY DESC(?score)
LIMIT {internal_limit}"""

        raw = await self._sparql_select(sparql)
        return raw.get("results", {}).get("bindings", [])
