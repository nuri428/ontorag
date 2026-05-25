from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ontorag.api.deps import get_store
from ontorag.stores.base import GraphStore, SearchHit

router = APIRouter(prefix="/tools", tags=["tools"])


class SearchTextRequest(BaseModel):
    """Request body for BM25 full-text search."""

    query: str = Field(
        description=(
            "Lucene query string — e.g. 'Pikachu', 'pika*', "
            "'Pikachu OR Raichu'. Passed as a bound parameter; never interpolated."
        )
    )
    class_uri: str | None = Field(
        default=None,
        description=(
            "Optional class URI to restrict results to instances of that class "
            "or any of its subclasses (rdfs:subClassOf inference included)."
        ),
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Maximum number of results to return (1–200, default 20).",
    )
    ontology: str | None = Field(
        default=None,
        description="Optional ontology id to scope results to; None = all (union).",
    )


@router.post(
    "/search/text",
    operation_id="search_text",
    summary="BM25 전문 검색 — 온톨로지 인스턴스에 대한 키워드/Lucene 검색 (Fuseki + Neo4j)",
    response_model=list[SearchHit],
)
async def search_text(
    body: SearchTextRequest,
    store: GraphStore = Depends(get_store),
) -> list[SearchHit]:
    """Search ontology instance data using BM25 full-text (Lucene) scoring.

    Available on both backends:
      - ``GRAPH_STORE=fuseki``: jena-text (Lucene) index over indexed predicates
        (``rdfs:label``, ``rdfs:comment``, ``skos:prefLabel``, ``skos:definition``).
      - ``GRAPH_STORE=neo4j``: Neo4j full-text index over all string-valued properties.

    Returns 501 when the active backend does not expose ``search_text``.
    Results are ordered by Lucene relevance score (higher = more relevant).

    Args:
        body.query: Lucene query string (e.g. "Pikachu", "pika*", "피카츄").
        body.class_uri: Optional class URI to restrict results to that class
            and its subclasses (rdfs:subClassOf inference included).
        body.limit: Maximum number of results (1–200, default 20).

    Returns:
        List of SearchHit ordered by score descending.

    Raises:
        HTTPException: 501 if the active backend does not support full-text search.
    """
    # Capability guard: dispatch only when the backend exposes search_text.
    fn = getattr(store, "search_text", None)
    if fn is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Full-text search is not supported by the active graph store "
                f"({type(store).__name__}). "
                "This endpoint requires a backend with full-text search "
                "(GRAPH_STORE=fuseki or GRAPH_STORE=neo4j)."
            ),
        )

    return await fn(body.query, body.class_uri, body.limit, ontology=body.ontology)
