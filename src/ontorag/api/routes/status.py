from __future__ import annotations

from fastapi import APIRouter, Depends

from ontorag.api.deps import get_store
from ontorag.stores.base import StoreStatus
from ontorag.stores.fuseki import FusekiStore

router = APIRouter(tags=["system"])


@router.get(
    "/status",
    operation_id="get_status",
    summary="그래프 스토어 연결 및 데이터 로드 상태 확인",
    response_model=StoreStatus,
)
async def get_status(store: FusekiStore = Depends(get_store)) -> StoreStatus:
    """Return graph store connection status and triple counts.

    Returns:
        connected: Whether Fuseki is reachable.
        triple_count: Total triples across schema and data graphs.
        schema_loaded: True if TBox graph has triples.
        data_loaded: True if ABox graph has triples.
    """
    return await store.status()
