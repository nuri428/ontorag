"""Test the global exception handler (v1.0 hardening).

An unhandled exception must become a structured 500 ({detail, type}) without
leaking the raw message; HTTPException route guards still pass through unchanged.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


def _app_with_handler() -> FastAPI:
    """Rebuild the same handler wiring as api/main.py on a throwaway app."""
    app = FastAPI()

    @app.exception_handler(Exception)
    async def _h(request: Request, exc: Exception) -> JSONResponse:  # noqa: ANN001
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error.", "type": exc.__class__.__name__},
        )

    @app.get("/boom")
    async def boom():
        raise RuntimeError("super secret internal detail: db password leaked")

    @app.get("/notfound")
    async def notfound():
        raise HTTPException(status_code=404, detail="nope")

    return app


def test_unhandled_exception_returns_structured_500():
    client = TestClient(_app_with_handler(), raise_server_exceptions=False)
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["detail"] == "Internal server error."
    assert body["type"] == "RuntimeError"
    # raw message must NOT leak to the client
    assert "secret" not in r.text
    assert "password" not in r.text


def test_http_exception_still_passes_through():
    client = TestClient(_app_with_handler(), raise_server_exceptions=False)
    r = client.get("/notfound")
    assert r.status_code == 404
    assert r.json()["detail"] == "nope"


def test_real_app_imports_and_wires_handler():
    """Smoke: the real app registers the Exception handler."""
    from ontorag.api.main import app

    assert Exception in app.exception_handlers
