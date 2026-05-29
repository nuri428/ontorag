from __future__ import annotations

import logging
import os

from ontorag.stores.base import GraphStore

logger = logging.getLogger(__name__)

VALID_BACKENDS = ("fuseki", "neo4j", "falkordb")


def create_store() -> GraphStore:
    """Create a graph store backend selected by the GRAPH_STORE env var.

    Reads ``GRAPH_STORE`` (one of ``fuseki`` | ``neo4j``; default ``fuseki``)
    and builds the matching adapter from its own environment variables. This is
    the single construction seam for the store: callers depend on the
    :class:`~ontorag.stores.base.GraphStore` protocol, never on a concrete
    adapter, so swapping backends is an env-var change.

    When ``ONTOLOGY_ACCESS`` is set (non-empty), the concrete store is wrapped
    in an :class:`~ontorag.stores.access_wrapper.AccessControlledStore` that
    enforces per-ontology read/write permissions at the GraphStore boundary.
    When ``ONTOLOGY_ACCESS`` is absent or empty the raw store is returned
    unchanged (zero overhead, fully backward-compatible).

    Mirrors :func:`ontorag.api.deps.get_llm_provider` for the LLM layer.

    Returns:
        A configured object satisfying the GraphStore protocol, optionally
        wrapped with access control.

    Raises:
        ValueError: If GRAPH_STORE names an unknown backend, or a recognised
            backend whose adapter is not yet available, or if ONTOLOGY_ACCESS
            contains malformed entries.
    """
    backend = os.environ.get("GRAPH_STORE", "fuseki").strip().lower()

    if backend == "fuseki":
        from ontorag.stores.fuseki import FusekiStore  # noqa: PLC0415

        store: GraphStore = FusekiStore.from_env()

    elif backend == "neo4j":
        try:
            from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415
        except ImportError as exc:
            raise ValueError(
                "GRAPH_STORE=neo4j requires the 'neo4j' Python driver. "
                "Install it with: uv add 'ontorag[neo4j]' (or pip install neo4j)"
            ) from exc
        store = Neo4jStore.from_env()

    elif backend == "falkordb":
        try:
            from ontorag.stores.falkordb import FalkorDBStore  # noqa: PLC0415
        except ImportError as exc:
            raise ValueError(
                "GRAPH_STORE=falkordb requires the 'falkordb' client. "
                "Install it with: uv add 'ontorag[falkordb]' (or pip install falkordb)"
            ) from exc
        store = FalkorDBStore.from_env()

    else:
        raise ValueError(
            f"Unknown GRAPH_STORE: {backend!r}. Valid values: {', '.join(VALID_BACKENDS)}."
        )

    # Optionally wrap with per-ontology access control (zero cost when unset).
    from ontorag.core.access import AccessPolicy  # noqa: PLC0415
    from ontorag.stores.access_wrapper import AccessControlledStore  # noqa: PLC0415

    policy = AccessPolicy.from_env()
    if policy is not None:
        logger.info("Per-ontology access control active (ONTOLOGY_ACCESS is set).")
        return AccessControlledStore(store, policy)

    return store
