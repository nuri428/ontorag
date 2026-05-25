from __future__ import annotations

import logging
from typing import Any

from ontorag.core.ontology import (
    data_graph_uri,
    graph_clause,
    schema_graph_uri,
    validate_ontology_id,
)
from ontorag.core.sparql import (
    STANDARD_PREFIXES,
    build_filter_sparql,
    build_prefix_block,
    uri_ref,
)
from ontorag.stores.base import AggFunc, AggregateResult, EntityFilter, EntityResult

logger = logging.getLogger(__name__)

# Both data- and schema-graph fragments come from the same authoritative
# helper (ontorag.core.ontology.graph_clause): None → union default graph
# (no GRAPH wrapper), a URI → GRAPH <uri> { ... }.
_data_clause = graph_clause
_schema_clause = graph_clause

_AGG_EXPR: dict[AggFunc, str] = {
    AggFunc.count: "COUNT(DISTINCT ?inst)",
    AggFunc.sum: "SUM(xsd:decimal(?group))",
    AggFunc.avg: "AVG(xsd:decimal(?group))",
    AggFunc.min: "MIN(xsd:decimal(?group))",
    AggFunc.max: "MAX(xsd:decimal(?group))",
}


class _EntityMixin:
    """L1 entity-level tool implementations mixed into FusekiStore."""

    _namespaces: dict[str, str]
    _sparql_select: Any  # provided by FusekiStore at runtime

    def _pfx(self) -> str:
        return build_prefix_block({**STANDARD_PREFIXES, **self._namespaces})

    async def find_entities(
        self,
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[EntityResult]:
        """Find instances of a class with optional filter conditions.

        Args:
            class_uri: Full URI or prefixed name of the ontology class.
            filters: Optional list of property-value conditions.
            limit: Maximum number of results.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            Matching entity list with properties.
        """
        ontology = validate_ontology_id(ontology)
        data_g = data_graph_uri(ontology) if ontology is not None else None
        schema_g = schema_graph_uri(ontology) if ontology is not None else None

        pfx = self._pfx()
        cls = uri_ref(class_uri)
        filter_triples, filter_line = build_filter_sparql(filters or [])

        # Subclass-aware (reasoning parity with Neo4j): an instance of any
        # subclass of <cls> matches. rdf:type lives in the data graph,
        # rdfs:subClassOf in the schema graph, so the path is joined across
        # the two named graphs. The UNION direct-match branch keeps results
        # correct even when no TBox/subClassOf is loaded.
        #
        # ontology=None: both patterns use the union default graph (no GRAPH
        # wrapper) — tdb2:unionDefaultGraph true makes the default graph the
        # union of all named graphs, backward-compat behavior preserved.
        # ontology=id: data patterns scoped to data_g, schema to schema_g.
        #
        # The direct-match arm is itself scoped to the data graph (HIGH #3):
        # it re-binds ?inst a <cls> inside GRAPH <data_g> rather than relying
        # on a bare FILTER(?type = <cls>) over the outer ?type binding — so
        # correctness no longer depends on the optimizer's join order.
        sub_main = (
            f"?inst a ?type .\n"
            f"    OPTIONAL {{ ?inst rdfs:label ?label . }}\n"
            f"{filter_triples}"
        )
        direct_main = (
            f"?inst a {cls} .\n"
            f"    OPTIONAL {{ ?inst rdfs:label ?label . }}\n"
            f"{filter_triples}"
        )
        uri_query = f"""{pfx}
SELECT DISTINCT ?inst ?label
WHERE {{
  {{
    {_data_clause(data_g, sub_main)}
    {_schema_clause(schema_g, f"?type rdfs:subClassOf* {cls} .")}
  }}
  UNION
  {{
    {_data_clause(data_g, direct_main)}
  }}
{filter_line}
}}
LIMIT {limit}"""
        uri_result = await self._sparql_select(uri_query)
        rows = uri_result.get("results", {}).get("bindings", [])
        if not rows:
            return []

        uris = [(b["inst"]["value"], b.get("label", {}).get("value")) for b in rows]

        # Batch-fetch all properties for found entities (including obj labels for URI values)
        values_block = " ".join(f"<{u}>" for u, _ in uris)
        prop_body = (
            f"?inst ?pred ?obj .\n"
            f"    FILTER(?pred != rdf:type)\n"
            f"    OPTIONAL {{ ?obj rdfs:label ?objLabel . }}"
        )
        prop_query = f"""{pfx}
SELECT ?inst ?pred ?obj ?objLabel
WHERE {{
  VALUES ?inst {{ {values_block} }}
  {_data_clause(data_g, prop_body)}
}}"""
        prop_result = await self._sparql_select(prop_query)

        # Group properties by entity URI; URI-valued objects include {"uri", "label"}
        props: dict[str, dict[str, Any]] = {u: {} for u, _ in uris}
        for b in prop_result.get("results", {}).get("bindings", []):
            eu = b["inst"]["value"]
            pred = b["pred"]["value"]
            obj_term = b["obj"]
            if eu not in props:
                continue
            if obj_term.get("type") == "uri":
                obj: Any = {
                    "uri": obj_term["value"],
                    "label": b.get("objLabel", {}).get("value"),
                }
            else:
                obj = obj_term["value"]
            existing = props[eu].get(pred)
            if existing is None:
                props[eu][pred] = obj
            elif isinstance(existing, list):
                existing.append(obj)
            else:
                props[eu][pred] = [existing, obj]

        return [
            EntityResult(
                uri=u,
                label=lbl,
                class_uri=class_uri,
                properties=props.get(u, {}),
            )
            for u, lbl in uris
        ]

    async def describe_entity(
        self,
        uri: str,
        predicates: list[str] | None = None,
        ontology: str | None = None,
    ) -> EntityResult:
        """Return all (or selected) properties of an entity.

        Args:
            uri: Full URI of the entity.
            predicates: Optional list of predicate URIs to restrict output.
            ontology: Ontology id for scoped query, or None for union.

        Returns:
            Entity with properties, including rdf:type.

        Raises:
            KeyError: If the entity does not exist in the store.
        """
        ontology = validate_ontology_id(ontology)
        data_g = data_graph_uri(ontology) if ontology is not None else None

        pfx = self._pfx()
        subj = uri_ref(uri)
        values_clause = (
            "  VALUES ?pred { " + " ".join(uri_ref(p) for p in predicates) + " }"
            if predicates
            else ""
        )

        inner_body = (
            f"{subj} ?pred ?obj .\n"
            f"    OPTIONAL {{ {subj} rdfs:label ?label . }}\n"
            f"    OPTIONAL {{ ?obj rdfs:label ?objLabel . }}"
        )
        query = f"""{pfx}
SELECT ?pred ?obj ?label ?objLabel
WHERE {{
{values_clause}
  {_data_clause(data_g, inner_body)}
}}"""
        result = await self._sparql_select(query)
        bindings = result.get("results", {}).get("bindings", [])
        if not bindings:
            raise KeyError(f"Entity not found: {uri}")

        label = next((b["label"]["value"] for b in bindings if "label" in b), None)
        class_uri: str | None = None
        properties: dict[str, Any] = {}
        rdf_type = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

        for b in bindings:
            pred = b["pred"]["value"]
            obj_term = b["obj"]
            obj_value = obj_term["value"]
            if pred == rdf_type:
                class_uri = obj_value
                continue
            if obj_term.get("type") == "uri":
                obj: Any = {
                    "uri": obj_value,
                    "label": b.get("objLabel", {}).get("value"),
                }
            else:
                obj = obj_value
            existing = properties.get(pred)
            if existing is None:
                properties[pred] = obj
            elif isinstance(existing, list):
                existing.append(obj)
            else:
                properties[pred] = [existing, obj]

        return EntityResult(
            uri=uri, label=label, class_uri=class_uri, properties=properties
        )

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
        ontology = validate_ontology_id(ontology)
        data_g = data_graph_uri(ontology) if ontology is not None else None
        schema_g = schema_graph_uri(ontology) if ontology is not None else None

        pfx = self._pfx()
        cls = uri_ref(class_uri)
        filter_triples, filter_line = build_filter_sparql(filters or [])

        # Subclass-aware count (reasoning parity) — see find_entities. The
        # direct-match arm is scoped to the data graph (HIGH #3) instead of a
        # bare FILTER(?type = <cls>), so correctness does not depend on the
        # optimizer's join order.
        sub_main = f"?inst a ?type .\n{filter_triples}"
        direct_main = f"?inst a {cls} .\n{filter_triples}"
        query = f"""{pfx}
SELECT (COUNT(DISTINCT ?inst) AS ?n)
WHERE {{
  {{
    {_data_clause(data_g, sub_main)}
    {_schema_clause(schema_g, f"?type rdfs:subClassOf* {cls} .")}
  }}
  UNION
  {{
    {_data_clause(data_g, direct_main)}
  }}
{filter_line}
}}"""
        result = await self._sparql_select(query)
        bindings = result.get("results", {}).get("bindings", [])
        return int(bindings[0]["n"]["value"]) if bindings else 0

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
        ontology = validate_ontology_id(ontology)
        data_g = data_graph_uri(ontology) if ontology is not None else None

        pfx = self._pfx()
        cls = uri_ref(class_uri)
        prop = uri_ref(group_by)
        agg_expr = _AGG_EXPR[agg]

        agg_body = f"?inst a {cls} .\n    ?inst {prop} ?group ."
        query = f"""{pfx}
SELECT ?group ({agg_expr} AS ?result)
WHERE {{
  {_data_clause(data_g, agg_body)}
}}
GROUP BY ?group
ORDER BY DESC(?result)"""
        result = await self._sparql_select(query)
        out: list[AggregateResult] = []
        for b in result.get("results", {}).get("bindings", []):
            if "group" not in b or "result" not in b:
                continue
            raw = b["result"]["value"]
            try:
                val: int | float = int(raw) if "." not in raw else float(raw)
            except ValueError:
                if agg != AggFunc.count:
                    logger.warning(
                        "aggregate: non-numeric value %r for agg=%s group=%r — skipping",
                        raw,
                        agg,
                        b.get("group", {}).get("value"),
                    )
                    continue  # skip this group instead of silently returning 0
                val = 0
            out.append(AggregateResult(group_value=b["group"]["value"], result=val))
        return out
