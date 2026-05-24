from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ontorag.api.deps import get_store
from ontorag.stores.base import GraphStore, SimilarHit

router = APIRouter(prefix="/tools", tags=["tools"])


class FindSimilarRequest(BaseModel):
    """Request body for graph-embedding nearest-neighbour search."""

    uri: str = Field(
        description=(
            "Full URI of the query entity.  "
            "Passed as a bound parameter — never interpolated into Cypher."
        )
    )
    top_k: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of similar entities to return (1–100, default 10).",
    )
    mode: Literal["structural", "textual", "hybrid"] = Field(
        default="structural",
        description=(
            "Embedding mode: 'structural' (GDS FastRP graph topology), "
            "'textual' (EmbeddingProvider semantic), or "
            "'hybrid' (RRF fusion of both)."
        ),
    )


@router.post(
    "/similar",
    operation_id="find_similar",
    summary=(
        "그래프 임베딩 유사도 검색 — 구조적(FastRP) / 의미적(텍스트) / 혼합(RRF) "
        "유사 엔티티 반환 (Neo4j 전용)"
    ),
    response_model=list[SimilarHit],
)
async def find_similar(
    body: FindSimilarRequest,
    store: GraphStore = Depends(get_store),
) -> list[SimilarHit]:
    """Find the most similar ontology entities using graph embeddings.

    Only available when ``GRAPH_STORE=neo4j`` and embeddings have been built
    via ``ontorag embed``.  Returns 501 for Fuseki or any backend that does
    not expose the ``find_similar`` capability.

    Structural mode uses GDS FastRP (graph topology).
    Textual mode uses EmbeddingProvider cosine similarity (semantic content).
    Hybrid mode fuses both via Reciprocal Rank Fusion (RRF).

    Returns an empty list when the index is absent or the node has no embedding
    (never raises 500 for missing index / embedding).

    Args:
        body.uri: Full URI of the query entity.
        body.top_k: Maximum number of results (1–100, default 10).
        body.mode: "structural", "textual", or "hybrid".

    Returns:
        List of SimilarHit ordered by similarity score descending.

    Raises:
        HTTPException: 501 if the active backend does not support find_similar.
    """
    # Capability guard: only Neo4j exposes find_similar.
    fn = getattr(store, "find_similar", None)
    if fn is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Graph embedding similarity search is not supported by the "
                f"active graph store ({type(store).__name__}). "
                "This endpoint requires GRAPH_STORE=neo4j and embeddings built "
                "via 'ontorag embed'."
            ),
        )

    return await fn(body.uri, body.top_k, body.mode)
