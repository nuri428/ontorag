"""Causal-model persistence for Neo4jStore (v0.8.0) — backend parity.

Mirrors the Fuseki causal mixin using Neo4j's native model: causal variables
become ``:_CausalVariable`` nodes and edges become ``[:_CAUSES]`` relationships,
all tagged with a ``_scope`` property equal to the causal named-graph URI.
Dedicated labels keep them out of the ``:Resource`` ontology graph.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.causal import CausalModel, CausalVariable, causal_graph_uri
from ontorag.core.ontology import validate_ontology_id

if TYPE_CHECKING:
    from ontorag.stores.neo4j import Neo4jStore

logger = logging.getLogger(__name__)

_LABELS = "(n:_CausalModel OR n:_CausalVariable)"


class _Neo4jCausalMixin:
    """Causal-model storage capability mixed into Neo4jStore."""

    _run: Any
    _run_write: Any

    async def put_causal_model(
        self: "Neo4jStore",
        model: CausalModel,
        ontology: str | None = None,
    ) -> int:
        ontology = validate_ontology_id(ontology)
        scope = causal_graph_uri(ontology)

        # Replace semantics: drop existing model nodes for this scope.
        await self._run_write(
            f"MATCH (n) WHERE {_LABELS} AND n._scope = $scope DETACH DELETE n",
            scope=scope,
        )
        await self._run_write(
            "CREATE (:_CausalModel {_scope: $scope, name: $name, based_on: $based_on})",
            scope=scope,
            name=model.name,
            based_on=model.based_on,
        )

        variables = [
            {"uri": v.uri, "observed": v.observed, "label": v.label}
            for v in model.variables
        ]
        await self._run_write(
            """UNWIND $variables AS v
               CREATE (:_CausalVariable {
                   _scope: $scope, uri: v.uri, observed: v.observed, label: v.label
               })""",
            scope=scope,
            variables=variables,
        )

        edges = [{"cause": c, "effect": e} for c, e in model.edges]
        if edges:
            await self._run_write(
                """UNWIND $edges AS edge
                   MATCH (c:_CausalVariable {_scope: $scope, uri: edge.cause})
                   MATCH (e:_CausalVariable {_scope: $scope, uri: edge.effect})
                   CREATE (c)-[:_CAUSES]->(e)""",
                scope=scope,
                edges=edges,
            )

        written = 1 + len(variables) + len(edges)
        logger.info(
            "Stored causal model (%d vars, %d edges) into scope %s",
            len(variables),
            len(edges),
            scope,
        )
        return written

    async def get_causal_model(
        self: "Neo4jStore",
        ontology: str | None = None,
    ) -> CausalModel | None:
        ontology = validate_ontology_id(ontology)
        scope = causal_graph_uri(ontology)

        var_rows = await self._run(
            """MATCH (v:_CausalVariable {_scope: $scope})
               RETURN v.uri AS uri, v.observed AS observed, v.label AS label
               ORDER BY v.uri""",
            scope=scope,
        )
        if not var_rows:
            return None
        variables = [
            CausalVariable(
                uri=r["uri"],
                observed=bool(r["observed"]) if r.get("observed") is not None else True,
                label=r.get("label"),
            )
            for r in var_rows
        ]

        edge_rows = await self._run(
            """MATCH (c:_CausalVariable {_scope: $scope})-[:_CAUSES]->(e:_CausalVariable {_scope: $scope})
               RETURN c.uri AS cause, e.uri AS effect
               ORDER BY c.uri, e.uri""",
            scope=scope,
        )
        edges = [(r["cause"], r["effect"]) for r in edge_rows]

        meta = await self._run(
            "MATCH (n:_CausalModel {_scope: $scope}) RETURN n.name AS name, n.based_on AS based_on LIMIT 1",
            scope=scope,
        )
        name = meta[0]["name"] if meta else None
        based_on = meta[0]["based_on"] if meta else None

        return CausalModel(
            variables=variables, edges=edges, based_on=based_on, name=name
        )

    async def clear_causal_model(
        self: "Neo4jStore",
        ontology: str | None = None,
    ) -> int:
        ontology = validate_ontology_id(ontology)
        scope = causal_graph_uri(ontology)
        rows = await self._run(
            f"MATCH (n) WHERE {_LABELS} AND n._scope = $scope RETURN count(n) AS removed",
            scope=scope,
        )
        removed = int(rows[0]["removed"]) if rows else 0
        await self._run_write(
            f"MATCH (n) WHERE {_LABELS} AND n._scope = $scope DETACH DELETE n",
            scope=scope,
        )
        return removed
