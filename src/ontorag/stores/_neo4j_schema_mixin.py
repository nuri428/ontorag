from __future__ import annotations

"""Schema (TBox) L1 tool implementations for Neo4jStore.

Implements: get_schema, get_class_detail.

Split out of neo4j.py to keep each module under the repo's 800-line cap.
Behaviour is identical to the in-class versions it replaced.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.sparql import STANDARD_PREFIXES
from ontorag.stores._neo4j_scope import build_where as _build_where
from ontorag.stores._neo4j_scope import ontology_scope_filter
from ontorag.stores._neo4j_values import first_scalar as _first_value
from ontorag.stores.base import (
    ClassDetail,
    ClassSummary,
    PropertySummary,
    SchemaResult,
)

if TYPE_CHECKING:
    from ontorag.stores.neo4j import Neo4jStore

logger = logging.getLogger(__name__)

# Hard cap on variable-length rdfs:subClassOf traversal (review #3).
_MAX_SUBCLASS_DEPTH = 10

# OWL prop type URI → protocol literal.
_OWL_TYPE_MAP: dict[str, str] = {
    "http://www.w3.org/2002/07/owl#ObjectProperty": "object",
    "http://www.w3.org/2002/07/owl#DatatypeProperty": "datatype",
    "http://www.w3.org/2002/07/owl#AnnotationProperty": "annotation",
}

_PROP_TYPE_URIS = [
    "http://www.w3.org/2002/07/owl#ObjectProperty",
    "http://www.w3.org/2002/07/owl#DatatypeProperty",
    "http://www.w3.org/2002/07/owl#AnnotationProperty",
]
_TRANSITIVE_URI = "http://www.w3.org/2002/07/owl#TransitiveProperty"


class _Neo4jSchemaMixin:
    """L1 schema tools (get_schema, get_class_detail) mixed into Neo4jStore."""

    # Provided by Neo4jStore at runtime
    _run: Any
    _ensure_prefix_map: Any
    _prefix_to_ns: dict[str, str]

    async def get_schema(  # type: ignore[override]
        self: "Neo4jStore",
        ontology: str | None = None,
    ) -> SchemaResult:
        """Return compact schema overview: class hierarchy + property counts.

        When ``ontology`` is not None, only TBox nodes (classes and properties)
        tagged with that id are returned; ``ontology=None`` returns all (union).
        Instance counts are also scoped to the given ontology.

        Args:
            ontology: Ontology id to scope the schema view, or None for all.

        Returns:
            SchemaResult with classes, properties, and namespace mapping.
        """
        await self._ensure_prefix_map()

        # Build scope clauses for class (c), property (p), and instance (inst)
        # node aliases.  All filters are bound params via ontology_scope_filter.
        cls_scope_frag, cls_scope_params = ontology_scope_filter(ontology, "c")
        prop_scope_frag, prop_scope_params = ontology_scope_filter(ontology, "p")
        inst_scope_frag, inst_scope_params = ontology_scope_filter(ontology, "inst")

        # Assemble each WHERE via where_parts/AND.join (robust to fragment
        # ordering — preferred over appending leading-" AND " to a hard-coded
        # WHERE body, which is fragile if conditions are reordered).
        cls_where = _build_where([cls_scope_frag])
        prop_where = _build_where(["t.uri IN $prop_types", prop_scope_frag])
        inst_where = _build_where(
            [
                "NOT (c:owl__ObjectProperty OR c:owl__DatatypeProperty "
                "OR c:owl__AnnotationProperty OR c:owl__Ontology)",
                inst_scope_frag,
            ]
        )

        cls_rows, prop_rows, inst_rows = await asyncio.gather(
            self._run(
                f"""
                MATCH (c:owl__Class)
                {cls_where}
                OPTIONAL MATCH (c)-[:rdfs__subClassOf]->(parent:Resource)
                RETURN DISTINCT
                    c.uri AS uri,
                    c.rdfs__label AS label,
                    parent.uri AS parent_uri,
                    c.rdfs__comment AS comment
                ORDER BY c.uri
                """,
                **cls_scope_params,
            ),
            self._run(
                f"""
                MATCH (p:Resource)-[:rdf__type]->(t:Resource)
                {prop_where}
                OPTIONAL MATCH (p)-[:rdfs__domain]->(d:Resource)
                OPTIONAL MATCH (p)-[:rdfs__range]->(r:Resource)
                OPTIONAL MATCH (p)-[:owl__inverseOf]->(inv:Resource)
                OPTIONAL MATCH (p)-[:rdf__type]->(trans:Resource {{uri: $transitive_uri}})
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
                prop_types=_PROP_TYPE_URIS,
                transitive_uri=_TRANSITIVE_URI,
                **prop_scope_params,
            ),
            self._run(
                f"""
                MATCH (inst:Resource)-[:rdf__type]->(c:owl__Class)
                {inst_where}
                RETURN c.uri AS class_uri, count(DISTINCT inst) AS cnt
                """,
                **inst_scope_params,
            ),
        )

        inst_count: dict[str, int] = {
            r["class_uri"]: r["cnt"] for r in inst_rows if r.get("class_uri")
        }

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

        namespaces = {**STANDARD_PREFIXES, **dict(self._prefix_to_ns)}

        return SchemaResult(
            total_classes=len(classes),
            total_properties=len(all_properties),
            namespaces=namespaces,
            classes=classes,
            properties=all_properties,
        )

    async def get_class_detail(  # type: ignore[override]
        self: "Neo4jStore",
        class_uri: str,
        ontology: str | None = None,
    ) -> ClassDetail:
        """Return full TBox detail for a single ontology class.

        When ``ontology`` is not None, the class node itself must be tagged with
        that id (``$ontology_id IN c._ontology``); an out-of-scope class raises
        KeyError — consistent with ``get_schema`` and ``find_entities`` scope
        semantics.  Instance counts and sample instances are likewise scoped.
        When ``ontology`` is None, the class is returned regardless of tagging
        (union/legacy behavior).

        Args:
            class_uri: Full URI of the class.
            ontology: Ontology id to scope the class + instance counts, or None.

        Returns:
            ClassDetail with properties, hierarchy, and sample instances.

        Raises:
            KeyError: If the class does not exist, or is not tagged with the
                requested ``ontology`` id.
        """
        await self._ensure_prefix_map()

        # Build instance scope filter for count and sample queries.
        inst_scope_frag, inst_scope_params = ontology_scope_filter(
            ontology, node_alias="inst"
        )
        inst_scope_and = f" AND {inst_scope_frag}" if inst_scope_frag else ""

        # Build class-node scope filter for the existence probe.
        cls_scope_frag, cls_scope_params = ontology_scope_filter(
            ontology, node_alias="c"
        )
        cls_where_parts = [cls_scope_frag] if cls_scope_frag else []
        cls_where = "WHERE " + " AND ".join(cls_where_parts) if cls_where_parts else ""

        # Explicit existence probe (review #9): a real leaf class with no
        # label/comment/parent/children/props/instances must NOT be reported
        # as "not found". Decide existence on the node alone.  When scoped, the
        # class node must also carry the ontology id (MEDIUM scope-consistency).
        exists_rows = await self._run(
            f"MATCH (c:Resource {{uri: $uri}}) {cls_where} "
            f"RETURN c.uri AS uri LIMIT 1",
            uri=class_uri,
            **cls_scope_params,
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
                prop_types=_PROP_TYPE_URIS,
            ),
            self._run("""
                MATCH (child:Resource)-[:rdfs__subClassOf]->(c:Resource {uri: $uri})
                RETURN DISTINCT child.uri AS child_uri
            """, uri=class_uri),
            self._run(
                f"""
                MATCH (inst:Resource)-[:rdf__type]->(c:Resource {{uri: $uri}})
                WHERE inst.uri IS NOT NULL{inst_scope_and}
                RETURN DISTINCT inst.uri AS uri
                LIMIT 3
                """,
                uri=class_uri,
                **inst_scope_params,
            ),
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

        # Count instances with subclass inference (capped — review #3).
        cnt_rows = await self._run(
            f"""
            MATCH (inst:Resource)-[:rdf__type]->(c:Resource)
                  -[:rdfs__subClassOf*0..{_MAX_SUBCLASS_DEPTH}]->(:Resource {{uri: $uri}})
            WHERE inst.uri IS NOT NULL{inst_scope_and}
            RETURN count(DISTINCT inst) AS cnt
            """,
            uri=class_uri,
            **inst_scope_params,
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
