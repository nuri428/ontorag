"""Tests for FastAPI route handlers in api/routes/tools/learning.py."""
from __future__ import annotations

import unittest.mock as mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ontorag.api import deps
from ontorag.api.routes.tools import learning as learning_mod
from ontorag.learn.base import ExtractedTriple, TermTypingResult
from ontorag.llm.base import _CompletionMessage
from ontorag.stores.base import ClassSummary, PropertySummary, SchemaResult


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_schema() -> SchemaResult:
    return SchemaResult(
        total_classes=1,
        total_properties=1,
        namespaces={"pk": "http://example.org/pokemon#"},
        classes=[
            ClassSummary(
                uri="http://example.org/pokemon#Pokemon",
                label="Pokemon",
                parent_uri=None,
                property_count=1,
                instance_count=5,
            )
        ],
        properties=[
            PropertySummary(
                uri="http://example.org/pokemon#hasType",
                label="has type",
                prop_type="object",
            )
        ],
    )


class _MockStore:
    def __init__(self) -> None:
        self._schema = _make_schema()

    async def get_schema(self) -> SchemaResult:
        return self._schema


class _MockLLM:
    async def complete(self, messages, tools, system=None, force_tool_use=False, force_tool_name=None):
        return _CompletionMessage(content=[], stop_reason="end_turn")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(learning_mod.router)
    app.dependency_overrides[deps.get_store] = lambda: _MockStore()
    return app


# ── type-term endpoint ────────────────────────────────────────────────────────

class TestTypeTermRoute:

    def test_returns_200_with_mocked_result(self):
        async def _fake_type_term(llm, schema, term, context, top_k):
            return [TermTypingResult(
                term=term,
                class_uri="http://example.org/pokemon#Pokemon",
                label="Pokemon",
                confidence=0.95,
            )]

        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()), \
             mock.patch("ontorag.api.routes.tools.learning._term_typing_mod.type_term", side_effect=_fake_type_term):
            client = TestClient(app)
            r = client.post("/tools/learn/type-term", json={"term": "Pikachu"})

        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["term"] == "Pikachu"
        assert body[0]["confidence"] == 0.95

    def test_rejects_empty_term(self):
        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()):
            client = TestClient(app)
            r = client.post("/tools/learn/type-term", json={"term": ""})

        assert r.status_code == 422

    def test_rejects_top_k_zero(self):
        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()):
            client = TestClient(app)
            r = client.post("/tools/learn/type-term", json={"term": "Pikachu", "top_k": 0})

        assert r.status_code == 422

    def test_rejects_top_k_over_max(self):
        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()):
            client = TestClient(app)
            r = client.post("/tools/learn/type-term", json={"term": "Pikachu", "top_k": 11})

        assert r.status_code == 422

    def test_503_when_llm_not_configured(self):
        app = _make_app()
        with mock.patch(
            "ontorag.api.routes.tools.learning.get_llm_provider",
            side_effect=ValueError("no provider"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/tools/learn/type-term", json={"term": "Pikachu"})

        assert r.status_code == 503
        assert "LLM provider not configured" in r.json()["detail"]

    def test_500_does_not_leak_exception_message(self):
        async def _broken_type_term(llm, schema, term, context, top_k):
            raise RuntimeError("internal secret message")

        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()), \
             mock.patch("ontorag.api.routes.tools.learning._term_typing_mod.type_term", side_effect=_broken_type_term):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/tools/learn/type-term", json={"term": "Pikachu"})

        assert r.status_code == 500
        assert "internal secret message" not in r.json()["detail"]
        assert "logs" in r.json()["detail"].lower()


# ── extract-triples endpoint ──────────────────────────────────────────────────

class TestExtractTriplesRoute:

    def test_returns_200_with_mocked_result(self):
        async def _fake_extract(llm, schema, text, entities, min_confidence):
            return [ExtractedTriple(
                subject_label="Pikachu",
                predicate_uri="http://example.org/pokemon#hasType",
                confidence=0.9,
                object_uri="http://example.org/pokemon#Electric",
            )]

        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()), \
             mock.patch("ontorag.api.routes.tools.learning._relation_mod.extract_relations", side_effect=_fake_extract):
            client = TestClient(app)
            r = client.post("/tools/learn/extract-triples", json={"text": "Pikachu is Electric type."})

        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert body[0]["subject_label"] == "Pikachu"

    def test_rejects_empty_text(self):
        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()):
            client = TestClient(app)
            r = client.post("/tools/learn/extract-triples", json={"text": ""})

        assert r.status_code == 422

    def test_rejects_text_over_max_length(self):
        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()):
            client = TestClient(app)
            r = client.post("/tools/learn/extract-triples", json={"text": "x" * 10_001})

        assert r.status_code == 422

    def test_accepts_text_at_max_length(self):
        async def _fake_extract(llm, schema, text, entities, min_confidence):
            return []

        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()), \
             mock.patch("ontorag.api.routes.tools.learning._relation_mod.extract_relations", side_effect=_fake_extract):
            client = TestClient(app)
            r = client.post("/tools/learn/extract-triples", json={"text": "x" * 10_000})

        assert r.status_code == 200

    def test_rejects_min_confidence_above_one(self):
        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()):
            client = TestClient(app)
            r = client.post("/tools/learn/extract-triples", json={"text": "some text", "min_confidence": 1.5})

        assert r.status_code == 422

    def test_503_when_llm_not_configured(self):
        app = _make_app()
        with mock.patch(
            "ontorag.api.routes.tools.learning.get_llm_provider",
            side_effect=ValueError("no api key"),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/tools/learn/extract-triples", json={"text": "some text"})

        assert r.status_code == 503

    def test_500_does_not_leak_exception_message(self):
        async def _broken_extract(llm, schema, text, entities, min_confidence):
            raise RuntimeError("db password in trace")

        app = _make_app()
        with mock.patch("ontorag.api.routes.tools.learning.get_llm_provider", return_value=_MockLLM()), \
             mock.patch("ontorag.api.routes.tools.learning._relation_mod.extract_relations", side_effect=_broken_extract):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/tools/learn/extract-triples", json={"text": "some text"})

        assert r.status_code == 500
        assert "db password in trace" not in r.json()["detail"]
