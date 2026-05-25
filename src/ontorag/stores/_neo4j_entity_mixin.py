from __future__ import annotations

"""Entity-level L1 tool implementations for Neo4jStore.

Implements: find_entities, describe_entity, count_entities, aggregate.

Intentional divergence from FusekiStore (documented):
  - find_entities / count_entities follow rdfs:subClassOf*0.. chains,
    providing OWL subclass inference that current Fuseki (--mem, no reasoner)
    does not perform.
  - Property values may be lists (handleMultival=ARRAY); unpack_value() maps
    single-element lists → scalar to match Fuseki's output shape.
"""

import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.cypher import _safe_rel
from ontorag.stores._neo4j_scope import ontology_scope_filter
from ontorag.stores._neo4j_values import first_scalar, unpack_value
from ontorag.stores.base import AggFunc, AggregateResult, EntityFilter, EntityResult

if TYPE_CHECKING:
    from ontorag.stores.neo4j import Neo4jStore

logger = logging.getLogger(__name__)

# Hard cap on variable-length rdfs:subClassOf traversal to terminate on
# pathological cyclic hierarchies (A ⊂ B ⊂ A) and bound query cost.
_MAX_SUBCLASS_DEPTH = 10

_AGG_CYPHER: dict[AggFunc, str] = {
    AggFunc.count: "count(DISTINCT inst)",
    AggFunc.sum: "sum(toFloat(grpVal))",
    AggFunc.avg: "avg(toFloat(grpVal))",
    AggFunc.min: "min(toFloat(grpVal))",
    AggFunc.max: "max(toFloat(grpVal))",
}


class _Neo4jEntityMixin:
    """L1 entity-level tools mixed into Neo4jStore."""

    # These are provided by Neo4jStore at runtime
    _run: Any
    _shorten: Any
    _shorten_prefixed: Any
    _expand: Any
    _tbox_type_list: Any
    _ensure_prefix_map: Any

    async def find_entities(  # type: ignore[override]
        self: "Neo4jStore",
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[EntityResult]:
        """Find instances of a class with subclass inference.

        Follows [:rdfs__subClassOf*0..] — intentional divergence from Fuseki
        which does not apply subclass inference in its current configuration.

        Args:
            class_uri: Full URI of the target class.
            filters: Optional property-value conditions.
            limit: Maximum number of results.
            ontology: Ontology id to scope results, or None for all (union).

        Returns:
            List of EntityResult matching the class (and its subclasses).
        """
        await self._ensure_prefix_map()

        # Build ontology scope filter (empty when ontology=None → union).
        scope_frag, scope_params = ontology_scope_filter(ontology, node_alias="inst")

        # Build filter WHERE conditions
        filter_where, filter_params = _build_filter_cypher(
            filters or [], self._shorten_prefixed
        )

        # Combine scope + property filters into a single WHERE clause.
        where_parts = [p for p in [scope_frag, filter_where] if p]
        where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        rows = await self._run(
            f"""
            MATCH (inst:Resource)-[:rdf__type]->(c:Resource)
                  -[:rdfs__subClassOf*0..{_MAX_SUBCLASS_DEPTH}]->(:Resource {{uri: $class_uri}})
            {where_clause}
            RETURN DISTINCT inst
            LIMIT $limit
            """,
            class_uri=class_uri,
            limit=limit,
            **scope_params,
            **filter_params,
        )

        if not rows:
            return []

        results: list[EntityResult] = []
        for row in rows:
            node = row.get("inst") or {}
            uri = node.get("uri") if isinstance(node, dict) else getattr(node, "get", lambda k, d=None: d)("uri")
            if not uri:
                continue
            node_props = dict(node) if isinstance(node, dict) else {k: v for k, v in node.items()}
            props, label = _extract_props(node_props, self._expand)
            results.append(
                EntityResult(
                    uri=uri,
                    label=label,
                    class_uri=class_uri,
                    properties=props,
                )
            )
        return results

    async def describe_entity(  # type: ignore[override]
        self: "Neo4jStore",
        uri: str,
        predicates: list[str] | None = None,
        ontology: str | None = None,
    ) -> EntityResult:
        """Return all (or selected) properties and relationships of an entity.

        Includes outgoing relationships (as URI-valued properties) and
        expands shortened property keys / relationship types back to full URIs.

        Args:
            uri: Full URI of the entity.
            predicates: Optional list of predicate URIs to restrict output.
            ontology: Ontology id to scope lookup, or None for all (union).

        Returns:
            EntityResult with all properties and relationships.

        Raises:
            KeyError: If the entity does not exist (or is outside the scope).
        """
        await self._ensure_prefix_map()

        scope_frag, scope_params = ontology_scope_filter(ontology, node_alias="n")
        # When scoped, verify the entity belongs to the requested ontology.
        where_clause = f"WHERE {scope_frag}" if scope_frag else ""

        # Fetch node properties + outgoing relationships + rdf:type
        rows = await self._run(
            f"""
            MATCH (n:Resource {{uri: $uri}})
            {where_clause}
            OPTIONAL MATCH (n)-[rel]->(neighbor:Resource)
            RETURN
                n AS node,
                type(rel) AS rel_type,
                neighbor.uri AS neighbor_uri,
                neighbor.rdfs__label AS neighbor_label
            """,
            uri=uri,
            **scope_params,
        )

        if not rows:
            raise KeyError(f"Entity not found: {uri}")

        # Extract node-level properties from first row
        first_node = rows[0].get("node")
        node_props = dict(first_node) if first_node is not None else {}
        props, label = _extract_props(node_props, self._expand)

        class_uri: str | None = None

        # Layer in outgoing relationship values
        for row in rows:
            rel_type = row.get("rel_type")
            neighbor_uri = row.get("neighbor_uri")
            if not rel_type or not neighbor_uri:
                continue

            full_pred = self._expand(rel_type)

            if predicates and full_pred not in predicates:
                continue

            # rdf:type → extract class
            if full_pred == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":
                class_uri = neighbor_uri
                continue

            neighbor_label_raw = row.get("neighbor_label")
            obj: Any = {
                "uri": neighbor_uri,
                "label": first_scalar(neighbor_label_raw),
            }

            existing = props.get(full_pred)
            if existing is None:
                props[full_pred] = obj
            elif isinstance(existing, list):
                existing.append(obj)
            else:
                props[full_pred] = [existing, obj]

        # Filter to requested predicates if given (apply to final props dict)
        if predicates:
            props = {k: v for k, v in props.items() if k in predicates}

        return EntityResult(uri=uri, label=label, class_uri=class_uri, properties=props)

    async def count_entities(  # type: ignore[override]
        self: "Neo4jStore",
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        ontology: str | None = None,
    ) -> int:
        """Count instances of a class with subclass inference.

        Args:
            class_uri: Full URI of the target class.
            filters: Optional filter conditions.
            ontology: Ontology id to scope results, or None for all (union).

        Returns:
            Number of matching instances.
        """
        await self._ensure_prefix_map()

        scope_frag, scope_params = ontology_scope_filter(ontology, node_alias="inst")
        filter_where, filter_params = _build_filter_cypher(
            filters or [], self._shorten_prefixed
        )

        where_parts = [p for p in [scope_frag, filter_where] if p]
        where_clause = "WHERE " + " AND ".join(where_parts) if where_parts else ""

        rows = await self._run(
            f"""
            MATCH (inst:Resource)-[:rdf__type]->(c:Resource)
                  -[:rdfs__subClassOf*0..{_MAX_SUBCLASS_DEPTH}]->(:Resource {{uri: $class_uri}})
            {where_clause}
            RETURN count(DISTINCT inst) AS cnt
            """,
            class_uri=class_uri,
            **scope_params,
            **filter_params,
        )
        return rows[0]["cnt"] if rows else 0

    async def aggregate(  # type: ignore[override]
        self: "Neo4jStore",
        class_uri: str,
        group_by: str,
        agg: AggFunc = AggFunc.count,
        ontology: str | None = None,
    ) -> list[AggregateResult]:
        """Group class instances by a property and aggregate.

        Supports grouping by both literal properties (node props) and
        relationship targets (object property URIs).

        Args:
            class_uri: Class to aggregate over.
            group_by: Property URI to group by.
            agg: Aggregation function.
            ontology: Ontology id to scope results, or None for all (union).

        Returns:
            List of group_value → result pairs sorted by result descending.
        """
        await self._ensure_prefix_map()

        scope_frag, scope_params = ontology_scope_filter(ontology, node_alias="inst")
        # Append scope filter after the MATCH chains using WHERE (or AND).
        scope_where = f"WHERE {scope_frag}" if scope_frag else ""

        # Validate the shortened identifier before any backtick interpolation
        # (used both as a relationship type and as a property key below).
        short_prop = _safe_rel(self._shorten_prefixed(group_by))
        agg_expr = _AGG_CYPHER[agg]

        # Try relationship-based grouping first (object property)
        rows = await self._run(
            f"""
            MATCH (inst:Resource)-[:rdf__type]->(c:Resource)
                  -[:rdfs__subClassOf*0..{_MAX_SUBCLASS_DEPTH}]->(:Resource {{uri: $class_uri}})
            MATCH (inst)-[:`{short_prop}`]->(grpNode:Resource)
            {scope_where}
            RETURN grpNode.uri AS grpVal, {agg_expr} AS result
            ORDER BY result DESC
            """,
            class_uri=class_uri,
            **scope_params,
        )

        if not rows:
            # Fall back to literal property aggregation
            lit_where_parts = [f"inst.`{short_prop}` IS NOT NULL"]
            if scope_frag:
                lit_where_parts.append(scope_frag)
            lit_where = "WHERE " + " AND ".join(lit_where_parts)
            rows = await self._run(
                f"""
                MATCH (inst:Resource)-[:rdf__type]->(c:Resource)
                      -[:rdfs__subClassOf*0..{_MAX_SUBCLASS_DEPTH}]->(:Resource {{uri: $class_uri}})
                {lit_where}
                WITH inst, inst.`{short_prop}`[0] AS grpVal
                RETURN grpVal, {agg_expr} AS result
                ORDER BY result DESC
                """,
                class_uri=class_uri,
                **scope_params,
            )

        out: list[AggregateResult] = []
        for row in rows:
            grp = row.get("grpVal")
            res = row.get("result")
            if grp is None or res is None:
                continue
            try:
                val: int | float = int(res) if isinstance(res, int) or (isinstance(res, float) and res == int(res)) else float(res)
            except (TypeError, ValueError):
                if agg != AggFunc.count:
                    logger.warning("aggregate: non-numeric result %r — skipping", res)
                    continue
                val = 0
            out.append(AggregateResult(group_value=str(grp), result=val))
        return out


# ── Internal helpers ──────────────────────────────────────────────────────────


def _build_filter_cypher(
    filters: list[EntityFilter],
    shorten_fn: "callable",  # type: ignore[valid-type]
) -> tuple[str, dict[str, Any]]:
    """Build Cypher WHERE clause fragments from EntityFilter list.

    Args:
        filters: List of EntityFilter conditions.
        shorten_fn: Function to convert predicate URI to n10s shortened form.

    Returns:
        ``(where_clause_body, params_dict)`` — both empty when filters is empty.
    """
    if not filters:
        return "", {}

    from ontorag.stores.base import FilterOp  # noqa: PLC0415 (avoid circular)

    parts: list[str] = []
    params: dict[str, Any] = {}

    for i, f in enumerate(filters):
        # Validate the shortened property key before backtick interpolation.
        short_prop = _safe_rel(shorten_fn(f.property))
        param_key = f"fv{i}"
        params[param_key] = f.value

        prop_expr = f"inst.`{short_prop}`[0]"  # unwrap ARRAY single value

        if f.op == FilterOp.contains:
            parts.append(f"CONTAINS(toString({prop_expr}), ${param_key})")
        elif f.op == FilterOp.starts_with:
            parts.append(f"toString({prop_expr}) STARTS WITH ${param_key}")
        elif f.op == FilterOp.eq:
            parts.append(
                f"({prop_expr} = ${param_key} "
                f"OR toString({prop_expr}) = toString(${param_key}) "
                f"OR toLower(toString({prop_expr})) = toLower(toString(${param_key})))"
            )
        else:
            parts.append(f"{prop_expr} {f.op.value} ${param_key}")

    return " AND ".join(parts), params


def _extract_props(
    node_props: dict[str, Any],
    expand_fn: "callable",  # type: ignore[valid-type]
) -> tuple[dict[str, Any], str | None]:
    """Extract and expand node properties, separating out rdfs:label.

    Converts shortened property keys (prefix__local) back to full URIs and
    unwraps ARRAY single-element lists to scalars.

    Args:
        node_props: Raw property dict from Neo4j node.
        expand_fn: Function to expand shortened key to full URI.

    Returns:
        ``(properties_dict, label)`` where label is the rdfs:label value or None.
    """
    props: dict[str, Any] = {}
    label: str | None = None
    skip_keys = {"uri"}

    for key, val in node_props.items():
        if key in skip_keys:
            continue
        full_key = expand_fn(key)
        unpacked = unpack_value(val)

        if full_key == "http://www.w3.org/2000/01/rdf-schema#label":
            label = str(unpacked) if unpacked is not None else None
            # Strip lang tag if present (keepLangTag=true stores "text@lang")
            if label and "@" in label:
                label = label.split("@")[0]
        else:
            props[full_key] = unpacked

    return props, label
