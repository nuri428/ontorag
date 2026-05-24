from __future__ import annotations

import os

from ontorag.stores.base import GraphStore

VALID_BACKENDS = ("fuseki", "neo4j")


def create_store() -> GraphStore:
    """Create a graph store backend selected by the GRAPH_STORE env var.

    Reads ``GRAPH_STORE`` (one of ``fuseki`` | ``neo4j``; default ``fuseki``)
    and builds the matching adapter from its own environment variables. This is
    the single construction seam for the store: callers depend on the
    :class:`~ontorag.stores.base.GraphStore` protocol, never on a concrete
    adapter, so swapping backends is an env-var change.

    Mirrors :func:`ontorag.api.deps.get_llm_provider` for the LLM layer.

    Returns:
        A configured object satisfying the GraphStore protocol.

    Raises:
        ValueError: If GRAPH_STORE names an unknown backend, or a recognised
            backend whose adapter is not yet available.
    """
    backend = os.environ.get("GRAPH_STORE", "fuseki").strip().lower()

    if backend == "fuseki":
        from ontorag.stores.fuseki import FusekiStore  # noqa: PLC0415

        return FusekiStore.from_env()

    if backend == "neo4j":
        # Adapter lands later in v0.5.0 (P1). Recognised but not yet wired.
        raise ValueError(
            "GRAPH_STORE=neo4j is not available yet — the Neo4j adapter ships "
            "in v0.5.0. Use GRAPH_STORE=fuseki for now."
        )

    raise ValueError(
        f"Unknown GRAPH_STORE: {backend!r}. Valid values: {', '.join(VALID_BACKENDS)}."
    )
