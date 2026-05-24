from __future__ import annotations

"""Neo4j + neosemantics (n10s) graph store adapter.

Design decisions (from docs/design/neo4j-n10s.md):
- handleVocabUris = SHORTEN   → labels/rel-types/props use prefix__local
- handleRDFTypes  = LABELS_AND_NODES → classes are also nodes (subClassOf traversable)
- handleMultival  = ARRAY     → multi-valued props stored as Python lists
- keepLangTag     = true      → rdfs:label has lang tags

URI ↔ shortened mapping:
  shorten(full_uri)  → prefix__local  (for Cypher queries)
  expand(prefix__local) → full_uri    (for protocol output)

TBox vs ABox separation (n10s has no named graphs):
  TBox node = typed as owl:Class / owl:*Property / rdfs:Class / owl:Ontology
  ABox node = all other :Resource nodes

subClassOf inference — intentional divergence from Fuseki:
  find_entities / count_entities follow [:rdfs__subClassOf*0..] chains
  natively in Cypher, giving real OWL hierarchy inference without external
  reasoner configuration.

Multi-valued props (ARRAY config):
  n10s stores every property as a list.  Read path: if list length == 1,
  unwrap to scalar; otherwise keep list — preserving Fuseki parity where
  repeated triples produce a list only when there are multiple values.
"""

import asyncio
import logging
import os
from typing import Any, Literal

from rdflib import Graph

from ontorag.core.loader import detect_mode, parse_rdf
from ontorag.core.sparql import STANDARD_PREFIXES
from ontorag.stores._neo4j_entity_mixin import _Neo4jEntityMixin
from ontorag.stores._neo4j_export import triples_to_ttl, triples_to_xlsx
from ontorag.stores._neo4j_schema_mixin import _Neo4jSchemaMixin
from ontorag.stores._neo4j_search_mixin import _Neo4jSearchMixin
from ontorag.stores._neo4j_traversal_mixin import _Neo4jTraversalMixin
from ontorag.stores.base import (
    LoadResult,
    PatternQuery,
    QueryResult,
    StoreStatus,
)

logger = logging.getLogger(__name__)

# URI of TBox vocabulary types — nodes whose rdf:type matches any of these
# are classified as TBox.
_TBOX_TYPE_URIS: frozenset[str] = frozenset(
    {
        "http://www.w3.org/2002/07/owl#Class",
        "http://www.w3.org/2000/01/rdf-schema#Class",
        "http://www.w3.org/2002/07/owl#ObjectProperty",
        "http://www.w3.org/2002/07/owl#DatatypeProperty",
        "http://www.w3.org/2002/07/owl#AnnotationProperty",
        "http://www.w3.org/2002/07/owl#Ontology",
        "http://www.w3.org/2002/07/owl#TransitiveProperty",
    }
)

# OWL prop type URI → protocol literal
_OWL_TYPE_MAP: dict[str, str] = {
    "http://www.w3.org/2002/07/owl#ObjectProperty": "object",
    "http://www.w3.org/2002/07/owl#DatatypeProperty": "datatype",
    "http://www.w3.org/2002/07/owl#AnnotationProperty": "annotation",
}


class Neo4jStore(_Neo4jSchemaMixin, _Neo4jEntityMixin, _Neo4jTraversalMixin, _Neo4jSearchMixin):
    """Apache Neo4j + neosemantics (n10s) graph store adapter.

    All public methods implement the GraphStore protocol exactly.
    Internal helpers are prefixed with ``_``.

    Construction:
        ``Neo4jStore.from_env()`` reads NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD /
        NEO4J_DATABASE from the environment.  Direct construction is also possible
        for tests.

    Thread safety:
        The Neo4j async driver is safe to share across coroutines.  Each
        ``_run`` / ``_run_write`` call opens its own session.
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
    ) -> None:
        """Initialize the Neo4j store adapter.

        Args:
            uri: Bolt URI (e.g. bolt://localhost:7687).
            user: Authentication username.
            password: Authentication password.
            database: Target database name (default: "neo4j").
        """
        try:
            from neo4j import AsyncGraphDatabase  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'neo4j' Python driver is required for GRAPH_STORE=neo4j. "
                "Install it with: uv add 'ontorag[neo4j]' (or pip install neo4j)"
            ) from exc

        self._uri = uri
        self._database = database
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        # prefix → namespace mapping (populated from _NsPrefDef after each load)
        self._prefix_to_ns: dict[str, str] = {}
        self._ns_to_prefix: dict[str, str] = {}
        # Namespaces pre-sorted longest-first so _shorten() picks the most
        # specific match without re-sorting on every call.
        self._ns_sorted: list[tuple[str, str]] = []
        self._prefix_map_loaded: bool = False
        # Serialises concurrent prefix-map reloads (avoids duplicate DB hits
        # and torn reads when several tools call _ensure_prefix_map at once).
        self._prefix_lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> Neo4jStore:
        """Create a Neo4jStore from environment variables.

        Reads: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE.
        Defaults: bolt://localhost:7687, neo4j, neo4j, neo4j.

        Returns:
            Configured Neo4jStore instance.
        """
        return cls(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "neo4j"),
            database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        )

    # ── Driver helpers ────────────────────────────────────────────────────────

    async def _run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Execute a read Cypher query and return all records as dicts.

        Args:
            cypher: Cypher query string.
            **params: Named parameters bound to the query.

        Returns:
            List of record dicts (key = result column name).
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, **params)
            records = await result.data()
            return records

    async def _run_write(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Execute a write Cypher query and return all records as dicts.

        Args:
            cypher: Cypher write query.
            **params: Named parameters.

        Returns:
            List of record dicts.
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, **params)
            records = await result.data()
            return records

    async def _run_query(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a Cypher query with an explicit params dict.

        Args:
            cypher: Cypher query string.
            params: Parameter dict (for pattern_to_cypher output).

        Returns:
            List of record dicts.
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(cypher, **params)
            return await result.data()

    # ── Prefix map ────────────────────────────────────────────────────────────

    async def _reload_prefix_map(self) -> None:
        """Refresh the bidirectional prefix ↔ namespace map from n10s _NsPrefDef.

        Guarded by ``_prefix_lock`` so concurrent callers don't issue duplicate
        reload queries or observe a half-updated map.
        """
        async with self._prefix_lock:
            records = await self._run(
                "MATCH (p:_NsPrefDef) RETURN properties(p) AS props"
            )
            if not records:
                return
            # _NsPrefDef stores all prefixes as properties on a single node
            props = records[0].get("props", {})
            new_p2n: dict[str, str] = {}
            new_n2p: dict[str, str] = {}
            for prefix, ns in props.items():
                if isinstance(ns, str) and ns.startswith("http"):
                    new_p2n[prefix] = ns
                    new_n2p[ns] = prefix
            self._prefix_to_ns = new_p2n
            self._ns_to_prefix = new_n2p
            # Cache the longest-first sorted (ns, prefix) list once per reload.
            self._ns_sorted = sorted(
                new_n2p.items(), key=lambda kv: -len(kv[0])
            )
            self._prefix_map_loaded = True
            logger.debug("Loaded %d prefixes from n10s", len(new_p2n))

    async def _ensure_prefix_map(self) -> None:
        """Ensure the prefix map has been loaded at least once."""
        if not self._prefix_map_loaded:
            await self._reload_prefix_map()

    def _shorten(self, uri: str) -> str:
        """Convert a full URI to n10s-shortened form (prefix__local).

        Falls back to the URI itself if no matching prefix is registered.

        Args:
            uri: Full URI string.

        Returns:
            Shortened ``prefix__local`` form, or the original URI.
        """
        # Iterate the cached longest-first list (no per-call re-sort).
        for ns, prefix in self._ns_sorted:
            if uri.startswith(ns):
                local = uri[len(ns):]
                return f"{prefix}__{local}"
        return uri

    def _expand(self, short: str) -> str:
        """Convert a shortened n10s form (prefix__local) back to full URI.

        Args:
            short: Shortened string, e.g. ``pk__Pokemon``.

        Returns:
            Full URI, or the original string if not resolvable.
        """
        if "__" in short:
            prefix, local = short.split("__", 1)
            ns = self._prefix_to_ns.get(prefix)
            if ns:
                return ns + local
        return short

    def _shorten_prefixed(self, prefixed_or_uri: str) -> str:
        """Shorten a SPARQL prefixed name (e.g. pk:Pokemon) or full URI.

        Args:
            prefixed_or_uri: Full URI, prefixed name (prefix:local), or
                angle-bracketed URI (<http://...>).

        Returns:
            Shortened ``prefix__local`` form.
        """
        # Strip angle brackets
        if prefixed_or_uri.startswith("<") and prefixed_or_uri.endswith(">"):
            uri = prefixed_or_uri[1:-1]
            return self._shorten(uri)
        # Prefixed name prefix:local
        if ":" in prefixed_or_uri and "://" not in prefixed_or_uri:
            pref, local = prefixed_or_uri.split(":", 1)
            ns = self._prefix_to_ns.get(pref) or (
                STANDARD_PREFIXES.get(pref)
            )
            if ns:
                return self._shorten(ns + local)
        if "://" in prefixed_or_uri:
            return self._shorten(prefixed_or_uri)
        return prefixed_or_uri

    # ── Bootstrapping ─────────────────────────────────────────────────────────

    async def _ensure_graphconfig(self) -> None:
        """Ensure n10s graphconfig and unique constraint exist (idempotent).

        n10s only allows one graphconfig per database — re-init raises an
        error. We check first and silently skip if already configured.

        Raises:
            Exception: Propagates auth/permission/connection errors from Neo4j.
                ``CREATE CONSTRAINT ... IF NOT EXISTS`` is itself idempotent,
                so we do NOT swallow its errors (which would hide auth issues).
        """
        # Idempotent — no try/except: a failure here is a real error (auth,
        # permissions, connectivity) that must surface, not be masked.
        await self._run_write(
            "CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS "
            "FOR (r:Resource) REQUIRE r.uri IS UNIQUE"
        )

        # Check whether graphconfig exists
        rows = await self._run("MATCH (n:_GraphConfig) RETURN count(n) AS cnt")
        if rows and rows[0].get("cnt", 0) > 0:
            return  # already initialized

        await self._run_write(
            "CALL n10s.graphconfig.init({"
            "handleVocabUris: 'SHORTEN', "
            "handleRDFTypes: 'LABELS_AND_NODES', "
            "handleMultival: 'ARRAY', "
            "keepLangTag: true"
            "})"
        )
        logger.info("n10s graphconfig initialized")

    async def _register_prefixes(self, graph: Graph) -> None:
        """Register all prefixes from an rdflib graph into n10s (idempotent).

        Args:
            graph: Parsed rdflib Graph whose ``graph.namespaces()`` will be
                registered in Neo4j via ``n10s.nsprefixes.add``.
        """
        for prefix, ns_ref in graph.namespaces():
            prefix_str = str(prefix)
            ns_str = str(ns_ref)
            if not prefix_str or not ns_str.startswith("http"):
                continue
            try:
                await self._run_write(
                    "CALL n10s.nsprefixes.add($prefix, $ns)",
                    prefix=prefix_str,
                    ns=ns_str,
                )
            except Exception as exc:
                # "Prefix already exists" is fine; log others
                msg = str(exc)
                if "already" not in msg.lower():
                    logger.debug("nsprefixes.add(%s): %s", prefix_str, exc)
        await self._reload_prefix_map()

    # ── TBox/ABox classification helpers ─────────────────────────────────────

    @property
    def _tbox_type_list(self) -> list[str]:
        """TBox type URIs as a list for Cypher parameter binding."""
        return list(_TBOX_TYPE_URIS)

    # ── Load ─────────────────────────────────────────────────────────────────

    async def load_rdf(
        self,
        path: str,
        mode: Literal["schema", "data", "auto"] = "auto",
        replace: bool = False,
    ) -> LoadResult:
        """Parse an RDF file and import it into Neo4j via n10s.

        TBox (schema) import always replaces existing TBox nodes.
        ABox (data) import appends unless replace=True.

        Args:
            path: Local file path (TTL, JSON-LD, RDF/XML, N3).
            mode: "schema", "data", or "auto" (auto-detects from content).
            replace: If True and mode resolves to "data", clears existing ABox
                     before importing.

        Returns:
            LoadResult with triple count and resolved mode.
        """
        graph = parse_rdf(path)
        triple_count = len(graph)

        await self._ensure_graphconfig()
        await self._register_prefixes(graph)

        resolved_mode: Literal["schema", "data"] = (
            detect_mode(graph) if mode == "auto" else mode  # type: ignore[assignment]
        )

        if resolved_mode == "schema":
            # Replace TBox: delete existing TBox nodes first
            await self._delete_tbox_nodes()
        elif replace:
            # Replace ABox
            await self._delete_abox_nodes()

        # Import via n10s inline (TTL serialization for universal support)
        ttl = graph.serialize(format="turtle")
        records = await self._run_write(
            "CALL n10s.rdf.import.inline($ttl, 'Turtle') "
            "YIELD triplesLoaded RETURN triplesLoaded",
            ttl=ttl,
        )
        loaded = records[0]["triplesLoaded"] if records else triple_count

        logger.info(
            "Loaded %d triples (%s) into Neo4j (source: %s)",
            loaded,
            resolved_mode,
            path,
        )

        # B2: keep the BM25 full-text index in sync after every load.
        await self._ensure_fulltext_index()

        return LoadResult(
            triples_loaded=loaded,
            source=path,
            mode=resolved_mode,
        )

    async def _delete_tbox_nodes(self) -> int:
        """Delete all TBox nodes (and their relationships) from the graph.

        Returns:
            Number of nodes deleted.
        """
        records = await self._run_write(
            """
            MATCH (n:Resource)-[:rdf__type]->(t:Resource)
            WHERE t.uri IN $tbox_uris
            WITH COLLECT(DISTINCT n) AS tbox_nodes
            UNWIND tbox_nodes AS n
            DETACH DELETE n
            RETURN count(n) AS deleted
            """,
            tbox_uris=self._tbox_type_list,
        )
        return records[0]["deleted"] if records else 0

    async def _delete_abox_nodes(self) -> int:
        """Delete all ABox nodes (not TBox) and their relationships.

        Returns:
            Number of nodes deleted.
        """
        # Precedence: the "is-not-TBox" OR predicate is parenthesized so the
        # _NsPrefDef / _GraphConfig guards apply to the WHOLE WHERE, not just
        # the right OR branch. Without the parens an internal n10s node with no
        # rdf__type edge could be deleted (review #8).
        records = await self._run_write(
            """
            MATCH (n:Resource)
            WHERE (
                   NOT (n)-[:rdf__type]->(:Resource)
                OR NOT EXISTS {
                       MATCH (n)-[:rdf__type]->(t:Resource)
                       WHERE t.uri IN $tbox_uris
                   }
            )
            AND NOT n:_NsPrefDef AND NOT n:_GraphConfig
            WITH collect(n) AS abox
            UNWIND abox AS n
            DETACH DELETE n
            RETURN count(n) AS deleted
            """,
            tbox_uris=self._tbox_type_list,
        )
        return records[0]["deleted"] if records else 0

    # ── Status ────────────────────────────────────────────────────────────────

    async def status(self) -> StoreStatus:
        """Return connection state and approximate triple counts.

        triple_count is approximate: counts relationships + non-uri/label
        literal properties on Resource nodes. Named graph separation is
        not available in Neo4j, so schema_loaded / data_loaded are derived
        from the presence of TBox / ABox nodes respectively.

        Returns:
            StoreStatus with connected flag and load state indicators.
        """
        try:
            await self._run("RETURN 1")
        except Exception as exc:
            logger.warning("Neo4j ping failed: %s", exc)
            return StoreStatus(
                connected=False,
                store_type="neo4j",
                triple_count=None,
                schema_loaded=False,
                data_loaded=False,
            )

        schema_rows, data_rows, triple_rows = await asyncio.gather(
            self._run(
                "MATCH (n:Resource)-[:rdf__type]->(t:Resource) "
                "WHERE t.uri IN $uris RETURN count(DISTINCT n) AS cnt",
                uris=self._tbox_type_list,
            ),
            self._run(
                """
                MATCH (n:Resource)
                WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig
                AND NOT EXISTS {
                    MATCH (n)-[:rdf__type]->(t:Resource)
                    WHERE t.uri IN $uris
                }
                RETURN count(n) AS cnt
                """,
                uris=self._tbox_type_list,
            ),
            self._run(
                "MATCH (:Resource)-[r]->(:Resource) RETURN count(r) AS cnt"
            ),
        )

        schema_cnt = schema_rows[0]["cnt"] if schema_rows else 0
        data_cnt = data_rows[0]["cnt"] if data_rows else 0
        rel_cnt = triple_rows[0]["cnt"] if triple_rows else 0
        # Approximate: each relationship ≈ 1 triple; each literal property ≈ 1 triple
        approx_triples = rel_cnt + max(schema_cnt + data_cnt, 0)

        return StoreStatus(
            connected=True,
            store_type="neo4j",
            triple_count=approx_triples,
            schema_loaded=schema_cnt > 0,
            data_loaded=data_cnt > 0,
        )

    # ── Layer 2 — query_pattern ───────────────────────────────────────────────

    async def query_pattern(self, query: PatternQuery) -> QueryResult:
        """Execute a JSON DSL query translated to Cypher internally.

        Args:
            query: Validated PatternQuery object.

        Returns:
            QueryResult with columns and rows.
        """
        from ontorag.core.cypher import pattern_to_cypher  # noqa: PLC0415

        await self._ensure_prefix_map()

        cypher, params = pattern_to_cypher(
            query,
            shorten_fn=self._shorten_prefixed,
            expand_fn=self._expand,
        )
        records = await self._run_query(cypher, params)

        # Columns = variables from SELECT (strip leading ?)
        columns = [v.lstrip("?") for v in query.select]

        rows: list[dict[str, Any]] = []
        for rec in records:
            row: dict[str, Any] = {}
            for col in columns:
                val = rec.get(col)
                if val is None:
                    continue
                # Node objects (dict with 'uri' key) → extract URI for protocol output
                if isinstance(val, dict) and "uri" in val:
                    row[col] = val["uri"]
                else:
                    row[col] = val
            rows.append(row)

        return QueryResult(columns=columns, rows=rows, total=len(rows))

    # ── Dump ─────────────────────────────────────────────────────────────────

    async def dump_graph(
        self,
        target: Literal["schema", "data", "all"],
        fmt: Literal["ttl", "json", "jsonl", "xlsx"] = "ttl",
    ) -> bytes:
        """Export TBox, ABox, or both as bytes in the requested format.

        Uses n10s.rdf.export.cypher to serialise nodes as RDF triples.
        TBox and ABox are distinguished by vocabulary-type membership.

        Args:
            target: "schema" (TBox), "data" (ABox), or "all".
            fmt: Serialisation format (ttl, json, jsonl, xlsx).

        Returns:
            Serialised bytes.
        """
        import json as _json  # noqa: PLC0415

        await self._ensure_prefix_map()

        # Use "export_cypher" as the Cypher parameter name to avoid shadowing
        # the first positional arg "cypher" of self._run().
        if target == "schema":
            export_cypher = (
                "MATCH (n:Resource)-[:rdf__type]->(t:Resource) "
                "WHERE t.uri IN $tbox_uris RETURN DISTINCT n"
            )
            export_q = (
                "CALL n10s.rdf.export.cypher($export_cypher, {tbox_uris: $tbox_uris}) "
                "YIELD subject, predicate, object, isLiteral, literalType, literalLang "
                "RETURN subject, predicate, object, isLiteral, literalType, literalLang"
            )
            rows = await self._run(
                export_q,
                export_cypher=export_cypher,
                tbox_uris=self._tbox_type_list,
            )
        elif target == "data":
            export_cypher = (
                "MATCH (n:Resource) "
                "WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
                "AND NOT EXISTS { "
                "  MATCH (n)-[:rdf__type]->(t:Resource) WHERE t.uri IN $tbox_uris "
                "} RETURN n"
            )
            export_q = (
                "CALL n10s.rdf.export.cypher($export_cypher, {tbox_uris: $tbox_uris}) "
                "YIELD subject, predicate, object, isLiteral, literalType, literalLang "
                "RETURN subject, predicate, object, isLiteral, literalType, literalLang"
            )
            rows = await self._run(
                export_q,
                export_cypher=export_cypher,
                tbox_uris=self._tbox_type_list,
            )
        else:
            # All nodes
            export_cypher = (
                "MATCH (n:Resource) "
                "WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
                "RETURN n"
            )
            export_q = (
                "CALL n10s.rdf.export.cypher($export_cypher, {}) "
                "YIELD subject, predicate, object, isLiteral, literalType, literalLang "
                "RETURN subject, predicate, object, isLiteral, literalType, literalLang"
            )
            rows = await self._run(export_q, export_cypher=export_cypher)

        # Build RDF triples from rows
        triples = [
            {
                "s": r["subject"],
                "p": r["predicate"],
                "o": r["object"],
                "isLiteral": r["isLiteral"],
                "literalType": r.get("literalType") or "",
                "literalLang": r.get("literalLang") or "",
            }
            for r in rows
        ]

        if fmt == "json":
            return _json.dumps(
                [{"s": t["s"], "p": t["p"], "o": t["o"]} for t in triples],
                ensure_ascii=False,
                indent=2,
            ).encode()

        if fmt == "jsonl":
            lines = [
                _json.dumps({"s": t["s"], "p": t["p"], "o": t["o"]}, ensure_ascii=False)
                for t in triples
            ]
            return ("\n".join(lines) + "\n").encode() if lines else b""

        if fmt == "xlsx":
            return triples_to_xlsx(triples, label=target)

        # Default: TTL — build using rdflib
        return triples_to_ttl(triples)

    # ── Clear ─────────────────────────────────────────────────────────────────

    async def clear_graph(
        self,
        target: Literal["schema", "data", "all"],
    ) -> dict[str, int]:
        """Delete TBox, ABox, or all nodes and report how many were removed.

        The n10s graphconfig, constraint, and _NsPrefDef nodes are preserved.

        Args:
            target: "schema" clears TBox, "data" clears ABox, "all" clears both.

        Returns:
            Mapping of graph name → nodes deleted.
        """
        removed: dict[str, int] = {}

        if target in ("schema", "all"):
            removed["schema"] = await self._delete_tbox_nodes()

        if target in ("data", "all"):
            removed["data"] = await self._delete_abox_nodes()

        return removed

    # ── Close ─────────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Close the Neo4j driver and release resources (idempotent)."""
        try:
            await self._driver.close()
        except Exception:
            pass
