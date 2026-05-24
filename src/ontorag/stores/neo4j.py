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
from ontorag.stores._neo4j_traversal_mixin import _Neo4jTraversalMixin
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


class Neo4jStore(_Neo4jEntityMixin, _Neo4jTraversalMixin):
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
        self._prefix_map_loaded: bool = False

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
        """Refresh the bidirectional prefix ↔ namespace map from n10s _NsPrefDef."""
        records = await self._run("MATCH (p:_NsPrefDef) RETURN properties(p) AS props")
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
        for ns, prefix in sorted(
            self._ns_to_prefix.items(), key=lambda kv: -len(kv[0])
        ):
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

    # ── Schema ────────────────────────────────────────────────────────────────

    async def get_schema(self) -> SchemaResult:
        """Return compact schema overview: class hierarchy + property counts.

        Returns:
            SchemaResult with classes, properties, and namespace mapping.
        """
        await self._ensure_prefix_map()

        # 1. All owl:Class nodes + optional label, subClassOf parent, comment
        cls_rows, prop_rows, inst_rows = await asyncio.gather(
            self._run("""
                MATCH (c:owl__Class)
                OPTIONAL MATCH (c)-[:rdfs__subClassOf]->(parent:Resource)
                RETURN DISTINCT
                    c.uri AS uri,
                    c.rdfs__label AS label,
                    parent.uri AS parent_uri,
                    c.rdfs__comment AS comment
                ORDER BY c.uri
            """),
            self._run("""
                MATCH (p:Resource)-[:rdf__type]->(t:Resource)
                WHERE t.uri IN $prop_types
                OPTIONAL MATCH (p)-[:rdfs__domain]->(d:Resource)
                OPTIONAL MATCH (p)-[:rdfs__range]->(r:Resource)
                OPTIONAL MATCH (p)-[:owl__inverseOf]->(inv:Resource)
                OPTIONAL MATCH (p)-[:rdf__type]->(trans:Resource {uri: $transitive_uri})
                RETURN DISTINCT
                    p.uri AS uri,
                    p.rdfs__label AS label,
                    t.uri AS prop_type,
                    d.uri AS domain_uri,
                    r.uri AS range_uri,
                    inv.uri AS inverse_uri,
                    p.rdfs__comment AS comment,
                    CASE WHEN trans IS NOT NULL THEN true ELSE false END AS is_transitive
                ORDER BY p.uri
            """,
                prop_types=[
                    "http://www.w3.org/2002/07/owl#ObjectProperty",
                    "http://www.w3.org/2002/07/owl#DatatypeProperty",
                    "http://www.w3.org/2002/07/owl#AnnotationProperty",
                ],
                transitive_uri="http://www.w3.org/2002/07/owl#TransitiveProperty",
            ),
            # Instance count per class (subclass inference via *0.. hop)
            self._run("""
                MATCH (inst:Resource)-[:rdf__type]->(c:owl__Class)
                WHERE NOT (c:owl__ObjectProperty OR c:owl__DatatypeProperty
                           OR c:owl__AnnotationProperty OR c:owl__Ontology)
                RETURN c.uri AS class_uri, count(DISTINCT inst) AS cnt
            """),
        )

        # Build instance count map
        inst_count: dict[str, int] = {
            r["class_uri"]: r["cnt"] for r in inst_rows if r.get("class_uri")
        }

        # Build property count per domain class
        prop_count_map: dict[str, int] = {}
        prop_meta: dict[str, dict] = {}
        for row in prop_rows:
            uri = row.get("uri")
            if not uri:
                continue
            domain = row.get("domain_uri")
            if domain:
                prop_count_map[domain] = prop_count_map.get(domain, 0) + 1
            meta = prop_meta.setdefault(
                uri,
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
            lbl = _first_value(row.get("label"))
            if lbl and not meta["label"]:
                meta["label"] = lbl
            raw_type = row.get("prop_type") or ""
            if raw_type in _OWL_TYPE_MAP:
                meta["prop_type"] = _OWL_TYPE_MAP[raw_type]
            if not meta["domain"]:
                meta["domain"] = domain
            if not meta["range"]:
                meta["range"] = row.get("range_uri")
            if row.get("is_transitive"):
                meta["is_transitive"] = True
            if not meta["inverse"]:
                meta["inverse"] = row.get("inverse_uri")
            if not meta["description"]:
                meta["description"] = _first_value(row.get("comment"))

        all_properties = [
            PropertySummary(
                uri=uri,
                label=m["label"],
                prop_type=m["prop_type"],  # type: ignore[arg-type]
                domain_uri=m["domain"],
                range_uri=m["range"],
                is_transitive=m["is_transitive"],
                inverse_of_uri=m["inverse"],
                description=m["description"],
            )
            for uri, m in prop_meta.items()
        ]

        # Build ClassSummary list
        class_meta: dict[str, dict] = {}
        for row in cls_rows:
            uri = row.get("uri")
            if not uri:
                continue
            meta_c = class_meta.setdefault(
                uri, {"label": None, "parent": None, "description": None}
            )
            lbl = _first_value(row.get("label"))
            if lbl and not meta_c["label"]:
                meta_c["label"] = lbl
            if not meta_c["parent"]:
                meta_c["parent"] = row.get("parent_uri")
            cmt = _first_value(row.get("comment"))
            if cmt and not meta_c["description"]:
                meta_c["description"] = cmt

        classes = [
            ClassSummary(
                uri=uri,
                label=meta_c["label"],
                parent_uri=meta_c["parent"],
                property_count=prop_count_map.get(uri, 0),
                instance_count=inst_count.get(uri, 0),
                description=meta_c["description"],
            )
            for uri, meta_c in class_meta.items()
        ]

        namespaces = {**STANDARD_PREFIXES, **{
            p: ns for p, ns in self._prefix_to_ns.items()
        }}

        return SchemaResult(
            total_classes=len(classes),
            total_properties=len(all_properties),
            namespaces=namespaces,
            classes=classes,
            properties=all_properties,
        )

    async def get_class_detail(self, class_uri: str) -> ClassDetail:
        """Return full TBox detail for a single ontology class.

        Args:
            class_uri: Full URI of the class.

        Returns:
            ClassDetail with properties, hierarchy, and sample instances.

        Raises:
            KeyError: If the class does not exist in the store.
        """
        await self._ensure_prefix_map()

        # Explicit existence probe (review #9): a real leaf class with no
        # label/comment/parent/children/props/instances must NOT be reported
        # as "not found". Decide existence on the node alone.
        exists_rows = await self._run(
            "MATCH (c:Resource {uri: $uri}) RETURN c.uri AS uri LIMIT 1",
            uri=class_uri,
        )
        if not exists_rows:
            raise KeyError(f"Class not found: {class_uri}")

        meta_rows, prop_rows, child_rows, inst_rows = await asyncio.gather(
            self._run("""
                MATCH (c:Resource {uri: $uri})
                OPTIONAL MATCH (c)-[:rdfs__subClassOf]->(parent:Resource)
                RETURN
                    c.rdfs__label AS label,
                    c.rdfs__comment AS comment,
                    parent.uri AS parent_uri
            """, uri=class_uri),
            self._run("""
                MATCH (p:Resource)-[:rdfs__domain]->(c:Resource {uri: $uri})
                MATCH (p)-[:rdf__type]->(t:Resource)
                WHERE t.uri IN $prop_types
                OPTIONAL MATCH (p)-[:rdfs__range]->(r:Resource)
                RETURN DISTINCT
                    p.uri AS uri,
                    p.rdfs__label AS label,
                    t.uri AS prop_type,
                    r.uri AS range_uri
                ORDER BY p.uri
            """,
                uri=class_uri,
                prop_types=[
                    "http://www.w3.org/2002/07/owl#ObjectProperty",
                    "http://www.w3.org/2002/07/owl#DatatypeProperty",
                    "http://www.w3.org/2002/07/owl#AnnotationProperty",
                ],
            ),
            self._run("""
                MATCH (child:Resource)-[:rdfs__subClassOf]->(c:Resource {uri: $uri})
                RETURN DISTINCT child.uri AS child_uri
            """, uri=class_uri),
            self._run("""
                MATCH (inst:Resource)-[:rdf__type]->(c:Resource {uri: $uri})
                RETURN DISTINCT inst.uri AS uri
                LIMIT 3
            """, uri=class_uri),
        )

        # Existence already confirmed above (review #9) — no emptiness check
        # here, so a real leaf class with no metadata is returned, not raised.

        label = None
        description = None
        parent_uris_set: set[str] = set()
        for row in meta_rows:
            lbl = _first_value(row.get("label"))
            if lbl and not label:
                label = lbl
            cmt = _first_value(row.get("comment"))
            if cmt and not description:
                description = cmt
            if row.get("parent_uri"):
                parent_uris_set.add(row["parent_uri"])

        properties = []
        seen_props: set[str] = set()
        for row in prop_rows:
            uri = row.get("uri")
            if not uri or uri in seen_props:
                continue
            seen_props.add(uri)
            properties.append(
                PropertySummary(
                    uri=uri,
                    label=_first_value(row.get("label")),
                    prop_type=_OWL_TYPE_MAP.get(
                        row.get("prop_type") or "", "annotation"
                    ),  # type: ignore[arg-type]
                    domain_uri=class_uri,
                    range_uri=row.get("range_uri"),
                )
            )

        # Count instances with subclass inference
        cnt_rows = await self._run(
            """
            MATCH (inst:Resource)-[:rdf__type]->(c:Resource)-[:rdfs__subClassOf*0..]->(:Resource {uri: $uri})
            RETURN count(DISTINCT inst) AS cnt
            """,
            uri=class_uri,
        )
        inst_count = cnt_rows[0]["cnt"] if cnt_rows else 0

        return ClassDetail(
            uri=class_uri,
            label=label,
            description=description,
            parent_uris=list(parent_uris_set),
            child_uris=[r["child_uri"] for r in child_rows if r.get("child_uri")],
            properties=properties,
            instance_count=inst_count,
            sample_instance_uris=[r["uri"] for r in inst_rows if r.get("uri")],
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
            return _triples_to_xlsx(triples, label=target)

        # Default: TTL — build using rdflib
        return _triples_to_ttl(triples)

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


# ── Module helpers ────────────────────────────────────────────────────────────


def _first_value(val: Any) -> Any:
    """Unwrap single-element lists produced by handleMultival=ARRAY.

    n10s stores every RDF property as a list. This helper returns the first
    element for scalar display, or None if the value is empty.

    Args:
        val: Raw value from Neo4j (may be list or scalar).

    Returns:
        Scalar value (first element if list), or None.
    """
    if val is None:
        return None
    if isinstance(val, list):
        if not val:
            return None
        return val[0]
    return val


def _unpack_value(val: Any) -> Any:
    """Unwrap an ARRAY-config multi-value to scalar or list.

    If the list has exactly one element, return that element (scalar parity
    with Fuseki which returns single-value triples as scalars). If it has
    multiple elements, return the list. If empty, return None.

    Args:
        val: Raw property value from Neo4j.

    Returns:
        Scalar, list, or None.
    """
    if isinstance(val, list):
        if len(val) == 0:
            return None
        if len(val) == 1:
            return val[0]
        return val
    return val


def _triples_to_ttl(triples: list[dict]) -> bytes:
    """Serialize SPO rows from n10s export as Turtle bytes.

    Args:
        triples: List of dicts with keys s, p, o, isLiteral, literalType, literalLang.

    Returns:
        UTF-8 encoded Turtle bytes.
    """
    from rdflib import Graph, Literal, URIRef  # noqa: PLC0415
    from rdflib.namespace import XSD  # noqa: PLC0415

    g = Graph()
    for t in triples:
        subj = URIRef(t["s"])
        pred = URIRef(t["p"])
        if t.get("isLiteral"):
            lang = t.get("literalLang") or ""
            dtype = t.get("literalType") or ""
            if lang and lang != "null":
                obj = Literal(t["o"], lang=lang)
            elif dtype and dtype != "null" and dtype != str(XSD.string):
                obj = Literal(t["o"], datatype=URIRef(dtype))
            else:
                obj = Literal(t["o"])
        else:
            obj = URIRef(t["o"])
        g.add((subj, pred, obj))
    return g.serialize(format="turtle").encode()


def _triples_to_xlsx(triples: list[dict], label: str = "data") -> bytes:
    """Serialize SPO rows as an XLSX workbook.

    Args:
        triples: List of dicts with keys s, p, o.
        label: Sheet name.

    Returns:
        XLSX bytes.
    """
    import io  # noqa: PLC0415

    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "openpyxl is not installed. Run: uv add openpyxl"
        ) from exc

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = label
    ws.append(["Subject", "Predicate", "Object"])
    for t in triples:
        ws.append([t["s"], t["p"], t["o"]])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
