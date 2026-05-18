from __future__ import annotations

import asyncio
import logging
from typing import Any

from ontorag.core.sparql import (
    build_filter_sparql,
    uri_ref,
)
from ontorag.stores.base import EntityFilter, TraversalDirection, TraversalResult

logger = logging.getLogger(__name__)

_DATA = "urn:ontorag:data"
_MAX_DEPTH_HARD = 6


class _TraversalMixin:
    """L1 traversal tool implementations mixed into FusekiStore."""

    _namespaces: dict[str, str]
    _sparql_select: Any  # provided by FusekiStore at runtime

    async def traverse(
        self,
        start_uri: str,
        predicate: str | None = None,
        max_depth: int = 2,
        direction: TraversalDirection = TraversalDirection.outgoing,
    ) -> TraversalResult:
        """BFS traversal from a starting node up to max_depth hops."""
        max_depth = min(max_depth, _MAX_DEPTH_HARD)
        pfx = self._pfx()
        pred_filter = f"    FILTER(?pred = {uri_ref(predicate)})" if predicate else ""

        visited: set[str] = {start_uri}
        frontier: list[str] = [start_uri]
        nodes: list[dict[str, Any]] = [{"uri": start_uri, "depth": 0}]
        edges: list[dict[str, Any]] = []
        depth_reached = 0

        for depth in range(1, max_depth + 1):
            if not frontier:
                break
            depth_reached = depth
            values_block = " ".join(f"<{u}>" for u in frontier)
            new_frontier: list[str] = []

            if direction in (TraversalDirection.outgoing, TraversalDirection.both):
                out_q = f"""{pfx}
SELECT DISTINCT ?src ?pred ?tgt
WHERE {{
  VALUES ?src {{ {values_block} }}
  GRAPH <{_DATA}> {{
    ?src ?pred ?tgt .
    FILTER(isIRI(?tgt))
{pred_filter}
  }}
}}"""
                for b in (
                    (await self._sparql_select(out_q))
                    .get("results", {})
                    .get("bindings", [])
                ):
                    src, pred_uri, tgt = (
                        b["src"]["value"],
                        b["pred"]["value"],
                        b["tgt"]["value"],
                    )
                    edges.append({"from": src, "to": tgt, "predicate": pred_uri})
                    if tgt not in visited:
                        visited.add(tgt)
                        new_frontier.append(tgt)
                        nodes.append({"uri": tgt, "depth": depth})

            if direction in (TraversalDirection.incoming, TraversalDirection.both):
                in_q = f"""{pfx}
SELECT DISTINCT ?src ?pred ?tgt
WHERE {{
  VALUES ?tgt {{ {values_block} }}
  GRAPH <{_DATA}> {{
    ?src ?pred ?tgt .
    FILTER(isIRI(?src))
{pred_filter}
  }}
}}"""
                for b in (
                    (await self._sparql_select(in_q))
                    .get("results", {})
                    .get("bindings", [])
                ):
                    src, pred_uri, tgt = (
                        b["src"]["value"],
                        b["pred"]["value"],
                        b["tgt"]["value"],
                    )
                    edges.append({"from": src, "to": tgt, "predicate": pred_uri})
                    if src not in visited:
                        visited.add(src)
                        new_frontier.append(src)
                        nodes.append({"uri": src, "depth": depth})

            frontier = new_frontier

        # Batch-fetch labels for all discovered nodes + predicate URIs (TBox)
        # so LLM can read the result without needing extra describe_entity calls.
        if nodes:
            uris_block = " ".join(f"<{n['uri']}>" for n in nodes)
            lbl_q = f"{pfx}\nSELECT ?uri ?label WHERE {{ VALUES ?uri {{ {uris_block} }} GRAPH <{_DATA}> {{ ?uri rdfs:label ?label . }} }}"
            lbl_bindings = (
                (await self._sparql_select(lbl_q))
                .get("results", {})
                .get("bindings", [])
            )
            uri_labels = {
                b["uri"]["value"]: b["label"]["value"]
                for b in lbl_bindings
                if "uri" in b and "label" in b
            }
            for node in nodes:
                node["label"] = uri_labels.get(node["uri"])

        # Predicate labels live in the schema graph, not the data graph
        if edges:
            pred_uris = list({e["predicate"] for e in edges})
            pred_block = " ".join(f"<{u}>" for u in pred_uris)
            pred_q = (
                f"{pfx}\nSELECT ?p ?label WHERE {{ VALUES ?p {{ {pred_block} }} "
                f"?p rdfs:label ?label . }}"
            )
            try:
                pred_bindings = (
                    (await self._sparql_select(pred_q))
                    .get("results", {})
                    .get("bindings", [])
                )
                pred_labels = {
                    b["p"]["value"]: b["label"]["value"]
                    for b in pred_bindings
                    if "p" in b and "label" in b
                }
                for edge in edges:
                    edge["predicate_label"] = pred_labels.get(edge["predicate"])
            except Exception as exc:  # pragma: no cover — best-effort enrichment
                logger.debug("predicate label enrichment skipped: %s", exc)

        return TraversalResult(
            start_uri=start_uri,
            nodes=nodes,
            edges=edges,
            depth_reached=depth_reached,
        )

    async def find_path(
        self,
        uri_a: str,
        uri_b: str,
        max_depth: int = 4,
    ) -> TraversalResult:
        """BFS shortest-path search between two entities (both directions)."""
        max_depth = min(max_depth, _MAX_DEPTH_HARD)
        pfx = self._pfx()

        visited: set[str] = {uri_a}
        frontier: list[str] = [uri_a]
        # parent[node] = (parent_uri, predicate_uri)
        parent: dict[str, tuple[str | None, str | None]] = {uri_a: (None, None)}

        found = False
        for _ in range(max_depth):
            if not frontier or found:
                break
            values_block = " ".join(f"<{u}>" for u in frontier)

            out_query = f"""{pfx}
SELECT DISTINCT ?src ?pred ?tgt
WHERE {{
  VALUES ?src {{ {values_block} }}
  GRAPH <{_DATA}> {{
    ?src ?pred ?tgt .
    FILTER(isIRI(?tgt))
  }}
}}"""
            in_query = f"""{pfx}
SELECT DISTINCT ?src ?pred ?tgt
WHERE {{
  VALUES ?tgt {{ {values_block} }}
  GRAPH <{_DATA}> {{
    ?src ?pred ?tgt .
    FILTER(isIRI(?src))
  }}
}}"""
            out_rows_result, in_rows_result = await asyncio.gather(
                self._sparql_select(out_query),
                self._sparql_select(in_query),
            )

            new_frontier: list[str] = []

            # Process outgoing edges: ?src → ?tgt
            for b in out_rows_result.get("results", {}).get("bindings", []):
                src, pred_uri, tgt = (
                    b["src"]["value"],
                    b["pred"]["value"],
                    b["tgt"]["value"],
                )
                if tgt not in visited:
                    visited.add(tgt)
                    parent[tgt] = (src, pred_uri)
                    if tgt == uri_b:
                        found = True
                        break
                    new_frontier.append(tgt)
            if found:
                break

            # Process incoming edges: ?src ← ?tgt (frontier nodes are the ?tgt end)
            for b in in_rows_result.get("results", {}).get("bindings", []):
                src, pred_uri, tgt = (
                    b["src"]["value"],
                    b["pred"]["value"],
                    b["tgt"]["value"],
                )
                # src is the new node discovered; tgt is already in frontier/visited
                if src not in visited:
                    visited.add(src)
                    parent[src] = (tgt, pred_uri)
                    if src == uri_b:
                        found = True
                        break
                    new_frontier.append(src)
            if found:
                break

            frontier = new_frontier

        if uri_b not in parent:
            return TraversalResult(
                start_uri=uri_a, end_uri=uri_b, nodes=[], edges=[], depth_reached=0
            )

        # Reconstruct path
        path_nodes: list[dict[str, Any]] = []
        path_edges: list[dict[str, Any]] = []
        cur: str | None = uri_b
        while cur is not None:
            path_nodes.insert(0, {"uri": cur})
            par, pred_uri = parent[cur]
            if par is not None and pred_uri is not None:
                path_edges.insert(0, {"from": par, "to": cur, "predicate": pred_uri})
            cur = par

        # Batch-fetch labels for path nodes
        if path_nodes:
            uris_block = " ".join(f"<{n['uri']}>" for n in path_nodes)
            lbl_q = f"{pfx}\nSELECT ?uri ?label WHERE {{ VALUES ?uri {{ {uris_block} }} GRAPH <{_DATA}> {{ ?uri rdfs:label ?label . }} }}"
            lbl_bindings = (
                (await self._sparql_select(lbl_q))
                .get("results", {})
                .get("bindings", [])
            )
            uri_labels = {
                b["uri"]["value"]: b["label"]["value"]
                for b in lbl_bindings
                if "uri" in b and "label" in b
            }
            for node in path_nodes:
                node["label"] = uri_labels.get(node["uri"])

        return TraversalResult(
            start_uri=uri_a,
            end_uri=uri_b,
            nodes=path_nodes,
            edges=path_edges,
            depth_reached=len(path_edges),
        )

    async def find_related(
        self,
        class_uri_a: str,
        predicate: str,
        class_uri_b: str,
        filters_a: list[EntityFilter] | None = None,
        filters_b: list[EntityFilter] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Find pairs of entities from two classes connected by a predicate."""
        pfx = self._pfx()
        cls_a = uri_ref(class_uri_a)
        cls_b = uri_ref(class_uri_b)
        pred = uri_ref(predicate)

        ft_a, fl_a = build_filter_sparql(
            filters_a or [], subject_var="?a", var_prefix="fa"
        )
        ft_b, fl_b = build_filter_sparql(
            filters_b or [], subject_var="?b", var_prefix="fb"
        )

        query = f"""{pfx}
SELECT DISTINCT ?a ?aLabel ?b ?bLabel
WHERE {{
  GRAPH <{_DATA}> {{
    ?a a {cls_a} .
    ?a {pred} ?b .
    ?b a {cls_b} .
    OPTIONAL {{ ?a rdfs:label ?aLabel . }}
    OPTIONAL {{ ?b rdfs:label ?bLabel . }}
{ft_a}
{ft_b}
  }}
{fl_a}
{fl_b}
}}
LIMIT {limit}"""
        result = await self._sparql_select(query)
        out: list[dict[str, Any]] = []
        for b in result.get("results", {}).get("bindings", []):
            out.append(
                {
                    "entity_a": {
                        "uri": b["a"]["value"],
                        "label": b.get("aLabel", {}).get("value"),
                        "class_uri": class_uri_a,
                    },
                    "entity_b": {
                        "uri": b["b"]["value"],
                        "label": b.get("bLabel", {}).get("value"),
                        "class_uri": class_uri_b,
                    },
                }
            )
        return out

    async def property_path_closure(
        self,
        predicate_uri: str,
        start_uri: str | None = None,
        start_label: str | None = None,
        start_class_uri: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """SPARQL property-path closure with label-based start resolution.

        Accepts either an instance URI or an rdfs:label (case- and
        lang-tag-insensitive). When ``start_label`` is given without
        ``start_uri``, the SPARQL bundles the label lookup and the
        closure into a single round-trip — so the LLM never has to
        chain find_entities → property_path_query manually.
        """
        if not start_uri and not start_label:
            raise ValueError(
                "property_path_closure requires either start_uri or start_label"
            )

        pfx = self._pfx()
        pred = uri_ref(predicate_uri)

        if start_uri:
            start_clause = f"BIND({uri_ref(start_uri)} AS ?start)"
            type_clause = ""
        else:
            # SPARQL embeds the label lookup; LCASE/STR mirror our
            # filter-builder fix so multilingual labels match.
            label_val = start_label.replace('"', '\\"') if start_label else ""
            start_clause = (
                f'?start rdfs:label ?startLabel .\n'
                f'    FILTER(STR(?startLabel) = "{label_val}" '
                f'|| LCASE(STR(?startLabel)) = LCASE("{label_val}"))'
            )
            if start_class_uri:
                type_clause = f"?start a {uri_ref(start_class_uri)} ."
            else:
                type_clause = ""

        query = f"""{pfx}
SELECT DISTINCT ?reached (SAMPLE(?l) AS ?label)
WHERE {{
  GRAPH <{_DATA}> {{
    {start_clause}
    {type_clause}
    ?start {pred}+ ?reached .
    OPTIONAL {{ ?reached rdfs:label ?l . }}
  }}
}}
GROUP BY ?reached
ORDER BY STR(?reached)
LIMIT {limit}"""
        result = await self._sparql_select(query)
        out: list[dict[str, Any]] = []
        for b in result.get("results", {}).get("bindings", []):
            out.append(
                {
                    "uri": b["reached"]["value"],
                    "label": b.get("label", {}).get("value"),
                }
            )
        return out
