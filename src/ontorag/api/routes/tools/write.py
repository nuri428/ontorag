"""ABox write tools — assert_triple / retract_triple / assert_triples.

Exposed via MCP (fastapi-mcp auto-converts these POST endpoints to MCP tools).
Used by ontorag-flow's AssertTriple / RetractTriple actions and
ontorag-memory's MemoryClient.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ontorag.api.deps import get_store
from ontorag.stores.base import GraphStore

router = APIRouter(prefix="/tools", tags=["tools"])


class AssertTripleRequest(BaseModel):
    subject: str = Field(description="Subject URI.")
    predicate: str = Field(description="Predicate URI.")
    object: str = Field(description="Object — URI or plain string literal.")
    object_is_uri: bool = Field(
        default=False,
        description="True when object is a URI reference; False for string literal.",
    )
    ontology: str | None = Field(
        default=None,
        description="Ontology/graph id to write into (None = default ABox).",
    )


class RetractTripleRequest(BaseModel):
    subject: str
    predicate: str
    object: str
    object_is_uri: bool = False
    ontology: str | None = None


class Triple(BaseModel):
    subject: str
    predicate: str
    object: str
    object_is_uri: bool = False


class AssertTriplesRequest(BaseModel):
    triples: list[Triple] = Field(description="Triples to insert.")
    ontology: str | None = None


class WriteResult(BaseModel):
    status: str
    triple: dict | None = None
    count: int | None = None


@router.post(
    "/write/assert",
    operation_id="assert_triple",
    summary="Insert a single triple into the ABox.",
    response_model=WriteResult,
)
async def assert_triple_route(
    body: AssertTripleRequest,
    store: GraphStore = Depends(get_store),
) -> WriteResult:
    await store.assert_triple(
        body.subject,
        body.predicate,
        body.object,
        object_is_uri=body.object_is_uri,
        ontology=body.ontology,
    )
    return WriteResult(
        status="asserted",
        triple={"s": body.subject, "p": body.predicate, "o": body.object},
    )


@router.post(
    "/write/retract",
    operation_id="retract_triple",
    summary="Remove a single triple from the ABox.",
    response_model=WriteResult,
)
async def retract_triple_route(
    body: RetractTripleRequest,
    store: GraphStore = Depends(get_store),
) -> WriteResult:
    await store.retract_triple(
        body.subject,
        body.predicate,
        body.object,
        object_is_uri=body.object_is_uri,
        ontology=body.ontology,
    )
    return WriteResult(
        status="retracted",
        triple={"s": body.subject, "p": body.predicate, "o": body.object},
    )


@router.post(
    "/write/assert-many",
    operation_id="assert_triples",
    summary="Insert multiple triples in one batch.",
    response_model=WriteResult,
)
async def assert_triples_route(
    body: AssertTriplesRequest,
    store: GraphStore = Depends(get_store),
) -> WriteResult:
    triples = [
        (t.subject, t.predicate, t.object, t.object_is_uri)
        for t in body.triples
    ]
    count = await store.assert_triples(triples, ontology=body.ontology)
    return WriteResult(status="asserted", count=count)
