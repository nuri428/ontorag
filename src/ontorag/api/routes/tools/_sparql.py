from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ontorag.api.deps import get_store
from ontorag.stores.base import QueryResult
from ontorag.stores.fuseki import FusekiStore

router = APIRouter(prefix="/tools", tags=["debug"])


class RawSparqlRequest(BaseModel):
    """Raw SPARQL query — debug/admin use only."""

    sparql: str


@router.post(
    "/query/sparql",
    operation_id="query_sparql_raw",
    summary="[DEBUG] Raw SPARQL 실행 — LLM에 비노출, 개발자 전용",
    response_model=QueryResult,
    include_in_schema=True,
)
async def query_sparql_raw(
    body: RawSparqlRequest,
    store: FusekiStore = Depends(get_store),
) -> QueryResult:
    """Execute a raw SPARQL SELECT query directly against the store.

    This endpoint is intentionally excluded from MCP tool exposure.
    Use only for debugging, development, or admin purposes.
    Do NOT expose this endpoint to untrusted callers.

    Args:
        body.sparql: Raw SPARQL SELECT query string.

    Returns:
        Query results as column names and rows.
    """
    result = await store._sparql_select(body.sparql)
    bindings = result.get("results", {}).get("bindings", [])
    if not bindings:
        return QueryResult(columns=[], rows=[], total=0)

    columns = list(bindings[0].keys())
    rows = [
        {col: b[col]["value"] for col in columns if col in b}
        for b in bindings
    ]
    return QueryResult(columns=columns, rows=rows, total=len(rows))
