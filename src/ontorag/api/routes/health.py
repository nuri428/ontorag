from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ontorag import __version__

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """API liveness response."""

    status: str
    version: str


@router.get(
    "/health",
    operation_id="health_check",
    summary="API 상태 확인",
    response_model=HealthResponse,
)
async def health_check() -> HealthResponse:
    """Return API liveness status.

    Returns:
        status: "ok" when the API is running.
        version: Current ontorag version.
    """
    return HealthResponse(status="ok", version=__version__)
