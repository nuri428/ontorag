from __future__ import annotations

import logging
from typing import Any

from ontorag.core.sparql import (
    STANDARD_PREFIXES,
    build_filter_sparql,
    build_prefix_block,
    uri_ref,
)
from ontorag.stores.base import AggFunc, AggregateResult, EntityFilter, EntityResult

logger = logging.getLogger(__name__)

_DATA = "urn:ontorag:data"

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

    async def _sparql_select(self, sparql: str) -> dict[str, Any]:
        raise NotImplementedError

    def _pfx(self) -> str:
        return build_prefix_block({**STANDARD_PREFIXES, **self._namespaces})

    async def find_entities(
        self,
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        limit: int = 100,
    ) -> list[EntityResult]:
        """Find instances of a class with optional filter conditions."""
        pfx = self._pfx()
        cls = uri_ref(class_uri)
        filter_triples, filter_line = build_filter_sparql(filters or [])

        uri_query = f"""{pfx}
SELECT DISTINCT ?inst ?label
WHERE {{
  GRAPH <{_DATA}> {{
    ?inst a {cls} .
    OPTIONAL {{ ?inst rdfs:label ?label . }}
{filter_triples}
  }}
{filter_line}
}}
LIMIT {limit}"""
        uri_result = await self._sparql_select(uri_query)
        rows = uri_result.get("results", {}).get("bindings", [])
        if not rows:
            return []

        uris = [(b["inst"]["value"], b.get("label", {}).get("value")) for b in rows]

        # Batch-fetch all properties for found entities
        values_block = " ".join(f"<{u}>" for u, _ in uris)
        prop_query = f"""{pfx}
SELECT ?inst ?pred ?obj
WHERE {{
  VALUES ?inst {{ {values_block} }}
  GRAPH <{_DATA}> {{
    ?inst ?pred ?obj .
    FILTER(?pred != rdf:type)
  }}
}}"""
        prop_result = await self._sparql_select(prop_query)

        # Group properties by entity URI
        props: dict[str, dict[str, Any]] = {u: {} for u, _ in uris}
        for b in prop_result.get("results", {}).get("bindings", []):
            eu = b["inst"]["value"]
            pred = b["pred"]["value"]
            obj = b["obj"]["value"]
            if eu not in props:
                continue
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
    ) -> EntityResult:
        """Return all (or selected) properties of an entity."""
        pfx = self._pfx()
        subj = uri_ref(uri)
        values_clause = (
            "  VALUES ?pred { " + " ".join(uri_ref(p) for p in predicates) + " }"
            if predicates
            else ""
        )

        query = f"""{pfx}
SELECT ?pred ?obj ?label
WHERE {{
{values_clause}
  GRAPH <{_DATA}> {{
    {subj} ?pred ?obj .
    OPTIONAL {{ {subj} rdfs:label ?label . }}
  }}
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
            obj = b["obj"]["value"]
            if pred == rdf_type:
                class_uri = obj
                continue
            existing = properties.get(pred)
            if existing is None:
                properties[pred] = obj
            elif isinstance(existing, list):
                existing.append(obj)
            else:
                properties[pred] = [existing, obj]

        return EntityResult(uri=uri, label=label, class_uri=class_uri, properties=properties)

    async def count_entities(
        self,
        class_uri: str,
        filters: list[EntityFilter] | None = None,
    ) -> int:
        """Count instances of a class matching optional filters."""
        pfx = self._pfx()
        cls = uri_ref(class_uri)
        filter_triples, filter_line = build_filter_sparql(filters or [])

        query = f"""{pfx}
SELECT (COUNT(DISTINCT ?inst) AS ?n)
WHERE {{
  GRAPH <{_DATA}> {{
    ?inst a {cls} .
{filter_triples}
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
    ) -> list[AggregateResult]:
        """Group instances by a property and apply an aggregation function."""
        pfx = self._pfx()
        cls = uri_ref(class_uri)
        prop = uri_ref(group_by)
        agg_expr = _AGG_EXPR[agg]

        query = f"""{pfx}
SELECT ?group ({agg_expr} AS ?result)
WHERE {{
  GRAPH <{_DATA}> {{
    ?inst a {cls} .
    ?inst {prop} ?group .
  }}
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
                val = 0
            out.append(AggregateResult(group_value=b["group"]["value"], result=val))
        return out
