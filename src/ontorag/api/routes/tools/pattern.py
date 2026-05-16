from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ontorag.api.deps import get_store
from ontorag.stores.base import PatternQuery, QueryResult
from ontorag.stores.fuseki import FusekiStore

router = APIRouter(prefix="/tools", tags=["tools"])


@router.post(
    "/query/pattern",
    operation_id="query_pattern",
    summary="JSON DSL 쿼리 실행 (SPARQL 직접 노출 없이 구조화된 쿼리)",
    response_model=QueryResult,
)
async def query_pattern(
    query: PatternQuery,
    store: FusekiStore = Depends(get_store),
) -> QueryResult:
    """Execute a structured JSON DSL query translated to SPARQL internally.

    Use this when Layer 1 tools (find_entities, traverse, etc.) cannot express
    the required query. SPARQL injection is not possible — all input is
    structurally validated before translation.

    Example query::

        {
          "select": ["?person", "?paper"],
          "where": [
            {"s": "?person", "p": "rdf:type", "o": "ex:Researcher"},
            {"s": "?person", "p": "ex:authored", "o": "?paper"}
          ],
          "filters": [{"var": "?paper", "op": "!=", "value": ""}],
          "limit": 50
        }

    Args:
        query: PatternQuery DSL object. Input is validated; malformed terms
            are rejected before reaching the store.

    Returns:
        Query results as column names and rows.
    """
    try:
        return await store.query_pattern(query)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="query_pattern: Day 4에 구현 예정")
