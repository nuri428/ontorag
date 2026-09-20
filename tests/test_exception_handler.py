"""Test the global exception handlers (v1.0 hardening + AccessDenied -> 403).

An unhandled exception must become a structured 500 ({detail, type}) without
leaking the raw message; HTTPException route guards still pass through
unchanged; AccessDenied (a policy-driven, expected outcome — see
ontorag.stores.access_wrapper) must become a structured 403, not fall
through to the generic 500 handler.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from ontorag.stores.access_wrapper import AccessDenied


def _app_with_handler() -> FastAPI:
    """Rebuild the same handler wiring as api/main.py on a throwaway app."""
    app = FastAPI()

    @app.exception_handler(AccessDenied)
    async def _access_denied_handler(request: Request, exc: AccessDenied) -> JSONResponse:  # noqa: ANN001
        return JSONResponse(status_code=403, content={"detail": str(exc)})

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

    @app.get("/denied")
    async def denied():
        raise AccessDenied("find_entities: read access denied for ontology 'secret'.")

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


def test_access_denied_returns_structured_403():
    client = TestClient(_app_with_handler(), raise_server_exceptions=False)
    r = client.get("/denied")
    assert r.status_code == 403
    assert "secret" in r.json()["detail"]


def test_access_denied_does_not_fall_through_to_generic_500():
    """Regression: without a dedicated AccessDenied handler, a policy denial
    on the default (unscoped) request path surfaced as an opaque 500 —
    indistinguishable from an actual server bug — instead of a clear 403."""
    client = TestClient(_app_with_handler(), raise_server_exceptions=False)
    r = client.get("/denied")
    assert r.status_code != 500


def test_real_app_imports_and_wires_handler():
    """Smoke: the real app registers the Exception and AccessDenied handlers."""
    from ontorag.api.main import app

    assert Exception in app.exception_handlers
    assert AccessDenied in app.exception_handlers


def test_real_route_default_ontology_denied_returns_403_not_500():
    """End-to-end: an ordinary find_entities call (no explicit ontology —
    the default/common request shape) against a policy that denies some
    ontology must come back as a clean 403 through the REAL app + real
    route + real AccessControlledStore wiring, not the generic 500 a
    missing handler would produce. This is the scenario the fail-closed
    union guard (see AccessPolicy.has_read_restricted_ontology) makes the
    default path hit whenever ONTOLOGY_ACCESS has any 'none' entry."""
    from ontorag.api.deps import get_store
    from ontorag.api.main import app
    from ontorag.core.access import AccessPolicy
    from ontorag.stores.access_wrapper import AccessControlledStore

    class _StubStore:
        async def find_entities(self, class_uri, filters=None, limit=100, ontology=None):
            return []

        async def aclose(self):
            pass

    policy = AccessPolicy.from_string("secret:none,public:rw")
    wrapped = AccessControlledStore(_StubStore(), policy)
    app.dependency_overrides[get_store] = lambda: wrapped
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/tools/entities/find", json={"class_uri": "ex:Foo"})
        assert resp.status_code == 403
        assert "union read" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_store, None)
