"""Causal-model persistence for FusekiStore (v0.8.0).

Implements :class:`~ontorag.stores.base.CausalStore` by storing the causal DAG
as ``causal:`` triples in the causal named graph (``urn:ontorag:causal`` or
per-ontology scoped). Reuses the store's GSP helpers — one Graph put/get round
trip, atomic at the named-graph level. Capability, not GraphStore protocol.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.causal import (
    CausalModel,
    causal_graph_uri,
    graph_to_model,
    model_to_graph,
)
from ontorag.core.ontology import validate_ontology_id

if TYPE_CHECKING:
    from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)


class _FusekiCausalMixin:
    """Causal-model storage capability mixed into FusekiStore."""

    _gsp_put: Any
    _gsp_get: Any
    _gsp_delete: Any
    _ensure_dataset: Any
    _count_graph: Any

    async def put_causal_model(
        self: "FusekiStore",
        model: CausalModel,
        ontology: str | None = None,
    ) -> int:
        ontology = validate_ontology_id(ontology)
        await self._ensure_dataset()
        g = model_to_graph(model)
        await self._gsp_put(g, causal_graph_uri(ontology))
        logger.info(
            "Stored causal model (%d vars, %d edges, %d triples) into %s",
            len(model.variables),
            len(model.edges),
            len(g),
            causal_graph_uri(ontology),
        )
        return len(g)

    async def get_causal_model(
        self: "FusekiStore",
        ontology: str | None = None,
    ) -> CausalModel | None:
        ontology = validate_ontology_id(ontology)
        await self._ensure_dataset()
        g = await self._gsp_get(causal_graph_uri(ontology))
        return graph_to_model(g)

    async def clear_causal_model(
        self: "FusekiStore",
        ontology: str | None = None,
    ) -> int:
        ontology = validate_ontology_id(ontology)
        await self._ensure_dataset()
        uri = causal_graph_uri(ontology)
        removed = await self._count_graph(uri)
        await self._gsp_delete(uri)
        return removed
