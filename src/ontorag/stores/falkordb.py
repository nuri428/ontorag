"""FalkorDB graph store adapter (v0.9).

FalkorDB is a Cypher-compatible, GraphBLAS-accelerated graph database (a Redis
module). It speaks OpenCypher, so this adapter reuses the Neo4j L1 mixins
(schema / entity / traversal) and the reasoning-layer mixins (bayes / causal)
unchanged — they target the same property-graph convention:

    (:Resource {uri})  +  [:rdf__type] / [:rdfs__subClassOf] / [:prefix__local] edges
    +  prefix__local property keys, every literal stored as a LIST (n10s ARRAY parity)

Three things differ from Neo4j and are implemented here:

1. **No n10s.** FalkorDB has no neosemantics, so RDF is loaded with a custom
   rdflib → Cypher converter (:func:`_rdf_to_graph` + :meth:`_import`) that
   reproduces the n10s SHORTEN + LABELS_AND_NODES + ARRAY conventions the shared
   mixins depend on. Prefixes are persisted in a single ``:_OntoragMeta`` node
   (the n10s ``_NsPrefDef`` analogue) so a fresh process can shorten/expand.
2. **No GDS.** Structural embeddings use the pure-Python ``core/fastrp.py`` (the
   Fuseki path); kNN uses FalkorDB's *native* vector index. See the embedding mixin.
3. **Cypher dialect.** Full-text / vector use ``db.idx.fulltext`` / ``db.idx.vector``
   (Neo4j uses ``db.index.*``); ``EXISTS {}`` subqueries are unavailable so the
   TBox/ABox classification and status queries are rewritten with OPTIONAL MATCH.

Construction: ``FalkorDBStore.from_env()`` reads FALKORDB_HOST / FALKORDB_PORT /
FALKORDB_PASSWORD / FALKORDB_GRAPH. The async client lives in the optional
``[falkordb]`` extra; a missing install surfaces as ImportError at construction.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Literal

from rdflib import Graph, Literal as RdfLiteral, URIRef

from ontorag.core.cypher import _safe_rel
from ontorag.core.loader import detect_mode, parse_rdf
from ontorag.core.ontology import validate_ontology_id
from ontorag.core.sparql import STANDARD_PREFIXES
from ontorag.stores._falkordb_embedding_mixin import _FalkorDBEmbeddingMixin
from ontorag.stores._falkordb_search_mixin import _FalkorDBSearchMixin
from ontorag.stores._neo4j_bayes_mixin import _Neo4jBayesMixin
from ontorag.stores._neo4j_causal_mixin import _Neo4jCausalMixin
from ontorag.stores._neo4j_entity_mixin import _Neo4jEntityMixin
from ontorag.stores._neo4j_schema_mixin import _Neo4jSchemaMixin
from ontorag.stores._neo4j_traversal_mixin import _Neo4jTraversalMixin
from ontorag.stores.neo4j import _TBOX_TYPE_URIS
from ontorag.stores.base import LoadResult, PatternQuery, QueryResult, StoreStatus

logger = logging.getLogger(__name__)

# Label for the single metadata node that persists the prefix map (n10s
# _NsPrefDef analogue). Deliberately NOT :Resource so it never appears in
# :Resource queries.
_META_LABEL = "_OntoragMeta"


class FalkorDBStore(
    _Neo4jSchemaMixin,
    _Neo4jEntityMixin,
    _Neo4jTraversalMixin,
    _FalkorDBSearchMixin,
    _FalkorDBEmbeddingMixin,
    _Neo4jBayesMixin,
    _Neo4jCausalMixin,
):
    """FalkorDB (Cypher/OpenCypher) graph store adapter.

    Reuses the Neo4j L1 + reasoning mixins; overrides connection, RDF loading,
    status, dump, and the full-text / vector capabilities for FalkorDB's dialect.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        password: str | None = None,
        graph_name: str = "ontorag",
    ) -> None:
        """Initialize the FalkorDB adapter.

        Args:
            host: FalkorDB (Redis) host.
            port: FalkorDB (Redis) port.
            password: Optional Redis AUTH password.
            graph_name: Logical graph key within FalkorDB.
        """
        try:
            from falkordb.asyncio import FalkorDB  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'falkordb' client is required for GRAPH_STORE=falkordb. "
                "Install it with: uv add 'ontorag[falkordb]' (or pip install falkordb)"
            ) from exc

        self._host = host
        self._port = port
        self._graph_name = graph_name
        self._db = FalkorDB(host=host, port=port, password=password)
        self._graph = self._db.select_graph(graph_name)
        # prefix ↔ namespace maps (persisted in :_OntoragMeta, rebuilt per process)
        self._prefix_to_ns: dict[str, str] = {}
        self._ns_to_prefix: dict[str, str] = {}
        self._ns_sorted: list[tuple[str, str]] = []
        self._prefix_map_loaded: bool = False
        self._prefix_lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> FalkorDBStore:
        """Create a FalkorDBStore from environment variables.

        Reads: FALKORDB_HOST, FALKORDB_PORT, FALKORDB_PASSWORD, FALKORDB_GRAPH.
        Defaults: localhost, 6379, (none), ontorag.
        """
        port_raw = os.environ.get("FALKORDB_PORT", "6379")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ValueError(f"FALKORDB_PORT must be an integer, got {port_raw!r}.") from exc
        return cls(
            host=os.environ.get("FALKORDB_HOST", "localhost"),
            port=port,
            password=os.environ.get("FALKORDB_PASSWORD") or None,
            graph_name=os.environ.get("FALKORDB_GRAPH", "ontorag"),
        )

    # ── Driver helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(result: Any) -> list[dict[str, Any]]:
        """Convert a FalkorDB QueryResult to Neo4j-style ``list[dict]``.

        Column names come from ``header[i][1]``. Node cells are flattened to
        their property dict (incl. ``uri``) so the inherited Neo4j mixins — which
        expect ``result.data()`` semantics where a returned node is a plain
        property dict — work unchanged.
        """
        header = getattr(result, "header", None) or []
        cols = [h[1] if isinstance(h, (list, tuple)) and len(h) > 1 else str(h) for h in header]
        rows: list[dict[str, Any]] = []
        for raw in (getattr(result, "result_set", None) or []):
            row: dict[str, Any] = {}
            for i, col in enumerate(cols):
                val = raw[i] if i < len(raw) else None
                props = getattr(val, "properties", None)
                row[col] = dict(props) if props is not None else val
            rows.append(row)
        return rows

    async def _run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Execute a read query, returning Neo4j-shaped record dicts."""
        result = await self._graph.query(cypher, params or None)
        return self._normalize(result)

    async def _run_write(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Execute a write query, returning Neo4j-shaped record dicts."""
        result = await self._graph.query(cypher, params or None)
        return self._normalize(result)

    async def _run_query(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a query with an explicit params dict (pattern_to_cypher path)."""
        result = await self._graph.query(cypher, params or None)
        return self._normalize(result)

    # ── Prefix map (n10s _NsPrefDef analogue, persisted in :_OntoragMeta) ──────

    @staticmethod
    def _namespace_of(uri: str) -> str:
        """Namespace of a URI: up to and including the last ``#`` or ``/``."""
        for sep in ("#", "/"):
            idx = uri.rfind(sep)
            if idx != -1:
                return uri[: idx + 1]
        return uri

    async def _reload_prefix_map(self) -> None:
        """Refresh the prefix ↔ namespace maps from the :_OntoragMeta node."""
        async with self._prefix_lock:
            rows = await self._run(
                f"MATCH (m:`{_META_LABEL}` {{kind: 'prefixes'}}) RETURN m AS m"
            )
            props = (rows[0].get("m") or {}) if rows else {}
            new_p2n: dict[str, str] = {}
            new_n2p: dict[str, str] = {}
            for prefix, ns in props.items():
                if prefix in ("kind",):
                    continue
                if isinstance(ns, str) and ns.startswith("http"):
                    new_p2n[prefix] = ns
                    new_n2p[ns] = prefix
            self._prefix_to_ns = new_p2n
            self._ns_to_prefix = new_n2p
            self._ns_sorted = sorted(new_n2p.items(), key=lambda kv: -len(kv[0]))
            self._prefix_map_loaded = True

    async def _ensure_prefix_map(self) -> None:
        if not self._prefix_map_loaded:
            await self._reload_prefix_map()

    async def _register_prefixes(self, graph: Graph) -> None:
        """Build a complete prefix map for *graph* and persist it.

        Starts from rdflib's bound prefixes, then assigns a generated ``nsN``
        prefix to every namespace that appears in a URI but is unbound — so
        ``_shorten`` always yields a valid ``prefix__local`` for ``_safe_rel``.
        """
        p2n = dict(self._prefix_to_ns)
        n2p = dict(self._ns_to_prefix)

        def _add(prefix: str, ns: str) -> None:
            if not prefix or not ns.startswith("http") or ns in n2p:
                return
            # Avoid clobbering an existing prefix that maps to a different ns.
            base, i = prefix, 0
            while prefix in p2n and p2n[prefix] != ns:
                i += 1
                prefix = f"{base}{i}"
            p2n[prefix] = ns
            n2p[ns] = prefix

        for prefix, ns_ref in graph.namespaces():
            _add(str(prefix), str(ns_ref))

        gen = 0
        for triple in graph:
            for term in triple:
                if isinstance(term, URIRef):
                    ns = self._namespace_of(str(term))
                    if ns and ns.startswith("http") and ns not in n2p:
                        while f"ns{gen}" in p2n:
                            gen += 1
                        _add(f"ns{gen}", ns)

        # Persist as flat properties on the single meta node.
        await self._run_write(
            f"MERGE (m:`{_META_LABEL}` {{kind: 'prefixes'}}) SET m += $props",
            props=p2n,
        )
        self._prefix_to_ns = p2n
        self._ns_to_prefix = n2p
        self._ns_sorted = sorted(n2p.items(), key=lambda kv: -len(kv[0]))
        self._prefix_map_loaded = True

    def _shorten(self, uri: str) -> str:
        """Full URI → ``prefix__local`` (or the URI unchanged if no prefix)."""
        for ns, prefix in self._ns_sorted:
            if uri.startswith(ns):
                return f"{prefix}__{uri[len(ns):]}"
        return uri

    def _expand(self, short: str) -> str:
        """``prefix__local`` → full URI (or unchanged)."""
        if "__" in short:
            prefix, local = short.split("__", 1)
            ns = self._prefix_to_ns.get(prefix)
            if ns:
                return ns + local
        return short

    def _shorten_prefixed(self, prefixed_or_uri: str) -> str:
        """Shorten a full URI, ``<uri>``, or SPARQL ``prefix:local`` name."""
        if prefixed_or_uri.startswith("<") and prefixed_or_uri.endswith(">"):
            return self._shorten(prefixed_or_uri[1:-1])
        if ":" in prefixed_or_uri and "://" not in prefixed_or_uri:
            pref, local = prefixed_or_uri.split(":", 1)
            ns = self._prefix_to_ns.get(pref) or STANDARD_PREFIXES.get(pref)
            if ns:
                return self._shorten(ns + local)
        if "://" in prefixed_or_uri:
            return self._shorten(prefixed_or_uri)
        return prefixed_or_uri

    @property
    def _tbox_type_list(self) -> list[str]:
        return list(_TBOX_TYPE_URIS)

    # ── RDF → property-graph conversion (the n10s replacement) ─────────────────

    @staticmethod
    def _literal_to_value(lit: RdfLiteral, pred_uri: str) -> Any:
        """Convert an rdflib Literal to a stored value (keepLangTag on labels)."""
        if (
            lit.language
            and pred_uri == "http://www.w3.org/2000/01/rdf-schema#label"
        ):
            return f"{lit}@{lit.language}"
        py = lit.toPython()
        # FalkorDB stores scalars; keep numbers/bools native, everything else str.
        if isinstance(py, (str, int, float, bool)):
            return py
        return str(lit)

    def _rdf_to_graph(
        self, graph: Graph
    ) -> tuple[
        dict[str, dict[str, list[Any]]],
        list[tuple[str, str, str]],
        dict[str, set[str]],
    ]:
        """Convert an rdflib Graph to (nodes, edges, labels) mirroring n10s.

        nodes: ``{uri: {prefix__local_key: [values, ...]}}`` — every literal
            property is a LIST (handleMultival=ARRAY parity).
        edges: ``[(subject_uri, prefix__local_reltype, object_uri), ...]`` — covers
            rdf:type and rdfs:subClassOf naturally (they are URI-valued).
        labels: ``{uri: {prefix__local_class, ...}}`` — for every ``s rdf:type o``,
            the shortened ``o`` becomes an extra node label on ``s`` (n10s
            LABELS_AND_NODES parity), so label-based schema queries
            (``MATCH (c:owl__Class)``) work unchanged.

        Blank-node subjects/objects are skipped (rare in the target ontologies;
        keeps v0.9.0 lean — the over-claim is documented).
        """
        rdf_type = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
        nodes: dict[str, dict[str, list[Any]]] = {}
        edges: list[tuple[str, str, str]] = []
        labels: dict[str, set[str]] = {}
        for s, p, o in graph:
            if not isinstance(s, URIRef):
                continue
            s_uri = str(s)
            nodes.setdefault(s_uri, {})
            p_uri = str(p)
            rel = _safe_rel(self._shorten(p_uri))
            if isinstance(o, URIRef):
                o_uri = str(o)
                edges.append((s_uri, rel, o_uri))
                nodes.setdefault(o_uri, {})
                if p_uri == rdf_type:
                    try:
                        labels.setdefault(s_uri, set()).add(
                            _safe_rel(self._shorten(o_uri))
                        )
                    except ValueError:
                        pass  # unshortenable type URI → no label (edge still set)
            elif isinstance(o, RdfLiteral):
                nodes[s_uri].setdefault(rel, []).append(
                    self._literal_to_value(o, p_uri)
                )
            # BNode object → skipped
        return nodes, edges, labels

    async def _import(
        self,
        nodes: dict[str, dict[str, list[Any]]],
        edges: list[tuple[str, str, str]],
        labels: dict[str, set[str]],
    ) -> int:
        """MERGE nodes (array props) + type-labels + edges. Returns triples-ish count."""
        node_rows = [{"uri": uri, "props": props} for uri, props in nodes.items()]
        if node_rows:
            await self._run_write(
                "UNWIND $rows AS r MERGE (n:Resource {uri: r.uri}) SET n += r.props",
                rows=node_rows,
            )
        # Type-as-label: group URIs by label (labels can't be parameterized;
        # each is _safe_rel-validated). One batch per distinct label.
        by_label: dict[str, list[str]] = {}
        for uri, lset in labels.items():
            for lab in lset:
                by_label.setdefault(lab, []).append(uri)
        for lab, uris in by_label.items():
            await self._run_write(
                f"UNWIND $uris AS u MATCH (n:Resource {{uri: u}}) SET n:`{lab}`",
                uris=uris,
            )
        # Edges: group by rel-type (validated by _safe_rel). One batch per type.
        by_rel: dict[str, list[dict[str, str]]] = {}
        for s_uri, rel, o_uri in edges:
            by_rel.setdefault(rel, []).append({"s": s_uri, "o": o_uri})
        for rel, pairs in by_rel.items():
            await self._run_write(
                "UNWIND $pairs AS pr "
                "MATCH (s:Resource {uri: pr.s}) MATCH (o:Resource {uri: pr.o}) "
                f"MERGE (s)-[:`{rel}`]->(o)",
                pairs=pairs,
            )
        prop_count = sum(len(v) for props in nodes.values() for v in props.values())
        return len(edges) + prop_count

    # ── Load ───────────────────────────────────────────────────────────────────

    async def load_rdf(
        self,
        path: str,
        mode: Literal["schema", "data", "auto"] = "auto",
        replace: bool = False,
        ontology: str | None = None,
        graph: Graph | None = None,
    ) -> LoadResult:
        """Parse an RDF file and import it into FalkorDB (custom rdflib loader).

        Mirrors the Neo4j semantics: schema import replaces existing TBox; data
        import appends unless ``replace``; ``ontology`` tags imported nodes.
        """
        ontology = validate_ontology_id(ontology)
        if graph is None:
            graph = parse_rdf(path)
        triple_count = len(graph)

        await self._register_prefixes(graph)
        resolved_mode: Literal["schema", "data"] = (
            detect_mode(graph) if mode == "auto" else mode  # type: ignore[assignment]
        )

        if resolved_mode == "schema":
            if ontology is not None:
                await self._clear_ontology_nodes(ontology, "schema")
            else:
                await self._delete_tbox_nodes()
        elif replace:
            if ontology is not None:
                await self._clear_ontology_nodes(ontology, "data")
            else:
                await self._delete_abox_nodes()

        nodes, edges, labels = self._rdf_to_graph(graph)
        await self._import(nodes, edges, labels)

        if ontology is not None:
            await self._tag_ontology_nodes(graph, ontology)
        await self._ensure_fulltext_index()

        logger.info("Loaded %d triples (%s) into FalkorDB (source: %s)", triple_count, resolved_mode, path)
        return LoadResult(
            triples_loaded=triple_count,
            source=path,
            mode=resolved_mode,
            ontology=ontology,
        )

    # ── TBox / ABox classification (no EXISTS{} — uses OPTIONAL MATCH) ──────────

    async def _delete_tbox_nodes(self) -> int:
        rows = await self._run_write(
            "MATCH (n:Resource)-[:rdf__type]->(t:Resource) "
            "WHERE t.uri IN $tbox "
            "WITH collect(DISTINCT n) AS ns "
            "UNWIND ns AS n DETACH DELETE n "
            "RETURN count(n) AS deleted",
            tbox=self._tbox_type_list,
        )
        return rows[0]["deleted"] if rows else 0

    async def _delete_abox_nodes(self) -> int:
        rows = await self._run_write(
            "MATCH (n:Resource) "
            "OPTIONAL MATCH (n)-[:rdf__type]->(t:Resource) WHERE t.uri IN $tbox "
            "WITH n, count(t) AS tc "
            "WHERE tc = 0 "
            "WITH collect(n) AS ns "
            "UNWIND ns AS n DETACH DELETE n "
            "RETURN count(n) AS deleted",
            tbox=self._tbox_type_list,
        )
        return rows[0]["deleted"] if rows else 0

    async def _clear_ontology_nodes(
        self, ontology: str, kind: Literal["schema", "data"]
    ) -> int:
        """Delete nodes tagged with *ontology* of the given TBox/ABox kind."""
        ontology = validate_ontology_id(ontology)
        if kind == "schema":
            rows = await self._run_write(
                "MATCH (n:Resource)-[:rdf__type]->(t:Resource) "
                "WHERE t.uri IN $tbox AND $oid IN n._ontology "
                "WITH collect(DISTINCT n) AS ns "
                "UNWIND ns AS n DETACH DELETE n RETURN count(n) AS deleted",
                tbox=self._tbox_type_list,
                oid=ontology,
            )
        else:
            rows = await self._run_write(
                "MATCH (n:Resource) WHERE $oid IN n._ontology "
                "OPTIONAL MATCH (n)-[:rdf__type]->(t:Resource) WHERE t.uri IN $tbox "
                "WITH n, count(t) AS tc WHERE tc = 0 "
                "WITH collect(n) AS ns "
                "UNWIND ns AS n DETACH DELETE n RETURN count(n) AS deleted",
                tbox=self._tbox_type_list,
                oid=ontology,
            )
        return rows[0]["deleted"] if rows else 0

    async def _tag_ontology_nodes(self, graph: Graph, ontology: str) -> int:
        """Tag :Resource nodes whose URI appears in *graph* with the ontology id."""
        uris = list(
            {str(t) for triple in graph for t in triple if isinstance(t, URIRef)}
        )
        if not uris:
            return 0
        rows = await self._run_write(
            "MATCH (n:Resource) WHERE n.uri IN $uris "
            "SET n._ontology = CASE "
            "  WHEN $oid IN coalesce(n._ontology, []) THEN n._ontology "
            "  ELSE coalesce(n._ontology, []) + $oid END "
            "RETURN count(n) AS tagged",
            uris=uris,
            oid=ontology,
        )
        return rows[0]["tagged"] if rows else 0

    # ── clear_graph (CLI `ontorag clear`) ──────────────────────────────────────

    async def clear_graph(
        self, target: Literal["schema", "data", "all"]
    ) -> dict[str, int]:
        """Drop TBox, ABox, or both. Returns removed-node counts per graph."""
        if target == "schema":
            return {"schema": await self._delete_tbox_nodes()}
        if target == "data":
            return {"data": await self._delete_abox_nodes()}
        schema = await self._delete_tbox_nodes()
        data = await self._delete_abox_nodes()
        return {"schema": schema, "data": data}

    # ── Status ───────────────────────────────────────────────────────────────

    async def status(self) -> StoreStatus:
        """Connection state + approximate counts (no EXISTS{} subqueries)."""
        try:
            await self._run("RETURN 1")
        except Exception as exc:
            logger.warning("FalkorDB ping failed: %s", exc)
            return StoreStatus(
                connected=False,
                store_type="falkordb",
                triple_count=None,
                schema_loaded=False,
                data_loaded=False,
            )

        schema_rows = await self._run(
            "MATCH (n:Resource)-[:rdf__type]->(t:Resource) "
            "WHERE t.uri IN $tbox RETURN count(DISTINCT n) AS cnt",
            tbox=self._tbox_type_list,
        )
        data_rows = await self._run(
            "MATCH (n:Resource) "
            "OPTIONAL MATCH (n)-[:rdf__type]->(t:Resource) WHERE t.uri IN $tbox "
            "WITH n, count(t) AS tc WHERE tc = 0 RETURN count(n) AS cnt",
            tbox=self._tbox_type_list,
        )
        rel_rows = await self._run(
            "MATCH (:Resource)-[r]->(:Resource) RETURN count(r) AS cnt"
        )
        schema_cnt = schema_rows[0]["cnt"] if schema_rows else 0
        data_cnt = data_rows[0]["cnt"] if data_rows else 0
        rel_cnt = rel_rows[0]["cnt"] if rel_rows else 0
        return StoreStatus(
            connected=True,
            store_type="falkordb",
            triple_count=rel_cnt + max(schema_cnt + data_cnt, 0),
            schema_loaded=schema_cnt > 0,
            data_loaded=data_cnt > 0,
        )

    # ── Layer 2 — query_pattern (shared translator) ────────────────────────────

    async def query_pattern(self, query: PatternQuery) -> QueryResult:
        """Execute a JSON DSL query translated to Cypher (shared with Neo4j)."""
        from ontorag.core.cypher import pattern_to_cypher  # noqa: PLC0415

        await self._ensure_prefix_map()
        cypher, params = pattern_to_cypher(
            query, shorten_fn=self._shorten_prefixed, expand_fn=self._expand
        )
        records = await self._run_query(cypher, params)
        columns = [v.lstrip("?") for v in query.select]
        rows: list[dict[str, Any]] = []
        for rec in records:
            row: dict[str, Any] = {}
            for col in columns:
                val = rec.get(col)
                if val is None:
                    continue
                row[col] = val["uri"] if isinstance(val, dict) and "uri" in val else val
            rows.append(row)
        return QueryResult(columns=columns, rows=rows, total=len(rows))

    # ── Dump (custom rdflib export — no n10s.rdf.export) ───────────────────────

    async def dump_graph(
        self,
        target: Literal["schema", "data", "all"],
        fmt: Literal["ttl", "json", "jsonl", "xlsx"] = "ttl",
        ontology: str | None = None,
    ) -> bytes:
        """Export TBox/ABox/all by reconstructing triples from the property graph."""
        import json as _json  # noqa: PLC0415

        from ontorag.stores._neo4j_export import triples_to_xlsx  # noqa: PLC0415

        ontology = validate_ontology_id(ontology)
        await self._ensure_prefix_map()

        # Select node URIs in scope.
        scope = " AND $oid IN n._ontology" if ontology is not None else ""
        params: dict[str, Any] = {"tbox": self._tbox_type_list}
        if ontology is not None:
            params["oid"] = ontology
        if target == "schema":
            node_q = (
                "MATCH (n:Resource)-[:rdf__type]->(t:Resource) "
                f"WHERE t.uri IN $tbox{scope} RETURN DISTINCT n AS n"
            )
        elif target == "data":
            node_q = (
                "MATCH (n:Resource) "
                "OPTIONAL MATCH (n)-[:rdf__type]->(t:Resource) WHERE t.uri IN $tbox "
                f"WITH n, count(t) AS tc WHERE tc = 0{scope.replace('AND', 'AND') if ontology else ''} "
                "RETURN n AS n"
            )
            if ontology is not None:
                node_q = (
                    "MATCH (n:Resource) WHERE $oid IN n._ontology "
                    "OPTIONAL MATCH (n)-[:rdf__type]->(t:Resource) WHERE t.uri IN $tbox "
                    "WITH n, count(t) AS tc WHERE tc = 0 RETURN n AS n"
                )
        else:
            node_q = (
                f"MATCH (n:Resource) WHERE 1=1{scope} RETURN n AS n"
                if ontology is not None
                else "MATCH (n:Resource) RETURN n AS n"
            )
        node_rows = await self._run(node_q, **params)
        in_scope = {
            r["n"]["uri"]
            for r in node_rows
            if isinstance(r.get("n"), dict) and r["n"].get("uri")
        }

        out_graph = Graph()
        for prefix, ns in self._prefix_to_ns.items():
            out_graph.bind(prefix, ns)

        # Literal properties.
        for r in node_rows:
            node = r.get("n")
            if not isinstance(node, dict):
                continue
            s_uri = node.get("uri")
            if not s_uri:
                continue
            for key, val in node.items():
                if key in ("uri", "_ontology"):
                    continue
                pred = self._expand(key)
                vals = val if isinstance(val, list) else [val]
                for v in vals:
                    text = str(v)
                    lang = None
                    if "@" in text and pred.endswith("#label"):
                        text, _, lang = text.rpartition("@")
                    out_graph.add(
                        (URIRef(s_uri), URIRef(pred), RdfLiteral(text, lang=lang))
                    )

        # Object-property edges between in-scope nodes.
        edge_rows = await self._run(
            "MATCH (s:Resource)-[r]->(o:Resource) "
            "RETURN s.uri AS s, type(r) AS rel, o.uri AS o"
        )
        for er in edge_rows:
            s_uri, rel, o_uri = er.get("s"), er.get("rel"), er.get("o")
            if not s_uri or not o_uri or not rel:
                continue
            if s_uri not in in_scope:
                continue
            out_graph.add(
                (URIRef(s_uri), URIRef(self._expand(rel)), URIRef(o_uri))
            )

        if fmt == "ttl":
            return out_graph.serialize(format="turtle").encode()

        triples = [
            {"s": str(s), "p": str(p), "o": str(o)} for s, p, o in out_graph
        ]
        if fmt == "json":
            return _json.dumps(triples, ensure_ascii=False, indent=2).encode()
        if fmt == "jsonl":
            return (
                "\n".join(_json.dumps(t, ensure_ascii=False) for t in triples) + "\n"
            ).encode() if triples else b""
        if fmt == "xlsx":
            xlsx_triples = [
                {**t, "isLiteral": False, "literalType": "", "literalLang": ""}
                for t in triples
            ]
            return triples_to_xlsx(xlsx_triples, label=target)
        raise ValueError(f"Unsupported dump format: {fmt!r}")

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Close the underlying Redis connection (idempotent, exception-safe)."""
        try:
            conn = getattr(self._db, "connection", None)
            if conn is not None:
                await conn.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("FalkorDB aclose: %s", exc)
