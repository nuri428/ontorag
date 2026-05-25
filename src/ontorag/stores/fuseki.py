from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Literal

import httpx
from rdflib import Graph

if TYPE_CHECKING:
    from ontorag.stores._qdrant import QdrantWrapper

from ontorag.core.loader import detect_mode, parse_rdf
from ontorag.core.sparql import STANDARD_PREFIXES, pattern_to_sparql, uri_ref
from ontorag.stores._entity_mixin import _EntityMixin
from ontorag.stores._fuseki_embedding_mixin import _FusekiEmbeddingMixin
from ontorag.stores._fuseki_search_mixin import _FusekiSearchMixin
from ontorag.stores._traversal_mixin import _TraversalMixin
from ontorag.stores.base import (
    ClassDetail,
    ClassSummary,
    LoadResult,
    PatternQuery,
    PropertySummary,
    QueryResult,
    SchemaResult,
    StoreStatus,
)

logger = logging.getLogger(__name__)

SCHEMA_GRAPH_URI = "urn:ontorag:schema"
DATA_GRAPH_URI = "urn:ontorag:data"


class FusekiStore(_EntityMixin, _FusekiEmbeddingMixin, _FusekiSearchMixin, _TraversalMixin):
    """Apache Jena Fuseki graph store adapter.

    Uses SPARQL 1.1 endpoints and the RDF Graph Store Protocol (GSP).
    Named graphs:
      - urn:ontorag:schema  → TBox (ontology class/property declarations)
      - urn:ontorag:data    → ABox (instance data)

    Inference layer: enable RDFS/OWL inference in Fuseki's dataset configuration
    (ja:OntModelSpec) to make Layer 1 tools automatically subclass- and
    transitive-property-aware without changing application code.
    """

    def __init__(
        self,
        url: str,
        dataset: str,
        user: str,
        password: str,
    ) -> None:
        """Initialize the Fuseki store adapter.

        Args:
            url: Fuseki base URL (e.g. http://localhost:3030).
            dataset: Dataset name (e.g. "ontology").
            user: HTTP Basic auth username.
            password: HTTP Basic auth password.
        """
        # Extra namespace prefixes captured from loaded RDF files
        self._namespaces: dict[str, str] = {}
        self._base = url.rstrip("/")
        self._dataset = dataset
        self._auth = httpx.BasicAuth(user, password)
        self._client: httpx.AsyncClient | None = None
        self._dataset_ensured: bool = False
        # Lazily created by the embedding mixin's _get_qdrant() on first use.
        self._qdrant: QdrantWrapper | None = None

    @classmethod
    def from_env(cls) -> FusekiStore:
        """Create a FusekiStore from environment variables.

        Reads: FUSEKI_URL, FUSEKI_DATASET, FUSEKI_USER, FUSEKI_PASSWORD.

        Returns:
            Configured FusekiStore instance.
        """
        return cls(
            url=os.environ.get("FUSEKI_URL", "http://localhost:3030"),
            dataset=os.environ.get("FUSEKI_DATASET", "ontorag"),
            user=os.environ.get("FUSEKI_USER", "admin"),
            password=os.environ.get("FUSEKI_PASSWORD", "admin"),
        )

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(auth=self._auth, timeout=60.0)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client and Qdrant client (if created)."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        # Close the Qdrant client if the embedding mixin ever created one.
        qdrant = getattr(self, "_qdrant", None)
        if qdrant is not None:
            await qdrant.aclose()
            self._qdrant = None  # type: ignore[assignment]

    async def _ensure_dataset(self) -> None:
        """Create the dataset via the admin API if it does not exist.

        Fuseki 5.x no longer auto-creates datasets from the FUSEKI_DATASET
        environment variable — explicit creation via POST /$/datasets is required.
        This is a no-op if the dataset already exists (409 Conflict is silently ignored).
        """
        if self._dataset_ensured:
            return
        client = await self._http()
        response = await client.post(
            f"{self._base}/$/datasets",
            data={"dbName": self._dataset, "dbType": "mem"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # 409 = already exists (persistent Fuseki), 403 = admin API blocked but
        # dataset was pre-created by --mem flag — both are safe to ignore.
        if response.status_code not in (200, 201, 403, 409):
            response.raise_for_status()
        self._dataset_ensured = True

    async def _gsp_put(self, graph: Graph, named_graph: str) -> None:
        """Replace a named graph via GSP PUT (idempotent)."""
        client = await self._http()
        response = await client.put(
            f"{self._base}/{self._dataset}/data",
            params={"graph": named_graph},
            content=graph.serialize(format="turtle").encode(),
            headers={"Content-Type": "text/turtle"},
        )
        response.raise_for_status()

    async def _gsp_post(self, graph: Graph, named_graph: str) -> None:
        """Append triples to a named graph via GSP POST."""
        client = await self._http()
        response = await client.post(
            f"{self._base}/{self._dataset}/data",
            params={"graph": named_graph},
            content=graph.serialize(format="turtle").encode(),
            headers={"Content-Type": "text/turtle"},
        )
        response.raise_for_status()

    async def _gsp_delete(self, named_graph: str) -> None:
        """Drop a named graph via GSP DELETE (no-op if graph does not exist)."""
        client = await self._http()
        response = await client.delete(
            f"{self._base}/{self._dataset}/data",
            params={"graph": named_graph},
        )
        # 404 = graph didn't exist — treat as success
        if response.status_code != 404:
            response.raise_for_status()

    async def _gsp_get(self, named_graph: str) -> Graph:
        """Fetch a named graph via GSP GET (returns empty Graph if not found)."""
        client = await self._http()
        response = await client.get(
            f"{self._base}/{self._dataset}/data",
            params={"graph": named_graph},
            headers={"Accept": "text/turtle"},
        )
        if response.status_code == 404:
            return Graph()
        response.raise_for_status()
        g = Graph()
        if response.text.strip():
            g.parse(data=response.text, format="turtle")
        return g

    async def clear_graph(
        self, target: Literal["schema", "data", "all"]
    ) -> dict[str, int]:
        """Drop one or both named graphs and return how many triples were removed.

        Args:
            target: "schema" clears TBox, "data" clears ABox, "all" clears both.

        Returns:
            Dict mapping graph name → triples removed before deletion.
        """
        await self._ensure_dataset()
        removed: dict[str, int] = {}

        if target in ("schema", "all"):
            removed["schema"] = await self._count_graph(SCHEMA_GRAPH_URI)
            await self._gsp_delete(SCHEMA_GRAPH_URI)

        if target in ("data", "all"):
            removed["data"] = await self._count_graph(DATA_GRAPH_URI)
            await self._gsp_delete(DATA_GRAPH_URI)

        return removed

    async def dump_graph(
        self,
        target: Literal["schema", "data", "all"],
        fmt: Literal["ttl", "json", "jsonl", "xlsx"] = "ttl",
    ) -> bytes:
        """Export one or both named graphs as bytes in the requested format.

        Args:
            target: "schema" (TBox), "data" (ABox), or "all" (both).
            fmt: "ttl" | "json" (triple array) | "jsonl" | "xlsx".

        Note — all + xlsx vs. all + ttl/json/jsonl:
            XLSX exports TBox and ABox as **separate sheets** (more useful in a
            spreadsheet tool).  TTL/JSON/JSONL merge both graphs into a single
            stream so the output is a valid, self-contained RDF/triple document.

        Returns:
            Serialised bytes.
        """
        import json as _json

        await self._ensure_dataset()

        if target == "schema":
            graphs: dict[str, Graph] = {"TBox": await self._gsp_get(SCHEMA_GRAPH_URI)}
        elif target == "data":
            graphs = {"ABox": await self._gsp_get(DATA_GRAPH_URI)}
        else:
            schema_g, data_g = await asyncio.gather(
                self._gsp_get(SCHEMA_GRAPH_URI),
                self._gsp_get(DATA_GRAPH_URI),
            )
            graphs = {"TBox": schema_g, "ABox": data_g}

        # XLSX uses per-sheet split; other formats need a single merged graph.
        if fmt == "xlsx":
            return _graphs_to_xlsx(graphs)

        merged = Graph()
        for g in graphs.values():
            merged |= g  # union including namespace bindings (rdflib 6+)

        if fmt == "ttl":
            return merged.serialize(format="turtle").encode()

        if fmt == "json":
            rows = [{"s": str(s), "p": str(p), "o": str(o)} for s, p, o in merged]
            return _json.dumps(rows, ensure_ascii=False, indent=2).encode()

        # jsonl — trailing newline required by NDJSON spec
        lines = [
            _json.dumps({"s": str(s), "p": str(p), "o": str(o)}, ensure_ascii=False)
            for s, p, o in merged
        ]
        return ("\n".join(lines) + "\n").encode() if lines else b""

    async def _sparql_select(self, sparql: str) -> dict[str, Any]:
        """Execute a SPARQL SELECT query (internal use only — not MCP-exposed).

        Args:
            sparql: Validated SPARQL SELECT string.

        Returns:
            Raw SPARQL JSON results dict.
        """
        client = await self._http()
        response = await client.post(
            f"{self._base}/{self._dataset}/sparql",
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
        )
        response.raise_for_status()
        return response.json()

    async def _count_graph(self, named_graph: str) -> int:
        result = await self._sparql_select(
            f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{named_graph}> {{ ?s ?p ?o }} }}"
        )
        bindings = result.get("results", {}).get("bindings", [])
        return int(bindings[0]["n"]["value"]) if bindings else 0

    # ── Load ─────────────────────────────────────────────────────────────────

    async def load_rdf(
        self,
        path: str,
        mode: Literal["schema", "data", "auto"] = "auto",
        replace: bool = False,
    ) -> LoadResult:
        """Parse an RDF file and upload it to the appropriate named graph.

        Schema (TBox) → PUT urn:ontorag:schema (always replaces; one canonical schema).
        Data (ABox)   → POST urn:ontorag:data (appends by default).
                        Pass replace=True to DROP the existing data graph first.

        Args:
            path: Local file path (TTL, JSON-LD, RDF/XML, N3).
            mode: "schema", "data", or "auto" (auto-detects from content).
            replace: If True and mode is "data", replaces the entire data graph
                     instead of appending. Ignored for schema (always replaced).

        Returns:
            LoadResult with triple count and resolved mode.

        Raises:
            FileNotFoundError: If the file does not exist.
            httpx.HTTPStatusError: If Fuseki returns an error.
        """
        graph = parse_rdf(path)  # raises FileNotFoundError early if missing
        triple_count = len(graph)
        await self._ensure_dataset()

        # Capture domain-specific namespace prefixes for query_pattern translator
        for prefix, ns in graph.namespaces():
            prefix_str = str(prefix)
            if prefix_str and prefix_str not in STANDARD_PREFIXES:
                self._namespaces[prefix_str] = str(ns)

        resolved_mode: Literal["schema", "data"] = (
            detect_mode(graph) if mode == "auto" else mode  # type: ignore[assignment]
        )

        if resolved_mode == "schema" or replace:
            await self._gsp_put(
                graph, SCHEMA_GRAPH_URI if resolved_mode == "schema" else DATA_GRAPH_URI
            )
        else:
            await self._gsp_post(graph, DATA_GRAPH_URI)

        logger.info(
            "Loaded %d triples (%s) into %s",
            triple_count,
            resolved_mode,
            self._dataset,
        )
        return LoadResult(
            triples_loaded=triple_count,
            source=path,
            mode=resolved_mode,
        )

    # ── Status ────────────────────────────────────────────────────────────────

    async def status(self) -> StoreStatus:
        """Return connection state and triple counts per named graph."""
        try:
            client = await self._http()
            response = await client.get(f"{self._base}/$/ping")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Fuseki ping failed: %s", exc)
            return StoreStatus(
                connected=False,
                store_type="fuseki",
                triple_count=None,
                schema_loaded=False,
                data_loaded=False,
            )

        schema_count = await self._count_graph(SCHEMA_GRAPH_URI)
        data_count = await self._count_graph(DATA_GRAPH_URI)

        return StoreStatus(
            connected=True,
            store_type="fuseki",
            triple_count=schema_count + data_count,
            schema_loaded=schema_count > 0,
            data_loaded=data_count > 0,
        )

    # ── Layer 1 tools ────────────────────────────────────────────────────────

    async def get_schema(self) -> SchemaResult:
        """Return compact schema overview: class hierarchy + property counts only."""
        prefixes = (
            "PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX owl:  <http://www.w3.org/2002/07/owl#>\n"
        )

        # 1. All classes with optional label + parent (owl:Class + rdfs:Class)
        cls_query = (
            prefixes
            + f"""
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?class ?label ?parent ?comment
WHERE {{
  GRAPH <{SCHEMA_GRAPH_URI}> {{
    {{ ?class a owl:Class . }} UNION {{ ?class a rdfs:Class . }}
    OPTIONAL {{
      ?class rdfs:label ?label .
      FILTER(LANG(?label) = "" || LANGMATCHES(LANG(?label), "en"))
    }}
    OPTIONAL {{
      ?class rdfs:subClassOf ?parent .
      FILTER(!isBlank(?parent) && ?parent != owl:Thing)
    }}
    OPTIONAL {{
      {{ ?class rdfs:comment ?comment . }}
      UNION
      {{ ?class skos:definition ?comment . }}
      FILTER(LANG(?comment) = "" || LANGMATCHES(LANG(?comment), "en"))
    }}
  }}
}}
ORDER BY STR(?class)
"""
        )

        # 2. Property count per domain class + OWL metadata (transitive / inverse)
        prop_query = (
            prefixes
            + f"""
SELECT DISTINCT ?prop ?domain ?propType ?label ?range ?isTransitive ?inverse ?comment
WHERE {{
  GRAPH <{SCHEMA_GRAPH_URI}> {{
    VALUES ?propType {{ owl:ObjectProperty owl:DatatypeProperty owl:AnnotationProperty }}
    ?prop a ?propType .
    OPTIONAL {{ ?prop rdfs:domain ?domain . FILTER(!isBlank(?domain)) }}
    OPTIONAL {{ ?prop rdfs:label ?label .
               FILTER(LANG(?label) = "" || LANGMATCHES(LANG(?label), "en")) }}
    OPTIONAL {{ ?prop rdfs:range ?range . FILTER(!isBlank(?range)) }}
    OPTIONAL {{
      {{ ?prop rdfs:comment ?comment . }}
      UNION
      {{ ?prop <http://www.w3.org/2004/02/skos/core#definition> ?comment . }}
      FILTER(LANG(?comment) = "" || LANGMATCHES(LANG(?comment), "en"))
    }}
    OPTIONAL {{ ?prop a owl:TransitiveProperty . BIND(true AS ?isTransitive) }}
    OPTIONAL {{ ?prop owl:inverseOf ?inverse . FILTER(!isBlank(?inverse)) }}
  }}
}}
ORDER BY STR(?prop)
"""
        )

        # 3. Instance count per class
        inst_query = f"""
SELECT ?class (COUNT(DISTINCT ?inst) AS ?count)
WHERE {{
  GRAPH <{DATA_GRAPH_URI}> {{ ?inst a ?class . }}
}}
GROUP BY ?class
"""

        # Execute all three queries in parallel
        cls_result, prop_result, inst_result = await asyncio.gather(
            self._sparql_select(cls_query),
            self._sparql_select(prop_query),
            self._sparql_select(inst_query),
        )

        # Build lookup maps + collect all PropertySummary objects
        prop_count: dict[str, int] = {}
        all_properties: list[PropertySummary] = []
        seen_prop_uris: set[str] = set()
        _OWL_TYPE_MAP = {
            "http://www.w3.org/2002/07/owl#ObjectProperty": "object",
            "http://www.w3.org/2002/07/owl#DatatypeProperty": "datatype",
            "http://www.w3.org/2002/07/owl#AnnotationProperty": "annotation",
        }
        # The same prop URI may appear multiple times across (propType × domain ×
        # transitive × inverse) row combinations. We aggregate per-prop so
        # is_transitive / inverse_of_uri are sticky once observed.
        prop_meta: dict[str, dict] = {}
        for b in prop_result.get("results", {}).get("bindings", []):
            domain = b.get("domain", {}).get("value")
            if domain:
                prop_count[domain] = prop_count.get(domain, 0) + 1
            prop_uri = b.get("prop", {}).get("value")
            if not prop_uri:
                continue
            meta = prop_meta.setdefault(
                prop_uri,
                {
                    "label": None,
                    "prop_type": "annotation",
                    "domain": None,
                    "range": None,
                    "is_transitive": False,
                    "inverse": None,
                    "description": None,
                },
            )
            if b.get("label"):
                meta["label"] = b["label"].get("value") or meta["label"]
            raw_type = b.get("propType", {}).get("value", "")
            if raw_type in _OWL_TYPE_MAP:
                meta["prop_type"] = _OWL_TYPE_MAP[raw_type]
            if not meta["domain"]:
                meta["domain"] = domain
            if not meta["range"]:
                meta["range"] = b.get("range", {}).get("value")
            if b.get("isTransitive", {}).get("value") == "true":
                meta["is_transitive"] = True
            if not meta["inverse"]:
                meta["inverse"] = b.get("inverse", {}).get("value")
            if not meta["description"] and b.get("comment"):
                meta["description"] = b["comment"].get("value")

        for prop_uri, meta in prop_meta.items():
            if prop_uri in seen_prop_uris:
                continue
            seen_prop_uris.add(prop_uri)
            all_properties.append(
                PropertySummary(
                    uri=prop_uri,
                    label=meta["label"],
                    prop_type=meta["prop_type"],
                    domain_uri=meta["domain"],
                    range_uri=meta["range"],
                    is_transitive=meta["is_transitive"],
                    inverse_of_uri=meta["inverse"],
                    description=meta["description"],
                )
            )

        inst_count: dict[str, int] = {
            b["class"]["value"]: int(b["count"]["value"])
            for b in inst_result.get("results", {}).get("bindings", [])
            if "class" in b and "count" in b
        }

        # Build ClassSummary list — same URI may appear in multiple rows
        # (label × parent × comment combinations); merge first-non-null wins.
        class_meta: dict[str, dict] = {}
        for b in cls_result.get("results", {}).get("bindings", []):
            uri = b["class"]["value"]
            meta_c = class_meta.setdefault(
                uri, {"label": None, "parent": None, "description": None}
            )
            if not meta_c["label"]:
                meta_c["label"] = b.get("label", {}).get("value")
            if not meta_c["parent"]:
                meta_c["parent"] = b.get("parent", {}).get("value")
            if not meta_c["description"]:
                meta_c["description"] = b.get("comment", {}).get("value")

        classes: list[ClassSummary] = [
            ClassSummary(
                uri=uri,
                label=meta_c["label"],
                parent_uri=meta_c["parent"],
                property_count=prop_count.get(uri, 0),
                instance_count=inst_count.get(uri, 0),
                description=meta_c["description"],
            )
            for uri, meta_c in class_meta.items()
        ]

        # Deduplicate (a class may appear multiple times if it has multiple parents)
        seen: set[str] = set()
        unique_classes: list[ClassSummary] = []
        for c in classes:
            if c.uri not in seen:
                seen.add(c.uri)
                unique_classes.append(c)

        total_props = len(
            {
                b["prop"]["value"]
                for b in prop_result.get("results", {}).get("bindings", [])
            }
        )

        return SchemaResult(
            total_classes=len(unique_classes),
            total_properties=total_props,
            namespaces={**STANDARD_PREFIXES, **self._namespaces},
            classes=unique_classes,
            properties=all_properties,
        )

    async def get_class_detail(self, class_uri: str) -> ClassDetail:
        """Return full TBox detail for one class (properties, hierarchy, instances).

        Args:
            class_uri: Full URI of the class (e.g. http://xmlns.com/foaf/0.1/Person).

        Raises:
            ValueError: If class_uri contains characters that would enable SPARQL injection.
        """
        # Reject angle brackets before SPARQL interpolation — breaks out of <URI> quoting.
        if ">" in class_uri or "<" in class_uri:
            raise ValueError(
                f"class_uri contains illegal characters for SPARQL: {class_uri!r}"
            )
        safe_uri = uri_ref(class_uri)

        prefixes = (
            "PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX owl:  <http://www.w3.org/2002/07/owl#>\n"
        )

        # Class label and description
        meta_query = (
            prefixes
            + f"""
SELECT ?label ?description ?parent
WHERE {{
  GRAPH <{SCHEMA_GRAPH_URI}> {{
    OPTIONAL {{ {safe_uri} rdfs:label ?label .
               FILTER(LANG(?label) = "" || LANGMATCHES(LANG(?label), "en")) }}
    OPTIONAL {{ {safe_uri} rdfs:comment ?description .
               FILTER(LANG(?description) = "" || LANGMATCHES(LANG(?description), "en")) }}
    OPTIONAL {{ {safe_uri} rdfs:subClassOf ?parent .
               FILTER(!isBlank(?parent) && ?parent != owl:Thing) }}
  }}
}}
"""
        )

        # Properties with this class as domain
        prop_query = (
            prefixes
            + f"""
SELECT DISTINCT ?prop ?propType ?label ?range
WHERE {{
  GRAPH <{SCHEMA_GRAPH_URI}> {{
    VALUES ?propType {{ owl:ObjectProperty owl:DatatypeProperty owl:AnnotationProperty }}
    ?prop a ?propType ; rdfs:domain {safe_uri} .
    OPTIONAL {{ ?prop rdfs:label ?label .
               FILTER(LANG(?label) = "" || LANGMATCHES(LANG(?label), "en")) }}
    OPTIONAL {{ ?prop rdfs:range ?range . FILTER(!isBlank(?range)) }}
  }}
}}
ORDER BY STR(?prop)
"""
        )

        # Child classes
        children_query = (
            prefixes
            + f"""
SELECT DISTINCT ?child
WHERE {{
  GRAPH <{SCHEMA_GRAPH_URI}> {{
    ?child rdfs:subClassOf {safe_uri} .
    FILTER(?child != {safe_uri})
  }}
}}
"""
        )

        # Instance count + sample URIs
        inst_query = f"""
SELECT DISTINCT ?inst
WHERE {{
  GRAPH <{DATA_GRAPH_URI}> {{ ?inst a {safe_uri} . }}
}}
LIMIT 3
"""
        inst_count_query = f"""
SELECT (COUNT(DISTINCT ?inst) AS ?n)
WHERE {{
  GRAPH <{DATA_GRAPH_URI}> {{ ?inst a {safe_uri} . }}
}}
"""

        # Execute all five queries in parallel
        (
            meta_result,
            prop_result,
            children_result,
            inst_result,
            inst_count_result,
        ) = await asyncio.gather(
            self._sparql_select(meta_query),
            self._sparql_select(prop_query),
            self._sparql_select(children_query),
            self._sparql_select(inst_query),
            self._sparql_select(inst_count_query),
        )

        meta_bindings = meta_result.get("results", {}).get("bindings", [])
        label = next((b["label"]["value"] for b in meta_bindings if "label" in b), None)
        description = next(
            (b["description"]["value"] for b in meta_bindings if "description" in b),
            None,
        )
        parent_uris = list(
            {b["parent"]["value"] for b in meta_bindings if "parent" in b}
        )

        _prop_type_map = {
            "http://www.w3.org/2002/07/owl#ObjectProperty": "object",
            "http://www.w3.org/2002/07/owl#DatatypeProperty": "datatype",
            "http://www.w3.org/2002/07/owl#AnnotationProperty": "annotation",
        }
        properties = [
            PropertySummary(
                uri=b["prop"]["value"],
                label=b.get("label", {}).get("value"),
                prop_type=_prop_type_map.get(
                    b.get("propType", {}).get("value", ""), "annotation"
                ),  # type: ignore[arg-type]
                domain_uri=class_uri,
                range_uri=b.get("range", {}).get("value"),
            )
            for b in prop_result.get("results", {}).get("bindings", [])
        ]

        child_uris = [
            b["child"]["value"]
            for b in children_result.get("results", {}).get("bindings", [])
        ]
        sample_uris = [
            b["inst"]["value"]
            for b in inst_result.get("results", {}).get("bindings", [])
        ]
        inst_count_bindings = inst_count_result.get("results", {}).get("bindings", [])
        inst_count = (
            int(inst_count_bindings[0]["n"]["value"]) if inst_count_bindings else 0
        )

        return ClassDetail(
            uri=class_uri,
            label=label,
            description=description,
            parent_uris=parent_uris,
            child_uris=child_uris,
            properties=properties,
            instance_count=inst_count,
            sample_instance_uris=sample_uris,
        )

    # L1 entity/traversal tools are inherited from _EntityMixin and _TraversalMixin

    # ── Layer 2 tool ─────────────────────────────────────────────────────────

    async def query_pattern(self, query: PatternQuery) -> QueryResult:
        """Execute a structured JSON DSL query translated to SPARQL internally."""
        sparql = pattern_to_sparql(query, extra_prefixes=self._namespaces)
        raw = await self._sparql_select(sparql)

        vars_list: list[str] = raw.get("head", {}).get("vars", [])
        bindings = raw.get("results", {}).get("bindings", [])

        rows: list[dict[str, Any]] = [
            {var: b[var]["value"] for var in vars_list if var in b} for b in bindings
        ]

        return QueryResult(columns=vars_list, rows=rows, total=len(rows))


# ── Module-level helpers ──────────────────────────────────────────────────────


def _graphs_to_xlsx(graphs: dict[str, Graph]) -> bytes:
    """Serialise one or more rdflib Graphs into an XLSX workbook.

    Each graph becomes a sheet named after its key.
    Columns: Subject | Predicate | Object.
    """
    import io

    try:
        import openpyxl
    except ImportError as exc:
        raise ImportError(
            "openpyxl이 설치되어 있지 않습니다. 'uv add openpyxl' 후 재시도하세요."
        ) from exc

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove the default blank sheet
    for sheet_name, graph in graphs.items():
        ws = wb.create_sheet(title=sheet_name)
        ws.append(["Subject", "Predicate", "Object"])
        for s, p, o in graph:
            ws.append([str(s), str(p), str(o)])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
