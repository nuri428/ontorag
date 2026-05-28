"""Bayesian-network persistence for FusekiStore (v0.7.1).

Implements the :class:`~ontorag.stores.base.BayesianStore` capability by
storing the whole network as ``bn:`` triples in the probabilistic named graph
(``urn:ontorag:probabilistic`` or per-ontology scoped). Reuses the store's
existing GSP helpers — the network round-trips through one Graph put/get, so no
SPARQL UPDATE path is needed and writes are atomic at the named-graph level.

This is a *capability*, not part of the GraphStore protocol. The MCP routes
guard with ``getattr(store, "get_bayes_network", None)`` → 501 otherwise.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.bayes import (
    BayesNetwork,
    graph_to_network,
    network_to_graph,
    probabilistic_graph_uri,
)
from ontorag.core.ontology import validate_ontology_id

if TYPE_CHECKING:
    from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)


class _FusekiBayesMixin:
    """Bayesian-network storage capability mixed into FusekiStore.

    CPTs live exclusively in the probabilistic named graph — never schema/data.
    """

    # Provided by FusekiStore at runtime.
    _gsp_put: Any
    _gsp_get: Any
    _gsp_delete: Any
    _ensure_dataset: Any
    _count_graph: Any

    async def put_bayes_network(
        self: "FusekiStore",
        network: BayesNetwork,
        ontology: str | None = None,
    ) -> int:
        """Replace the stored network in the probabilistic graph (GSP PUT).

        Args:
            network: Validated BayesNetwork (structure + CPTs).
            ontology: Ontology id to scope under, or None for the default graph.

        Returns:
            Number of RDF statements written.

        Raises:
            ValueError: If the ontology id is invalid.
        """
        ontology = validate_ontology_id(ontology)
        await self._ensure_dataset()
        g = network_to_graph(network)
        await self._gsp_put(g, probabilistic_graph_uri(ontology))
        logger.info(
            "Stored Bayesian network (%d vars, %d cpds, %d triples) into %s",
            len(network.variables),
            len(network.cpds),
            len(g),
            probabilistic_graph_uri(ontology),
        )
        return len(g)

    async def get_bayes_network(
        self: "FusekiStore",
        ontology: str | None = None,
    ) -> BayesNetwork | None:
        """Return the stored network (GSP GET + parse), or None if empty."""
        ontology = validate_ontology_id(ontology)
        await self._ensure_dataset()
        g = await self._gsp_get(probabilistic_graph_uri(ontology))
        return graph_to_network(g)

    async def clear_bayes_network(
        self: "FusekiStore",
        ontology: str | None = None,
    ) -> int:
        """Drop the probabilistic graph for this scope (GSP DELETE)."""
        ontology = validate_ontology_id(ontology)
        await self._ensure_dataset()
        uri = probabilistic_graph_uri(ontology)
        removed = await self._count_graph(uri)
        await self._gsp_delete(uri)
        return removed
