from __future__ import annotations

"""Thin async Qdrant wrapper for the Fuseki vector embedding backend.

Point IDs are deterministic UUID5 values derived from the entity URI so that
repeated calls are idempotent (upsert = create-or-replace).  The entity URI
is stored in the point payload so it can be retrieved without a secondary
SPARQL lookup.

All parameters passed to the Qdrant client (collection name, vectors, IDs)
are bound values — never raw-interpolated into strings.  Collection names
are module-level constants so they cannot be user-supplied.

Lazy import: ``qdrant_client`` is an optional dependency in the ``[vector]``
extra.  A clear ``ValueError`` is raised when the package is missing (mirrors
the neo4j driver pattern in ``stores/factory.py``).
"""

import logging
import os
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # type: only imports live here

logger = logging.getLogger(__name__)

# ── Public collection name constants (never user-supplied) ────────────────────

#: Qdrant collection for structural (FastRP) embeddings — dim 256.
STRUCT_COLLECTION: str = "ontorag_struct"
#: Qdrant collection for textual embeddings — dim = provider.dimension.
TEXT_COLLECTION: str = "ontorag_text"

# Qdrant UUID5 namespace — stable across restarts.
_UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # NAMESPACE_URL


def _point_id(uri: str) -> str:
    """Return a deterministic UUID5 string for a given entity URI.

    Args:
        uri: Full entity URI used as the UUID5 name.

    Returns:
        UUID string that is stable for the same URI across restarts.
    """
    return str(uuid.uuid5(_UUID_NS, uri))


class QdrantWrapper:
    """Async helper over ``AsyncQdrantClient`` for the Fuseki embedding mixin.

    All public methods translate high-level operations (ensure_collection,
    upsert, query, retrieve_vector) into the qdrant-client v1.9 API.

    Args:
        url: Qdrant server URL (default ``http://localhost:6333``).
            Override via ``QDRANT_URL`` env var or pass explicitly.

    Raises:
        ValueError: If ``qdrant_client`` package is not installed.
    """

    def __init__(self, url: str = "http://localhost:6333") -> None:
        try:
            from qdrant_client import AsyncQdrantClient  # noqa: PLC0415
        except ImportError as exc:
            raise ValueError(
                "qdrant-client is not installed. "
                "Install it with: uv add 'ontorag[vector]' "
                "(or pip install 'ontorag[vector]')"
            ) from exc
        self._client = AsyncQdrantClient(url=url, timeout=30)

    @classmethod
    def from_env(cls) -> "QdrantWrapper":
        """Create from ``QDRANT_URL`` env var (default ``http://localhost:6333``).

        Returns:
            Configured QdrantWrapper instance.
        """
        url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        return cls(url=url)

    async def ensure_collection(
        self,
        name: str,
        dim: int,
    ) -> None:
        """Create a cosine-similarity collection, recreating it if the dimension changed.

        Idempotent: no-op when the collection exists with the correct dimension.

        Args:
            name: Collection name (use the module-level constants).
            dim: Vector dimension to use for the collection.
        """
        from qdrant_client.models import Distance, VectorParams  # noqa: PLC0415

        try:
            info = await self._client.get_collection(name)
            existing_dim = info.config.params.vectors.size  # type: ignore[union-attr]
            if existing_dim == dim:
                logger.debug("Qdrant collection '%s' already exists (dim=%d).", name, dim)
                return
            # Dimension mismatch — drop and recreate.
            logger.info(
                "Qdrant collection '%s' has dim=%d (expected %d); recreating.",
                name,
                existing_dim,
                dim,
            )
            await self._client.delete_collection(name)
        except Exception:
            pass  # Collection does not exist yet — create it below.

        await self._client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s' (dim=%d, cosine).", name, dim)

    async def upsert(
        self,
        collection: str,
        points: list[tuple[str, list[float]]],
    ) -> int:
        """Upsert a batch of (uri, vector) pairs into a Qdrant collection.

        Point IDs are deterministic UUID5 values derived from the URI.
        The URI is also stored in the payload for later retrieval.

        Args:
            collection: Target collection name.
            points: List of (uri, vector) tuples to upsert.

        Returns:
            Number of points successfully upserted.
        """
        if not points:
            return 0

        from qdrant_client.models import PointStruct  # noqa: PLC0415

        batch = [
            PointStruct(
                id=_point_id(uri),
                vector=vector,
                payload={"uri": uri},
            )
            for uri, vector in points
        ]

        try:
            await self._client.upsert(collection_name=collection, points=batch)
            return len(batch)
        except Exception as exc:
            logger.error("Qdrant upsert to '%s' failed: %s", collection, exc)
            return 0

    async def query(
        self,
        collection: str,
        vector: list[float],
        top_k: int,
    ) -> list[tuple[str, float]]:
        """Return the top-k nearest neighbours for a query vector.

        Args:
            collection: Collection to search.
            vector: Query vector (must match collection dimension).
            top_k: Maximum results to return.

        Returns:
            List of (uri, score) tuples ordered by descending cosine score.
            Returns ``[]`` if the collection is absent or any error occurs.
        """
        try:
            hits = await self._client.search(
                collection_name=collection,
                query_vector=vector,
                limit=top_k,
                with_payload=True,
            )
            return [
                (hit.payload.get("uri", ""), hit.score)  # type: ignore[union-attr]
                for hit in hits
                if hit.payload and "uri" in hit.payload
            ]
        except Exception as exc:
            logger.debug("Qdrant search on '%s' failed: %s", collection, exc)
            return []

    async def retrieve_vector(
        self,
        collection: str,
        uri: str,
    ) -> list[float] | None:
        """Retrieve the stored vector for a single entity URI.

        Uses the deterministic UUID5 point ID derived from the URI.

        Args:
            collection: Collection to retrieve from.
            uri: Entity URI to look up.

        Returns:
            The stored vector, or ``None`` if the point is absent or any
            error occurs.
        """
        pt_id = _point_id(uri)
        try:
            results = await self._client.retrieve(
                collection_name=collection,
                ids=[pt_id],
                with_vectors=True,
                with_payload=False,
            )
            if not results:
                return None
            vec = results[0].vector
            if vec is None:
                return None
            # qdrant-client returns list[float] or dict[str, list[float]]
            # for named/unnamed vectors respectively.
            if isinstance(vec, list):
                return vec
            return None
        except Exception as exc:
            logger.debug("Qdrant retrieve from '%s' failed: %s", collection, exc)
            return None

    async def delete_collection(self, name: str) -> None:
        """Drop a Qdrant collection, ignoring errors if it does not exist.

        Args:
            name: Collection name to drop.
        """
        try:
            await self._client.delete_collection(name)
        except Exception as exc:
            logger.debug("Qdrant delete_collection '%s' (ignored): %s", name, exc)

    async def aclose(self) -> None:
        """Release the underlying HTTP client connections.

        Safe to call even if the client was never used.
        """
        try:
            await self._client.close()
        except Exception:
            pass
