"""GET /dump — TBox/ABox graph export endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ontorag.api.deps import get_store
from ontorag.stores.fuseki import FusekiStore

router = APIRouter(tags=["dump"])

_MIME: dict[str, str] = {
    "ttl": "text/turtle",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_EXT: dict[str, str] = {
    "ttl": "ttl",
    "json": "json",
    "jsonl": "jsonl",
    "xlsx": "xlsx",
}


@router.get(
    "/dump",
    summary="TBox/ABox 그래프 덤프 다운로드",
    response_class=Response,
    responses={
        200: {
            "description": "직렬화된 그래프 바이트",
            "content": {
                "text/turtle": {},
                "application/json": {},
                "application/x-ndjson": {},
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            },
        }
    },
)
async def dump_graph(
    target: Literal["schema", "data", "all"] = Query(
        "schema",
        description="덤프 대상: schema(TBox) | data(ABox) | all(전체)",
    ),
    format: Literal["ttl", "json", "jsonl", "xlsx"] = Query(  # noqa: A002
        "ttl",
        description="출력 포맷: ttl | json | jsonl | xlsx",
    ),
    store: FusekiStore = Depends(get_store),
) -> Response:
    """Export one or both named graphs as a downloadable file.

    - **target**: `schema` (TBox), `data` (ABox), or `all` (both)
    - **format**: `ttl` (Turtle), `json` (triple array), `jsonl` (one triple/line), `xlsx`
    """
    data = await store.dump_graph(target, format)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ontorag_{target}_{ts}.{_EXT[format]}"
    return Response(
        content=data,
        media_type=_MIME[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
