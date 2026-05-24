from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ontorag.api.deps import get_store
from ontorag.stores.base import (
    AggFunc,
    AggregateResult,
    EntityFilter,
    EntityResult,
    GraphStore,
)
from pydantic import BaseModel

router = APIRouter(prefix="/tools", tags=["tools"])


class FindEntitiesRequest(BaseModel):
    """Request body for finding entities."""

    class_uri: str
    filters: list[EntityFilter] | None = None
    limit: int = 100


class CountEntitiesRequest(BaseModel):
    """Request body for counting entities."""

    class_uri: str
    filters: list[EntityFilter] | None = None


class AggregateRequest(BaseModel):
    """Request body for aggregation."""

    class_uri: str
    group_by: str
    agg: AggFunc = AggFunc.count


@router.post(
    "/entities/find",
    operation_id="find_entities",
    summary="클래스 + 필터로 인스턴스 찾기 (inference 포함)",
    response_model=list[EntityResult],
)
async def find_entities(
    body: FindEntitiesRequest,
    store: GraphStore = Depends(get_store),
) -> list[EntityResult]:
    """Find instances of an ontology class matching optional filter conditions.

    Inference-aware: includes subclass instances when RDFS inference is enabled.

    Args:
        body.class_uri: Full URI or prefixed name of the class (e.g. foaf:Person).
        body.filters: Optional list of property-value conditions.
        body.limit: Maximum number of results (default 100).

    Returns:
        List of matching entities with their properties.
    """
    try:
        return await store.find_entities(body.class_uri, body.filters, body.limit)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="find_entities: Day 5에 구현 예정")


@router.get(
    "/entities/{uri:path}",
    operation_id="describe_entity",
    summary="특정 엔티티의 속성과 관계 반환 (inference 포함)",
    response_model=EntityResult,
)
async def describe_entity(
    uri: Annotated[str, Path(description="엔티티 URI")],
    predicates: Annotated[
        list[str] | None,
        Query(description="반환할 predicate URI 목록. 미지정 시 전체 반환."),
    ] = None,
    store: GraphStore = Depends(get_store),
) -> EntityResult:
    """Return all properties and relationships of an entity.

    Includes owl:inverseOf relationships when inference is enabled.

    Args:
        uri: Full URI of the entity.
        predicates: Optional predicate URIs to restrict the output.

    Returns:
        Entity with all known (and inferred) properties.
    """
    try:
        return await store.describe_entity(uri, predicates)
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="describe_entity: Day 5에 구현 예정"
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Entity not found: {uri}")


@router.post(
    "/entities/count",
    operation_id="count_entities",
    summary="클래스 인스턴스 수 집계",
    response_model=int,
)
async def count_entities(
    body: CountEntitiesRequest,
    store: GraphStore = Depends(get_store),
) -> int:
    """Count instances of a class matching optional filters.

    Args:
        body.class_uri: Class URI or prefixed name.
        body.filters: Optional filter conditions.

    Returns:
        Number of matching instances.
    """
    try:
        return await store.count_entities(body.class_uri, body.filters)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="count_entities: Day 5에 구현 예정")


@router.post(
    "/entities/aggregate",
    operation_id="aggregate",
    summary="클래스 인스턴스를 속성으로 그룹화하여 집계",
    response_model=list[AggregateResult],
)
async def aggregate(
    body: AggregateRequest,
    store: GraphStore = Depends(get_store),
) -> list[AggregateResult]:
    """Group instances by a property and apply an aggregation function.

    Args:
        body.class_uri: Class to aggregate over.
        body.group_by: Property URI or prefixed name to group by.
        body.agg: Aggregation function (count, sum, avg, min, max).

    Returns:
        List of group_value → aggregated_result pairs.
    """
    try:
        return await store.aggregate(body.class_uri, body.group_by, body.agg)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="aggregate: Day 5에 구현 예정")
