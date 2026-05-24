from __future__ import annotations

from functools import lru_cache

from ontorag.stores.base import GraphStore
from ontorag.stores.factory import create_store


@lru_cache
def get_store() -> GraphStore:
    """Return a process-wide singleton graph store.

    The concrete backend is selected by the GRAPH_STORE environment variable
    (see :func:`ontorag.stores.factory.create_store`). The instance is created
    once and reused across all requests.

    Returns:
        A GraphStore implementation ready for use.
    """
    return create_store()
