from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator


# ── Result types ─────────────────────────────────────────────────────────────


class LoadResult(BaseModel):
    """Result of loading an RDF file into the store."""

    triples_loaded: int
    source: str
    mode: Literal["schema", "data"]
    ontology: str | None = Field(
        default=None,
        description="Ontology id the triples were loaded under. None = the "
        "default (legacy single-ontology) graph pair.",
    )


class ClassSummary(BaseModel):
    """Compact class entry for the schema overview.

    Property list is intentionally omitted — call get_class_detail(uri)
    for per-class property details. This keeps get_schema() token-efficient.
    """

    uri: str
    label: str | None = None
    parent_uri: str | None = None
    property_count: int = 0
    instance_count: int = 0
    description: str | None = Field(
        default=None,
        description="rdfs:comment or skos:definition — natural-language "
        "meaning authored on the TBox. Surfaced into the LLM's "
        "system prompt so prompt logic stays domain-agnostic.",
    )


class PropertySummary(BaseModel):
    """Compact property entry."""

    uri: str
    label: str | None = None
    prop_type: Literal["object", "datatype", "annotation"] = "annotation"
    domain_uri: str | None = None
    range_uri: str | None = None
    is_transitive: bool = False
    inverse_of_uri: str | None = None
    description: str | None = Field(
        default=None,
        description="rdfs:comment or skos:definition for this property.",
    )


class ClassDetail(BaseModel):
    """Full detail for one ontology class.

    Returned by get_class_detail(uri). Use this instead of get_schema()
    when the LLM needs property-level information for a specific class.
    """

    uri: str
    label: str | None = None
    description: str | None = None
    parent_uris: list[str] = Field(default_factory=list)
    child_uris: list[str] = Field(default_factory=list)
    properties: list[PropertySummary] = Field(default_factory=list)
    instance_count: int = 0
    sample_instance_uris: list[str] = Field(default_factory=list)


class SchemaResult(BaseModel):
    """Compact schema overview for LLM context.

    Contains class hierarchy with property counts only.
    Full property lists are available via get_class_detail(class_uri).

    Token estimate: ~30 tokens per class — 50 classes ≈ 1,500 tokens.
    (vs. full schema dump: ~6,000 tokens for same ontology)
    """

    total_classes: int
    total_properties: int
    namespaces: dict[str, str]
    classes: list[ClassSummary]
    properties: list[PropertySummary] = Field(
        default_factory=list,
        description="All TBox properties. Populated when the store supports it.",
    )


class QueryResult(BaseModel):
    """Result of a SPARQL query (internal use only)."""

    columns: list[str]
    rows: list[dict[str, Any]]
    total: int


class EntityResult(BaseModel):
    """A single ontology entity with its properties."""

    uri: str
    label: str | None
    class_uri: str | None
    properties: dict[str, Any]
    inferred: bool = False


class TraversalResult(BaseModel):
    """Result of a graph traversal or path query."""

    start_uri: str
    end_uri: str | None = None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    depth_reached: int


class AggregateResult(BaseModel):
    """Result of an aggregation query (group_by + agg function)."""

    group_value: str
    result: int | float


class SearchHit(BaseModel):
    """A single full-text (BM25) search hit.

    Returned by the optional `search_text` capability. `score` is the
    backend's relevance score (Lucene BM25 for Neo4j) — higher is more
    relevant; absolute values are only meaningful relative to other hits in
    the same response.
    """

    uri: str
    label: str | None = None
    class_uri: str | None = None
    score: float
    matched_property: str | None = Field(
        default=None,
        description="Property URI whose value matched, when the backend reports it.",
    )


class SimilarHit(BaseModel):
    """A single nearest-neighbour result from `find_similar`.

    `mode` records which embedding produced the hit: "structural" (graph
    topology, e.g. FastRP), "textual" (semantic content embedding), or
    "hybrid" (rank-fused). `score` is cosine similarity for single-mode hits
    (0–1, higher = closer) or the fused rank score for hybrid.
    """

    uri: str
    label: str | None = None
    class_uri: str | None = None
    score: float
    mode: Literal["structural", "textual", "hybrid"]


class StoreStatus(BaseModel):
    """Current state of the graph store."""

    connected: bool
    store_type: str
    triple_count: int | None
    schema_loaded: bool
    data_loaded: bool


# ── Layer 1 input types ───────────────────────────────────────────────────────


class FilterOp(str, Enum):
    """Comparison operators for entity filters."""

    eq = "="
    ne = "!="
    gt = ">"
    gte = ">="
    lt = "<"
    lte = "<="
    contains = "contains"
    starts_with = "starts_with"


class EntityFilter(BaseModel):
    """A single filter condition for entity queries.

    Example:
        EntityFilter(property="foaf:age", op=FilterOp.gt, value=30)
    """

    property: str = Field(description="Property URI or prefixed name (e.g. foaf:age).")
    op: FilterOp = Field(default=FilterOp.eq)
    value: str | int | float | bool


class TraversalDirection(str, Enum):
    """Direction of graph traversal."""

    outgoing = "outgoing"
    incoming = "incoming"
    both = "both"


class AggFunc(str, Enum):
    """Supported aggregation functions."""

    count = "count"
    sum = "sum"
    avg = "avg"
    min = "min"
    max = "max"


# ── Layer 2 — JSON DSL (query_pattern) ──────────────────────────────────────

_VAR_RE = re.compile(r"^\?[a-zA-Z][a-zA-Z0-9_]*$")
_SAFE_TERM_RE = re.compile(
    r"^(\?[a-zA-Z][a-zA-Z0-9_]*"  # ?variable
    r"|<[^<>\"{}|\\^`\s]+>"  # <URI>
    r"|[a-zA-Z_][a-zA-Z0-9_\-]*:[^\s\"{}|\\^`]+"  # prefixed:name
    r"|\"[^\"]*\"(@[a-zA-Z\-]+)?"  # "literal" or "literal"@lang
    r"|\"[^\"]*\"\^\^<[^<>]+>"  # "literal"^^<type>
    r"|-?[0-9]+(\.[0-9]+)?"  # numeric literal
    r"|true|false)$"  # boolean
)


class PatternTriple(BaseModel):
    """A single BGP triple pattern used in query_pattern DSL.

    Each term must be a variable (?var), URI (<...>), prefixed name (prefix:local),
    or literal ("value"). Injection-safe: validated against strict patterns.
    """

    s: str = Field(description="Subject: variable (?var), URI, or prefixed name.")
    p: str = Field(description="Predicate: variable (?var), URI, or prefixed name.")
    o: str = Field(description="Object: variable, URI, prefixed name, or literal.")

    @field_validator("s", "p", "o")
    @classmethod
    def validate_term(cls, v: str) -> str:
        """Reject any term that could enable SPARQL injection."""
        if not _SAFE_TERM_RE.match(v.strip()):
            raise ValueError(
                f"Unsafe or malformed RDF term: {v!r}. "
                "Expected ?variable, <URI>, prefix:local, or quoted literal."
            )
        return v.strip()


class PatternFilter(BaseModel):
    """Filter clause in query_pattern DSL."""

    var: str = Field(description="Variable to filter (e.g. ?year).")
    op: Literal["=", "!=", ">", ">=", "<", "<="]
    value: str | int | float

    @field_validator("var")
    @classmethod
    def validate_var(cls, v: str) -> str:
        if not _VAR_RE.match(v):
            raise ValueError(f"Invalid SPARQL variable: {v!r}")
        return v


class PatternQuery(BaseModel):
    """JSON DSL query translated to SPARQL internally (Layer 2).

    Replaces raw SPARQL exposure. LLM calls this instead of writing SPARQL.
    Server translates to SPARQL; injection is prevented by structural validation.

    Example::

        PatternQuery(
            select=["?person", "?name"],
            where=[
                PatternTriple(s="?person", p="rdf:type", o="ex:Researcher"),
                PatternTriple(s="?person", p="foaf:name", o="?name"),
            ],
            filters=[PatternFilter(var="?name", op="!=", value="")],
            limit=50,
        )
    """

    select: list[str] = Field(
        description="Variables to return, e.g. ['?person', '?name']."
    )
    where: list[PatternTriple] = Field(min_length=1)
    filters: list[PatternFilter] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)
    distinct: bool = False

    @field_validator("select")
    @classmethod
    def validate_select_vars(cls, vs: list[str]) -> list[str]:
        for v in vs:
            if not _VAR_RE.match(v):
                raise ValueError(f"Invalid SELECT variable: {v!r}")
        return vs


# ── GraphStore Protocol ───────────────────────────────────────────────────────


@runtime_checkable
class GraphStore(Protocol):
    """Protocol for graph store backends.

    3-layer tool design:
      Layer 1 — intent-based tools (get_schema, find_entities, describe_entity,
                traverse, find_path, count_entities, aggregate, find_related)
      Layer 2 — JSON DSL escape hatch (query_pattern)
      Layer 3 — raw SPARQL, internal only, NOT exposed via MCP (_sparql_select)

    All MCP-exposed tools depend on this interface.
    """

    # ── Load ─────────────────────────────────────────────────────────────────

    async def load_rdf(
        self,
        path: str,
        mode: Literal["schema", "data", "auto"] = "auto",
        replace: bool = False,
        ontology: str | None = None,
    ) -> LoadResult:
        """Load an RDF file (TBox or ABox) into the store.

        Args:
            path: Local file path (TTL, JSON-LD, RDF/XML).
            mode: "schema" replaces the TBox graph; "data" appends to ABox.
            replace: If True and mode resolves to "data", replace the entire
                data graph instead of appending. Ignored for schema, which is
                always replaced (one canonical TBox per store).
            ontology: Ontology id to load under (slug ``^[a-zA-Z0-9_-]+$``).
                None loads into the default (legacy single-ontology) graphs;
                a named id isolates the triples in a per-ontology graph pair.

        Returns:
            Triple count, resolved mode, and ontology id.
        """
        ...

    # ── Layer 1 tools ────────────────────────────────────────────────────────

    async def get_schema(self, ontology: str | None = None) -> SchemaResult:
        """Return a compact schema overview for LLM context.

        Returns class hierarchy and property counts only.
        For full property detail of a specific class, use get_class_detail(uri).

        Args:
            ontology: Ontology id for scoped query, or None for union (all
                ontologies). None preserves backward-compatible single-ontology
                behavior.

        Returns:
            Compact SchemaResult (~30 tokens per class).
        """
        ...

    async def get_class_detail(
        self, class_uri: str, ontology: str | None = None
    ) -> ClassDetail:
        """Return full TBox detail for a single ontology class.

        Designed for progressive disclosure: call get_schema() first to identify
        relevant classes, then call this for the classes you need.

        Args:
            class_uri: Full URI or prefixed name of the class.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            ClassDetail with all properties, parent/child classes, and sample instances.

        Raises:
            KeyError: If the class does not exist in the schema.
        """
        ...

    async def find_entities(
        self,
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[EntityResult]:
        """Find instances of a class matching optional filter conditions.

        Inference-aware: includes subclass instances when the store has
        rdfs:subClassOf inference enabled.

        Args:
            class_uri: Full URI or prefixed name of the ontology class.
            filters: Optional list of property-value conditions.
            limit: Maximum number of results.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            Matching entity list with properties.
        """
        ...

    async def describe_entity(
        self,
        uri: str,
        predicates: list[str] | None = None,
        ontology: str | None = None,
    ) -> EntityResult:
        """Return all (or selected) properties and relationships of an entity.

        Args:
            uri: Full URI of the entity.
            predicates: Optional list of predicate URIs to restrict output.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            Entity with properties, including owl:inverseOf relationships.

        Raises:
            KeyError: If the entity does not exist in the store.
        """
        ...

    async def traverse(
        self,
        start_uri: str,
        predicate: str | None = None,
        max_depth: int = 2,
        direction: TraversalDirection = TraversalDirection.outgoing,
        ontology: str | None = None,
    ) -> TraversalResult:
        """Traverse the graph from a starting node.

        Inference-aware: follows owl:TransitiveProperty closures when enabled.

        Args:
            start_uri: URI of the starting node.
            predicate: Predicate URI to follow. None means all predicates.
            max_depth: Maximum traversal depth (hard limit: 6).
            direction: outgoing, incoming, or both.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            Nodes and edges reachable from start_uri.
        """
        ...

    async def find_path(
        self,
        uri_a: str,
        uri_b: str,
        max_depth: int = 4,
        ontology: str | None = None,
    ) -> TraversalResult:
        """Find the shortest path between two entities.

        Args:
            uri_a: Starting entity URI.
            uri_b: Target entity URI.
            max_depth: Maximum path length (hard limit: 6).
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            Path nodes and edges, or empty result if no path found.
        """
        ...

    async def property_path_closure(
        self,
        predicate_uri: str,
        start_uri: str | None = None,
        start_label: str | None = None,
        start_class_uri: str | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all entities reachable via a transitive predicate.

        Three start modes (exactly one of the three groups must be given):

        - **Instance closure** (``start_uri``):
          ``<start_uri> <pred>+ ?reached``.
        - **Label lookup + instance closure** (``start_label`` ± ``start_class_uri``):
          single round-trip — label resolved case- and lang-tag-insensitive,
          then closure from the matched instance.
        - **Class-wide closure** (``start_class_uri`` alone): every
          instance of the class is a start node: ``?start a <Class> ;
          <pred>+ ?reached``. Use for "any X is transitively …"
          questions.

        Args:
            predicate_uri: Predicate to follow (should be
                owl:TransitiveProperty for the result to be meaningful).
            start_uri: Instance URI to start from. Mode 1.
            start_label: rdfs:label of the start instance. Mode 2.
            start_class_uri: Class URI — disambiguates Mode 2, or
                triggers Mode 3 on its own.
            limit: Max entities to return (default 100).
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            List of ``{"uri": str, "label": str | None}`` ordered by URI.
            Empty list means no closure result.
        """
        ...

    async def count_entities(
        self,
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        ontology: str | None = None,
    ) -> int:
        """Count instances of a class matching optional filters.

        Args:
            class_uri: Full URI or prefixed name of the ontology class.
            filters: Optional filter conditions.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            Number of matching instances.
        """
        ...

    async def aggregate(
        self,
        class_uri: str,
        group_by: str,
        agg: AggFunc = AggFunc.count,
        ontology: str | None = None,
    ) -> list[AggregateResult]:
        """Group instances by a property and apply an aggregation function.

        Args:
            class_uri: Class to aggregate over.
            group_by: Property URI or prefixed name to group by.
            agg: Aggregation function (count, sum, avg, min, max).
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            List of group_value → aggregated_result pairs.
        """
        ...

    async def find_related(
        self,
        class_uri_a: str,
        predicate: str,
        class_uri_b: str,
        filters_a: list[EntityFilter] | None = None,
        filters_b: list[EntityFilter] | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find pairs of entities connected by a predicate (multi-hop join).

        Args:
            class_uri_a: Class of the subject entity.
            predicate: Connecting predicate URI.
            class_uri_b: Class of the object entity.
            filters_a: Optional filters for subject entities.
            filters_b: Optional filters for object entities.
            limit: Maximum result pairs.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            List of {entity_a: EntityResult, entity_b: EntityResult} dicts.
        """
        ...

    # ── Layer 2 tool ─────────────────────────────────────────────────────────

    async def query_pattern(self, query: PatternQuery) -> QueryResult:
        """Execute a structured JSON DSL query (translated to SPARQL internally).

        Use when Layer 1 tools cannot express the required query.
        Input is structurally validated; SPARQL injection is not possible.

        Args:
            query: PatternQuery DSL object.

        Returns:
            Query results as column names and rows.
        """
        ...

    # ── Dump ─────────────────────────────────────────────────────────────────

    async def dump_graph(
        self,
        target: Literal["schema", "data", "all"],
        fmt: Literal["ttl", "json", "jsonl", "xlsx"] = "ttl",
        ontology: str | None = None,
    ) -> bytes:
        """Export one or both named graphs as bytes in the requested format.

        Args:
            target: "schema" (TBox only), "data" (ABox only), or "all" (both).
            fmt: Serialisation format — "ttl" (Turtle), "json" (triple array),
                 "jsonl" (one triple per line), "xlsx" (spreadsheet).
            ontology: Ontology id to export, or None for the default/legacy
                graph pair.

        Returns:
            Serialised bytes ready to write to a file or HTTP response.
        """
        ...

    async def clear_graph(
        self,
        target: Literal["schema", "data", "all"],
        ontology: str | None = None,
    ) -> dict[str, int]:
        """Drop one or both graphs and report how many triples were removed.

        Args:
            target: "schema" clears the TBox, "data" clears the ABox,
                "all" clears both.
            ontology: Ontology id to clear, or None for the default/legacy
                graph pair.

        Returns:
            Mapping of graph name → triple count removed before deletion.
        """
        ...

    # ── Store management ─────────────────────────────────────────────────────

    async def status(self) -> StoreStatus:
        """Return connection state and triple counts per named graph.

        Returns:
            Connected flag, total triple count, schema/data load state.
        """
        ...

    async def aclose(self) -> None:
        """Release backend resources (HTTP clients, driver sessions, sockets).

        Idempotent: safe to call when the store is already closed. Callers
        (CLI commands, request lifecycle) invoke this for clean shutdown.
        """
        ...
