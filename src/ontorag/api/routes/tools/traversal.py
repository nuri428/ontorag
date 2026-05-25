from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ontorag.api.deps import get_store
from ontorag.stores.base import (
    EntityFilter,
    GraphStore,
    TraversalDirection,
    TraversalResult,
)
from pydantic import BaseModel, Field

router = APIRouter(prefix="/tools", tags=["tools"])

_MAX_DEPTH = 6


class TraverseRequest(BaseModel):
    """Request body for graph traversal."""

    start_uri: str
    predicate: str | None = None
    max_depth: int = Field(default=2, ge=1, le=_MAX_DEPTH)
    direction: TraversalDirection = TraversalDirection.outgoing
    ontology: str | None = None


class FindPathRequest(BaseModel):
    """Request body for path finding between two entities."""

    uri_a: str
    uri_b: str
    max_depth: int = Field(default=4, ge=1, le=_MAX_DEPTH)
    ontology: str | None = None


class FindRelatedRequest(BaseModel):
    """Request body for multi-hop related entity query."""

    class_uri_a: str
    predicate: str
    class_uri_b: str
    filters_a: list[EntityFilter] | None = None
    filters_b: list[EntityFilter] | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    ontology: str | None = None


@router.post(
    "/traverse",
    operation_id="traverse_graph",
    summary="시작 노드에서 그래프 순회 (TransitiveProperty inference 포함)",
    response_model=TraversalResult,
)
async def traverse_graph(
    body: TraverseRequest,
    store: GraphStore = Depends(get_store),
) -> TraversalResult:
    """Traverse the graph from a starting node.

    Inference-aware: follows owl:TransitiveProperty closures when enabled.

    Args:
        body.start_uri: URI of the starting node.
        body.predicate: Predicate URI to follow. None means all predicates.
        body.max_depth: Maximum traversal depth (max 6).
        body.direction: outgoing, incoming, or both.

    Returns:
        Nodes and edges reachable from start_uri.
    """
    try:
        return await store.traverse(
            body.start_uri,
            body.predicate,
            body.max_depth,
            body.direction,
            ontology=body.ontology,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="traverse_graph: Day 6에 구현 예정")


@router.post(
    "/path",
    operation_id="find_path",
    summary="두 엔티티 간 최단 경로 탐색",
    response_model=TraversalResult,
)
async def find_path(
    body: FindPathRequest,
    store: GraphStore = Depends(get_store),
) -> TraversalResult:
    """Find the shortest path between two entities.

    Args:
        body.uri_a: Starting entity URI.
        body.uri_b: Target entity URI.
        body.max_depth: Maximum path length (max 6).

    Returns:
        Path nodes and edges, or empty result if no path found within max_depth.
    """
    try:
        return await store.find_path(
            body.uri_a, body.uri_b, body.max_depth, ontology=body.ontology
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="find_path: Day 6에 구현 예정")


@router.post(
    "/related",
    operation_id="find_related",
    summary="두 클래스 인스턴스 간 관계로 연결된 쌍 찾기 (멀티홉)",
    response_model=list[dict],
)
async def find_related(
    body: FindRelatedRequest,
    store: GraphStore = Depends(get_store),
) -> list[dict]:
    """Find pairs of entities from two classes connected by a predicate.

    The LLM can use tool chaining for simpler cases (find_entities + describe_entity),
    but this tool handles the join in a single query for efficiency.

    Args:
        body.class_uri_a: Subject entity class URI.
        body.predicate: Connecting predicate URI.
        body.class_uri_b: Object entity class URI.
        body.filters_a: Optional filters for subject entities.
        body.filters_b: Optional filters for object entities.
        body.limit: Maximum result pairs.

    Returns:
        List of {entity_a, entity_b} pairs.
    """
    try:
        return await store.find_related(
            body.class_uri_a,
            body.predicate,
            body.class_uri_b,
            body.filters_a,
            body.filters_b,
            body.limit,
            ontology=body.ontology,
        )
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="find_related: Day 6에 구현 예정")
