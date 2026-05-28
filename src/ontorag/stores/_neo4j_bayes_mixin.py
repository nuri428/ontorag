"""Bayesian-network persistence for Neo4jStore (v0.7.2) — backend parity.

Mirrors :class:`~ontorag.stores._fuseki_bayes_mixin._FusekiBayesMixin` using
Neo4j's native model instead of RDF triples: variables and CPDs become
``:_BayesVariable`` / ``:_BayesCPD`` nodes, tagged with a ``_scope`` property
equal to the probabilistic named-graph URI (``urn:ontorag:probabilistic`` or
per-ontology scoped). Both backends return an identical :class:`BayesNetwork`.

Storage is isolated from ontology data nodes: dedicated underscore-prefixed
labels, never mixed into the ``:Resource`` graph (the equivalent of "CPTs go in
the probabilistic graph only, never schema/data").

A capability, not part of the GraphStore protocol — guarded by ``getattr`` in
the routes.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.bayes import (
    BayesNetwork,
    BayesVariable,
    CPD,
    probabilistic_graph_uri,
)
from ontorag.core.ontology import validate_ontology_id

if TYPE_CHECKING:
    from ontorag.stores.neo4j import Neo4jStore

logger = logging.getLogger(__name__)

# Fixed internal labels (literals, never interpolated from user input).
_LABELS = "(n:_BayesNetwork OR n:_BayesVariable OR n:_BayesCPD)"


class _Neo4jBayesMixin:
    """Bayesian-network storage capability mixed into Neo4jStore."""

    # Provided by Neo4jStore at runtime.
    _run: Any
    _run_write: Any

    async def put_bayes_network(
        self: "Neo4jStore",
        network: BayesNetwork,
        ontology: str | None = None,
    ) -> int:
        """Replace the stored network for this scope (delete + recreate nodes).

        Args:
            network: Validated BayesNetwork (structure + CPTs).
            ontology: Ontology id to scope under, or None for the default scope.

        Returns:
            Number of nodes written.
        """
        ontology = validate_ontology_id(ontology)
        scope = probabilistic_graph_uri(ontology)

        # Replace semantics: drop the existing network for this scope first.
        await self._run_write(
            f"MATCH (n) WHERE {_LABELS} AND n._scope = $scope DETACH DELETE n",
            scope=scope,
        )

        await self._run_write(
            "CREATE (:_BayesNetwork {_scope: $scope, name: $name})",
            scope=scope,
            name=network.name,
        )

        variables = [
            {
                "uri": v.uri,
                "states": list(v.states),
                "label": v.label,
                "represents": v.represents,
            }
            for v in network.variables
        ]
        await self._run_write(
            """UNWIND $variables AS v
               CREATE (:_BayesVariable {
                   _scope: $scope, uri: v.uri, states: v.states,
                   label: v.label, represents: v.represents
               })""",
            scope=scope,
            variables=variables,
        )

        cpds = [
            {
                "variable": c.variable,
                "evidence": list(c.evidence),
                "values": json.dumps(c.values),
            }
            for c in network.cpds
        ]
        await self._run_write(
            """UNWIND $cpds AS c
               CREATE (:_BayesCPD {
                   _scope: $scope, variable: c.variable,
                   evidence: c.evidence, values: c.values
               })""",
            scope=scope,
            cpds=cpds,
        )

        written = 1 + len(variables) + len(cpds)
        logger.info(
            "Stored Bayesian network (%d vars, %d cpds) into scope %s",
            len(variables),
            len(cpds),
            scope,
        )
        return written

    async def get_bayes_network(
        self: "Neo4jStore",
        ontology: str | None = None,
    ) -> BayesNetwork | None:
        """Reconstruct the stored network for this scope, or None if empty."""
        ontology = validate_ontology_id(ontology)
        scope = probabilistic_graph_uri(ontology)

        var_rows = await self._run(
            """MATCH (v:_BayesVariable {_scope: $scope})
               RETURN v.uri AS uri, v.states AS states,
                      v.label AS label, v.represents AS represents
               ORDER BY v.uri""",
            scope=scope,
        )
        if not var_rows:
            return None

        variables = [
            BayesVariable(
                uri=r["uri"],
                states=list(r["states"]),
                label=r.get("label"),
                represents=r.get("represents"),
            )
            for r in var_rows
        ]

        cpd_rows = await self._run(
            """MATCH (c:_BayesCPD {_scope: $scope})
               RETURN c.variable AS variable, c.evidence AS evidence,
                      c.values AS values
               ORDER BY c.variable""",
            scope=scope,
        )
        cpds = [
            CPD(
                variable=r["variable"],
                evidence=list(r["evidence"]) if r.get("evidence") else [],
                values=json.loads(r["values"]),
            )
            for r in cpd_rows
        ]

        name_rows = await self._run(
            "MATCH (n:_BayesNetwork {_scope: $scope}) RETURN n.name AS name LIMIT 1",
            scope=scope,
        )
        name = name_rows[0]["name"] if name_rows else None

        return BayesNetwork(variables=variables, cpds=cpds, name=name)

    async def clear_bayes_network(
        self: "Neo4jStore",
        ontology: str | None = None,
    ) -> int:
        """Delete all network nodes for this scope; return how many were removed."""
        ontology = validate_ontology_id(ontology)
        scope = probabilistic_graph_uri(ontology)
        # Count then delete — two statements keep the Cypher unambiguous across
        # Neo4j 5.x versions; the tiny gap is irrelevant for a single-user admin op.
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
