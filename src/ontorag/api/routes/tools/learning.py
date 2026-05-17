from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ontorag.api.deps import get_store
from ontorag.learn.base import ExtractedTriple, TermTypingResult
from ontorag.learn import term_typing as _term_typing_mod
from ontorag.learn import relation as _relation_mod
from ontorag.llm.factory import get_llm_provider
from ontorag.stores.fuseki import FusekiStore

router = APIRouter(prefix="/tools/learn", tags=["learning"])


class TypeTermRequest(BaseModel):
    """Request body for Task A: term → TBox class."""

    term: str = Field(min_length=1, description="Text mention to classify against the TBox.")
    context: str | None = Field(
        default=None,
        description="Optional surrounding text for disambiguation (max 500 chars used).",
    )
    top_k: int = Field(default=3, ge=1, le=10, description="Number of ranked results.")


class ExtractTriplesRequest(BaseModel):
    """Request body for Task C: text → RDF triples."""

    text: str = Field(min_length=1, description="Source text to extract triples from.")
    entities: list[str] | None = Field(
        default=None,
        description="Optional entity label whitelist to focus extraction.",
    )
    min_confidence: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Minimum confidence threshold."
    )


@router.post(
    "/type-term",
    operation_id="type_term",
    summary="텍스트 언급 → TBox 클래스 매핑 (Task A)",
    response_model=list[TermTypingResult],
)
async def type_term(
    body: TypeTermRequest,
    store: FusekiStore = Depends(get_store),
) -> list[TermTypingResult]:
    """Map a text mention to ranked TBox classes (LLMs4OL Task A).

    Args:
        body.term: Text mention to classify (e.g. "Pikachu").
        body.context: Optional context for disambiguation.
        body.top_k: Number of ranked class assignments to return.

    Returns:
        Ranked list of {term, class_uri, label, confidence, reasoning}.
    """
    try:
        llm = get_llm_provider()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"LLM provider not configured: {exc}")

    try:
        schema = await store.get_schema()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not load schema: {exc}")

    try:
        return await _term_typing_mod.type_term(
            llm, schema, body.term, body.context, body.top_k
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/extract-triples",
    operation_id="extract_triples",
    summary="텍스트 → RDF 트리플 추출 (Task C)",
    response_model=list[ExtractedTriple],
)
async def extract_triples(
    body: ExtractTriplesRequest,
    store: FusekiStore = Depends(get_store),
) -> list[ExtractedTriple]:
    """Extract RDF triples from text using TBox-validated predicates (LLMs4OL Task C).

    Args:
        body.text: Source text to extract triples from.
        body.entities: Optional entity label whitelist.
        body.min_confidence: Minimum confidence to include a triple.

    Returns:
        List of {subject_label, subject_uri, predicate_uri, object_uri, object_value, confidence}.
    """
    try:
        llm = get_llm_provider()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=f"LLM provider not configured: {exc}")

    try:
        schema = await store.get_schema()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not load schema: {exc}")

    try:
        return await _relation_mod.extract_relations(
            llm, schema, body.text, body.entities, body.min_confidence
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
