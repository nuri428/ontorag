from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ontorag.api.deps import get_store
from ontorag.stores.base import GraphStore, QueryResult

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
    store: GraphStore = Depends(get_store),
) -> QueryResult:
    """Execute a raw SPARQL SELECT query directly against the store.

    Raw SPARQL is a SPARQL-store capability, not part of the GraphStore
    protocol — backends that do not speak SPARQL (e.g. Neo4j) return 501.
    This endpoint is intentionally excluded from MCP tool exposure: use it
    only for debugging, development, or admin purposes, never for untrusted
    callers.

    Args:
        body.sparql: Raw SPARQL SELECT query string.

    Returns:
        Query results as column names and rows.

    Raises:
        HTTPException: 501 if the active backend does not support raw SPARQL.
    """
    # Capability check: only SPARQL-backed stores (FusekiStore) expose this.
    sparql_select = getattr(store, "_sparql_select", None)
    if sparql_select is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Raw SPARQL is not supported by the active graph store "
                f"({type(store).__name__}). This debug endpoint requires a "
                "SPARQL backend (GRAPH_STORE=fuseki)."
            ),
        )

    result = await sparql_select(body.sparql)
    bindings = result.get("results", {}).get("bindings", [])
    if not bindings:
        return QueryResult(columns=[], rows=[], total=0)

    columns = list(bindings[0].keys())
    rows = [{col: b[col]["value"] for col in columns if col in b} for b in bindings]
    return QueryResult(columns=columns, rows=rows, total=len(rows))
