from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ontorag.api.deps import get_store
from ontorag.stores.base import ClassDetail, GraphStore, SchemaResult

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get(
    "/schema",
    operation_id="get_schema",
    summary="온톨로지 클래스·속성·계층 구조 반환 (LLM 컨텍스트용)",
    response_model=SchemaResult,
)
async def get_schema(store: GraphStore = Depends(get_store)) -> SchemaResult:
    """Return a compact view of ontology classes, properties, and hierarchy.

    Token-efficient: ~30 tokens per class. For full property detail on a
    specific class, call get_class_detail with the class URI.

    Returns:
        SchemaResult with class list, property counts, and namespace prefixes.
    """
    return await store.get_schema()


@router.get(
    "/schema/class",
    operation_id="get_class_detail",
    summary="특정 클래스의 상세 정보 반환 (속성·계층·인스턴스 샘플)",
    response_model=ClassDetail,
)
async def get_class_detail(
    class_uri: str,
    store: GraphStore = Depends(get_store),
) -> ClassDetail:
    """Return full TBox detail for one ontology class.

    Progressive disclosure: call get_schema() first to identify relevant
    classes, then call this endpoint for classes that need full detail.

    Args:
        class_uri: Full URI of the class (e.g. http://xmlns.com/foaf/0.1/Person).

    Returns:
        ClassDetail with all properties, parent/child URIs, and sample instances.

    Raises:
        404: If the class URI is not found in the schema graph.
    """
    try:
        return await store.get_class_detail(class_uri)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"클래스 없음: {class_uri}")
