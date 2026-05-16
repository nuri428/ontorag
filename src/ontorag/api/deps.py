from __future__ import annotations

from functools import lru_cache

from ontorag.stores.fuseki import FusekiStore


@lru_cache
def get_store() -> FusekiStore:
    """Return a singleton FusekiStore configured from environment variables.

    The instance is created once and reused across all requests.

    Returns:
        FusekiStore instance ready for use.
    """
    return FusekiStore.from_env()
