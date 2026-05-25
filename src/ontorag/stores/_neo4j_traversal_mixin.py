from __future__ import annotations

"""Traversal L1 tool implementations for Neo4jStore.

Implements: traverse, find_path, property_path_closure, find_related.

Cypher variable-length paths replace the BFS loop used by FusekiStore —
Neo4j natively handles multi-hop traversal efficiently.
"""

import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.cypher import _safe_rel
from ontorag.stores._neo4j_scope import ontology_scope_filter
from ontorag.stores._neo4j_values import first_scalar
from ontorag.stores.base import EntityFilter, TraversalDirection, TraversalResult

if TYPE_CHECKING:
    from ontorag.stores.neo4j import Neo4jStore

logger = logging.getLogger(__name__)

_MAX_DEPTH_HARD = 6


class _Neo4jTraversalMixin:
    """L1 traversal tools mixed into Neo4jStore."""

    # Provided by Neo4jStore at runtime
    _run: Any
    _shorten_prefixed: Any
    _expand: Any
    _ensure_prefix_map: Any

    async def traverse(  # type: ignore[override]
        self: "Neo4jStore",
        start_uri: str,
        predicate: str | None = None,
        max_depth: int = 2,
        direction: TraversalDirection = TraversalDirection.outgoing,
        ontology: str | None = None,
    ) -> TraversalResult:
        """Traverse the graph from a starting node using Cypher variable-length paths.

        Args:
            start_uri: URI of the starting node.
            predicate: Predicate URI to follow; None = all predicates.
            max_depth: Maximum depth (hard limit: 6).
            direction: outgoing, incoming, or both.
            ontology: Ontology id to scope neighbor nodes, or None for all.

        Returns:
            TraversalResult with nodes and edges reachable from start_uri.
        """
        await self._ensure_prefix_map()
        max_depth = min(max_depth, _MAX_DEPTH_HARD)

        scope_frag, scope_params = ontology_scope_filter(
            ontology, node_alias="neighbor"
        )
        # Combine with the existing neighbor.uri <> $start_uri filter.
        scope_and = f" AND {scope_frag}" if scope_frag else ""

        rel_pattern = _rel_pattern(predicate, max_depth, direction, self._shorten_prefixed)

        rows = await self._run(
            f"""
            MATCH (start:Resource {{uri: $start_uri}}){rel_pattern}(neighbor:Resource)
            WHERE neighbor.uri <> $start_uri{scope_and}
            RETURN DISTINCT
                start.uri AS src_uri,
                neighbor.uri AS tgt_uri
            LIMIT 500
            """,
            start_uri=start_uri,
            **scope_params,
        )

        if not rows:
            return TraversalResult(
                start_uri=start_uri,
                nodes=[{"uri": start_uri, "depth": 0}],
                edges=[],
                depth_reached=0,
            )

        # Collect unique URIs
        seen_uris = {start_uri}
        nodes: list[dict[str, Any]] = [{"uri": start_uri, "depth": 0}]
        edges: list[dict[str, Any]] = []

        for row in rows:
            tgt = row.get("tgt_uri")
            if tgt and tgt not in seen_uris:
                seen_uris.add(tgt)
                nodes.append({"uri": tgt, "depth": 1})

        # Fetch relationship details for edge info.  The neighbor scope filter
        # MUST be applied here too: without it, edges to out-of-scope neighbors
        # leak into TraversalResult.edges even though those nodes were excluded
        # from the primary (node) query above (HIGH #1 scope leak).
        edge_rows = await self._run(
            f"""
            MATCH (start:Resource {{uri: $start_uri}})-[rel{_pred_rel_type_filter(predicate, self._shorten_prefixed)}]->(neighbor:Resource)
            WHERE neighbor.uri <> $start_uri{scope_and}
            RETURN DISTINCT
                start.uri AS from_uri,
                type(rel) AS rel_type,
                neighbor.uri AS to_uri
            LIMIT 500
            """,
            start_uri=start_uri,
            **scope_params,
        )

        for row in edge_rows:
            full_pred = self._expand(row["rel_type"])
            edges.append({
                "from": row["from_uri"],
                "to": row["to_uri"],
                "predicate": full_pred,
            })

        # Enrich nodes with labels
        await _enrich_labels(nodes, self._run, self._expand)

        # Enrich edges with predicate labels
        await _enrich_edge_pred_labels(edges, self._run, self._expand)

        return TraversalResult(
            start_uri=start_uri,
            nodes=nodes,
            edges=edges,
            depth_reached=min(max_depth, 1) if edges else 0,
        )

    async def find_path(  # type: ignore[override]
        self: "Neo4jStore",
        uri_a: str,
        uri_b: str,
        max_depth: int = 4,
        ontology: str | None = None,
    ) -> TraversalResult:
        """Find the shortest path between two entities using Cypher shortestPath.

        When ``ontology`` is not None, both endpoint nodes must carry that id
        in their ``_ontology`` list; if either is absent the method returns an
        empty result (consistent with scoped semantics).

        Args:
            uri_a: Starting entity URI.
            uri_b: Target entity URI.
            max_depth: Maximum path length (hard limit: 6).
            ontology: Ontology id to scope endpoint nodes, or None for all.

        Returns:
            TraversalResult with path nodes and edges, or empty if no path found.
        """
        await self._ensure_prefix_map()
        max_depth = min(max_depth, _MAX_DEPTH_HARD)

        scope_frag_a, scope_params_a = ontology_scope_filter(
            ontology, node_alias="a"
        )
        scope_frag_b, scope_params_b = ontology_scope_filter(
            ontology, node_alias="b"
        )
        # Both fragments reference the same param key "ontology_id" — only
        # pass it once (scope_params_a and scope_params_b are identical when
        # ontology is not None, so merging is safe).
        all_scope_params = {**scope_params_a}

        scope_where_parts = [f for f in [scope_frag_a, scope_frag_b] if f]
        scope_where = (
            f"WHERE {' AND '.join(scope_where_parts)}" if scope_where_parts else ""
        )

        rows = await self._run(
            f"""
            MATCH (a:Resource {{uri: $uri_a}}), (b:Resource {{uri: $uri_b}})
            {scope_where}
            MATCH path = shortestPath((a)-[*1..{max_depth}]-(b))
            RETURN
                [n IN nodes(path) | n.uri] AS node_uris,
                [n IN nodes(path) | n.rdfs__label] AS node_labels,
                [r IN relationships(path) | type(r)] AS rel_types,
                [r IN relationships(path) | startNode(r).uri] AS rel_froms,
                [r IN relationships(path) | endNode(r).uri] AS rel_tos
            """,
            uri_a=uri_a,
            uri_b=uri_b,
            **all_scope_params,
        )

        if not rows:
            return TraversalResult(
                start_uri=uri_a, end_uri=uri_b, nodes=[], edges=[], depth_reached=0
            )

        row = rows[0]
        node_uris: list[str] = row["node_uris"] or []
        node_labels: list[Any] = row["node_labels"] or []
        rel_types: list[str] = row["rel_types"] or []
        rel_froms: list[str] = row["rel_froms"] or []
        rel_tos: list[str] = row["rel_tos"] or []

        nodes = [
            {"uri": uri, "label": first_scalar(lbl)}
            for uri, lbl in zip(node_uris, node_labels)
        ]
        edges = [
            {
                "from": frm,
                "to": to,
                "predicate": self._expand(rtype),
            }
            for frm, rtype, to in zip(rel_froms, rel_types, rel_tos)
        ]

        return TraversalResult(
            start_uri=uri_a,
            end_uri=uri_b,
            nodes=nodes,
            edges=edges,
            depth_reached=len(edges),
        )

    async def property_path_closure(  # type: ignore[override]
        self: "Neo4jStore",
        predicate_uri: str,
        start_uri: str | None = None,
        start_label: str | None = None,
        start_class_uri: str | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return all entities reachable via a transitive predicate.

        Three modes (match FusekiStore semantics):
        - Mode 1 (start_uri): instance closure from a specific URI.
        - Mode 2 (start_label ± start_class_uri): label lookup + closure.
        - Mode 3 (start_class_uri only): closure for every instance of a class.

        Args:
            predicate_uri: Predicate URI to follow transitively.
            start_uri: Mode 1 — start from this instance.
            start_label: Mode 2 — match instance by rdfs:label.
            start_class_uri: Disambiguates Mode 2 or triggers Mode 3 alone.
            limit: Max results.
            ontology: Ontology id to scope start + reached nodes, or None.

        Returns:
            List of ``{"uri": str, "label": str | None}`` sorted by URI.
        """
        await self._ensure_prefix_map()

        if not (start_uri or start_label or start_class_uri):
            raise ValueError(
                "property_path_closure requires at least one of "
                "start_uri / start_label / start_class_uri"
            )

        # Scope filter applied on the start node (restricts which starting
        # instances are considered) and on reached nodes (output scope).
        start_scope_frag, start_scope_params = ontology_scope_filter(
            ontology, node_alias="start"
        )
        reached_scope_frag, _ = ontology_scope_filter(
            ontology, node_alias="reached"
        )
        # Both use the same param key "ontology_id" — pass once.
        scope_params = start_scope_params  # shared key

        # Build WHERE additions for start and reached.
        start_scope_and = f" AND {start_scope_frag}" if start_scope_frag else ""
        reached_scope_and = f" AND {reached_scope_frag}" if reached_scope_frag else ""

        # Validate the shortened rel-type before any backtick interpolation.
        short_pred = _safe_rel(self._shorten_prefixed(predicate_uri))
        if start_uri:
            # Mode 1: instance closure. start_class_uri is parameterized
            # (never f-string interpolated) to prevent Cypher injection.
            type_filter = ""
            if start_class_uri:
                type_filter = (
                    "MATCH (start)-[:rdf__type]->(c:Resource)"
                    f"-[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->"
                    "(:Resource {uri: $start_class_uri})"
                )
            # Scope the start node too (HIGH #2): a start_uri belonging to a
            # different ontology must NOT be accepted under this scope — yields
            # empty, consistent with describe_entity's scope check.
            start_where = (
                f"WHERE {start_scope_frag}" if start_scope_frag else ""
            )
            rows = await self._run(
                f"""
                MATCH (start:Resource {{uri: $start_uri}})
                {start_where}
                {type_filter}
                MATCH (start)-[:`{short_pred}`*1..{_MAX_DEPTH_HARD}]->(reached:Resource)
                WHERE reached.uri IS NOT NULL{reached_scope_and}
                RETURN DISTINCT reached.uri AS uri, reached.rdfs__label AS label
                ORDER BY reached.uri
                LIMIT $limit
                """,
                start_uri=start_uri,
                start_class_uri=start_class_uri,
                limit=limit,
                **scope_params,
            )
        elif start_label:
            # Mode 2: label lookup + closure
            label_lower = start_label.lower()
            if start_class_uri:
                # Disambiguate by class: start must be rdf:type -> class hierarchy
                rows = await self._run(
                    f"""
                    MATCH (start:Resource)-[:rdf__type]->(c:Resource)
                          -[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->(:Resource {{uri: $start_class_uri}})
                    WHERE start.rdfs__label IS NOT NULL
                      AND (start.rdfs__label[0] = $start_label
                           OR toLower(start.rdfs__label[0]) = $label_lower)
                      {start_scope_and}
                    MATCH (start)-[:`{short_pred}`*1..{_MAX_DEPTH_HARD}]->(reached:Resource)
                    WHERE reached.uri IS NOT NULL{reached_scope_and}
                    RETURN DISTINCT reached.uri AS uri, reached.rdfs__label AS label
                    ORDER BY reached.uri
                    LIMIT $limit
                    """,
                    start_class_uri=start_class_uri,
                    start_label=start_label,
                    label_lower=label_lower,
                    limit=limit,
                    **scope_params,
                )
            else:
                rows = await self._run(
                    f"""
                    MATCH (start:Resource)
                    WHERE start.rdfs__label IS NOT NULL
                      AND (start.rdfs__label[0] = $start_label
                           OR toLower(start.rdfs__label[0]) = $label_lower)
                      {start_scope_and}
                    MATCH (start)-[:`{short_pred}`*1..{_MAX_DEPTH_HARD}]->(reached:Resource)
                    WHERE reached.uri IS NOT NULL{reached_scope_and}
                    RETURN DISTINCT reached.uri AS uri, reached.rdfs__label AS label
                    ORDER BY reached.uri
                    LIMIT $limit
                    """,
                    start_label=start_label,
                    label_lower=label_lower,
                    limit=limit,
                    **scope_params,
                )
        else:
            # Mode 3: class-wide closure
            rows = await self._run(
                f"""
                MATCH (start:Resource)-[:rdf__type]->(c:Resource)
                      -[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->(:Resource {{uri: $start_class_uri}})
                WHERE start.uri IS NOT NULL{start_scope_and}
                MATCH (start)-[:`{short_pred}`*1..{_MAX_DEPTH_HARD}]->(reached:Resource)
                WHERE reached.uri IS NOT NULL{reached_scope_and}
                RETURN DISTINCT reached.uri AS uri, reached.rdfs__label AS label
                ORDER BY reached.uri
                LIMIT $limit
                """,
                start_class_uri=start_class_uri,
                limit=limit,
                **scope_params,
            )

        return [
            {
                "uri": row["uri"],
                "label": first_scalar(row.get("label")),
            }
            for row in rows
            if row.get("uri")
        ]

    async def find_related(  # type: ignore[override]
        self: "Neo4jStore",
        class_uri_a: str,
        predicate: str,
        class_uri_b: str,
        filters_a: list[EntityFilter] | None = None,
        filters_b: list[EntityFilter] | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find pairs of entities from two classes connected by a predicate.

        Args:
            class_uri_a: Class of the subject entity.
            predicate: Connecting predicate URI.
            class_uri_b: Class of the object entity.
            filters_a: Optional filters for subject entities.
            filters_b: Optional filters for object entities.
            limit: Maximum result pairs.
            ontology: Ontology id to scope both entity sides, or None for all.

        Returns:
            List of {entity_a: dict, entity_b: dict} pairs.
        """
        await self._ensure_prefix_map()

        # Validate the shortened rel-type before any backtick interpolation.
        short_pred = _safe_rel(self._shorten_prefixed(predicate))
        from ontorag.stores._neo4j_entity_mixin import (  # noqa: PLC0415
            _build_filter_cypher,
        )

        scope_frag_a, scope_params = ontology_scope_filter(
            ontology, node_alias="a"
        )
        scope_frag_b, _ = ontology_scope_filter(ontology, node_alias="b")
        # Both fragments share the same "ontology_id" param key — pass once.

        # Build filter conditions for a and b separately
        fa_where, fa_params = _build_filter_cypher(
            filters_a or [], self._shorten_prefixed
        )
        fb_where, fb_params = _build_filter_cypher(
            filters_b or [], self._shorten_prefixed
        )

        # Rename param keys to avoid collision
        fa_params_renamed = {f"fa_{k}": v for k, v in fa_params.items()}
        fb_params_renamed = {f"fb_{k}": v for k, v in fb_params.items()}
        for old_k, new_k in zip(fa_params, fa_params_renamed):
            if old_k in fa_where:
                fa_where = fa_where.replace(f"${old_k}", f"$fa_{old_k}")
        for old_k in fb_params:
            if f"${old_k}" in fb_where:
                fb_where = fb_where.replace(f"${old_k}", f"$fb_{old_k}")

        where_parts = [p for p in [scope_frag_a, scope_frag_b, fa_where, fb_where] if p]
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        all_params: dict[str, Any] = {
            "class_uri_a": class_uri_a,
            "class_uri_b": class_uri_b,
            "limit": limit,
            **scope_params,
            **fa_params_renamed,
            **fb_params_renamed,
        }

        rows = await self._run(
            f"""
            MATCH (a:Resource)-[:rdf__type]->(ca:Resource)
                  -[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->(:Resource {{uri: $class_uri_a}})
            MATCH (b:Resource)-[:rdf__type]->(cb:Resource)
                  -[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->(:Resource {{uri: $class_uri_b}})
            MATCH (a)-[:`{short_pred}`]->(b)
            {where_clause}
            RETURN DISTINCT
                a.uri AS uri_a,
                a.rdfs__label AS label_a,
                b.uri AS uri_b,
                b.rdfs__label AS label_b
            LIMIT $limit
            """,
            **all_params,
        )

        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "entity_a": {
                        "uri": row["uri_a"],
                        "label": first_scalar(row.get("label_a")),
                        "class_uri": class_uri_a,
                    },
                    "entity_b": {
                        "uri": row["uri_b"],
                        "label": first_scalar(row.get("label_b")),
                        "class_uri": class_uri_b,
                    },
                }
            )
        return out


# ── Cypher pattern helpers ────────────────────────────────────────────────────


def _rel_pattern(
    predicate: str | None,
    max_depth: int,
    direction: TraversalDirection,
    shorten_fn: "callable",  # type: ignore[valid-type]
) -> str:
    """Build Cypher variable-length relationship pattern.

    Args:
        predicate: Optional predicate URI to filter by.
        max_depth: Max hops.
        direction: outgoing / incoming / both.
        shorten_fn: URI to shortened form converter.

    Returns:
        Cypher relationship pattern string, e.g. ``-[*1..2]->`` or
        ``-[:pk__hasType*1..2]->``.
    """
    if predicate:
        short = _safe_rel(shorten_fn(predicate).replace(":", "__"))
        rel_spec = f"[:`{short}`*1..{max_depth}]"
    else:
        rel_spec = f"[*1..{max_depth}]"

    if direction == TraversalDirection.outgoing:
        return f"-{rel_spec}->"
    if direction == TraversalDirection.incoming:
        return f"<-{rel_spec}-"
    return f"-{rel_spec}-"


def _pred_rel_type_filter(
    predicate: str | None, shorten_fn: "callable"  # type: ignore[valid-type]
) -> str:
    """Build single-hop relationship type filter for edge detail query."""
    if not predicate:
        return ""
    short = _safe_rel(shorten_fn(predicate).replace(":", "__"))
    return f"[:`{short}`]"


async def _enrich_labels(
    nodes: list[dict[str, Any]],
    run_fn: "callable",  # type: ignore[valid-type]
    expand_fn: "callable",  # type: ignore[valid-type]
) -> None:
    """Fetch and attach rdfs:label values to node dicts in-place.

    Args:
        nodes: Node dicts with "uri" key.
        run_fn: Async Neo4j run function.
        expand_fn: Shortened key → full URI converter.
    """
    if not nodes:
        return
    uris = [n["uri"] for n in nodes if n.get("uri")]
    if not uris:
        return
    rows = await run_fn(
        "MATCH (n:Resource) WHERE n.uri IN $uris "
        "RETURN n.uri AS uri, n.rdfs__label AS label",
        uris=uris,
    )
    label_map = {
        row["uri"]: first_scalar(row.get("label"))
        for row in rows
        if row.get("uri")
    }
    for node in nodes:
        node["label"] = label_map.get(node["uri"])


async def _enrich_edge_pred_labels(
    edges: list[dict[str, Any]],
    run_fn: "callable",  # type: ignore[valid-type]
    expand_fn: "callable",  # type: ignore[valid-type]
) -> None:
    """Fetch and attach predicate labels to edge dicts in-place.

    Args:
        edges: Edge dicts with "predicate" (full URI) key.
        run_fn: Async Neo4j run function.
        expand_fn: Shortened key → full URI converter (not used here, kept for API parity).
    """
    if not edges:
        return
    pred_uris = list({e["predicate"] for e in edges})
    if not pred_uris:
        return
    rows = await run_fn(
        "MATCH (p:Resource) WHERE p.uri IN $uris "
        "RETURN p.uri AS uri, p.rdfs__label AS label",
        uris=pred_uris,
    )
    label_map = {
        row["uri"]: first_scalar(row.get("label"))
        for row in rows
        if row.get("uri")
    }
    for edge in edges:
        edge["predicate_label"] = label_map.get(edge["predicate"])
