"""Tests for ontorag.eval.baselines — Protocol + LangChain adapter.

LangChain tests run without an OpenAI key by exercising only the
pure-function chunking helper (`render_graph_as_chunks`) and dependency
import handling. Live API calls are skipped when ``OPENAI_API_KEY`` is
absent.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ontorag.eval.baselines import (
    BaselineAnswer,
    BaselineError,
    MissingBaselineDependencyError,
    RAGBaseline,
)
from ontorag.eval.baselines.langchain_vector import (
    LangChainVectorBaseline,
    render_graph_as_chunks,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PURE_LAND = REPO_ROOT / "examples" / "pure_land"
COMMERCE = REPO_ROOT / "examples" / "commerce"


# ── BaselineAnswer dataclass ──────────────────────────────────────────────────


class TestBaselineAnswer:
    def test_minimal_construction(self):
        a = BaselineAnswer(text="Hi")
        assert a.text == "Hi"
        assert a.cited_triples == []
        assert a.tool_calls == 0
        assert a.latency_ms == 0.0
        assert a.extra == {}

    def test_full_construction(self):
        a = BaselineAnswer(
            text="answer",
            cited_triples=[("s", "p", "o")],
            tool_calls=3,
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=123.4,
            extra={"k": "v"},
        )
        assert a.tool_calls == 3
        assert a.extra["k"] == "v"


# ── RAGBaseline Protocol ──────────────────────────────────────────────────────


class TestRAGBaselineProtocol:
    def test_protocol_runtime_checkable(self):
        """A duck-typed object with the right attrs should satisfy the Protocol."""

        class Fake:
            name = "fake"
            version = "0"

            async def answer(self, q):
                return BaselineAnswer(text=q)

            async def close(self):
                return None

        assert isinstance(Fake(), RAGBaseline)

    def test_protocol_rejects_missing_attrs(self):
        class Incomplete:
            name = "x"
            # Missing version, answer, close

        assert not isinstance(Incomplete(), RAGBaseline)


# ── render_graph_as_chunks (pure function) ────────────────────────────────────


class TestRenderGraphAsChunks:
    def test_commerce_chunks_are_nonempty(self):
        chunks = render_graph_as_chunks(
            COMMERCE / "schema.ttl", COMMERCE / "data.ttl"
        )
        assert len(chunks) > 0
        # Should contain at least one of the fictional company names
        joined = " ".join(chunks)
        assert "Aurora Tech" in joined or "오로라 테크" in joined

    def test_commerce_chunk_count_matches_abox_size(self):
        chunks = render_graph_as_chunks(
            COMMERCE / "schema.ttl", COMMERCE / "data.ttl"
        )
        # Roughly: 4 currencies + 5 orgs + 3 brands + 7 people + 6 products
        # + 6 offers = ~31 entities
        assert 25 <= len(chunks) <= 40

    def test_pure_land_chunks_are_nonempty(self):
        chunks = render_graph_as_chunks(
            PURE_LAND / "schema.ttl", PURE_LAND / "data.ttl"
        )
        assert len(chunks) > 0

    def test_pure_land_includes_amitabha(self):
        chunks = render_graph_as_chunks(
            PURE_LAND / "schema.ttl", PURE_LAND / "data.ttl"
        )
        # Amitābha should appear by label in at least one chunk
        joined = " ".join(chunks)
        assert "Amitābha" in joined or "아미타불" in joined

    def test_chunks_are_strings(self):
        chunks = render_graph_as_chunks(
            COMMERCE / "schema.ttl", COMMERCE / "data.ttl"
        )
        assert all(isinstance(c, str) for c in chunks)
        assert all(c.strip() for c in chunks)


# ── LangChainVectorBaseline construction ──────────────────────────────────────


class TestLangChainVectorBaseline:
    def test_construction_without_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(BaselineError, match="OPENAI_API_KEY"):
            LangChainVectorBaseline(
                COMMERCE / "schema.ttl", COMMERCE / "data.ttl"
            )

    def test_missing_dependency_raises_clear_error(self, monkeypatch):
        """If LangChain is not installed, MissingBaselineDependencyError
        should be raised with an actionable message."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        # Patch the import to simulate missing dependency
        with patch(
            "ontorag.eval.baselines.langchain_vector._require_deps",
            side_effect=MissingBaselineDependencyError(
                "Install with: uv sync --extra bench"
            ),
        ):
            with pytest.raises(
                MissingBaselineDependencyError, match="bench"
            ):
                LangChainVectorBaseline(
                    COMMERCE / "schema.ttl", COMMERCE / "data.ttl"
                )

    def test_baseline_has_protocol_attrs(self):
        # Even without full instantiation, class-level attrs should exist
        assert LangChainVectorBaseline.name == "langchain_vector"
        assert LangChainVectorBaseline.version == "0.1.0"

    @pytest.mark.integration
    def test_live_answer_on_commerce(self):
        """End-to-end smoke test — requires explicit opt-in via RUN_LIVE_LLM_TESTS=1.

        Guarded by two conditions so a default ``pytest`` run never fires a
        billable, non-deterministic live LLM call:

        1. ``RUN_LIVE_LLM_TESTS=1`` must be set (explicit opt-in).
        2. ``OPENAI_API_KEY`` must be present (credential available).

        Use ``pytest -m integration`` together with ``RUN_LIVE_LLM_TESTS=1``
        to run this test deliberately.
        """
        if not os.environ.get("RUN_LIVE_LLM_TESTS"):
            pytest.skip("set RUN_LIVE_LLM_TESTS=1 to run live LLM tests")
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set")
        try:
            import langchain  # noqa: F401, PLC0415
        except ImportError:
            pytest.skip("langchain not installed (uv sync --extra bench)")

        baseline = LangChainVectorBaseline(
            COMMERCE / "schema.ttl", COMMERCE / "data.ttl"
        )
        import asyncio

        result = asyncio.run(baseline.answer("Who is the CEO of Aurora Tech?"))
        assert isinstance(result, BaselineAnswer)
        assert result.text
        assert result.latency_ms > 0
        assert result.cited_triples == []  # vector RAG never cites triples
        assert "retrieved_chunks" in result.extra

    def test_construction_calls_chroma_when_deps_mocked(self, monkeypatch):
        """With deps mocked, ensure construction wires through to Chroma."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        fake_vectorstore = MagicMock()
        fake_chroma_cls = MagicMock(from_texts=MagicMock(return_value=fake_vectorstore))
        fake_qa = MagicMock()
        fake_deps = {
            "RetrievalQA": MagicMock(from_chain_type=MagicMock(return_value=fake_qa)),
            "Chroma": fake_chroma_cls,
            "ChatOpenAI": MagicMock(),
            "OpenAIEmbeddings": MagicMock(),
        }

        with patch(
            "ontorag.eval.baselines.langchain_vector._require_deps",
            return_value=fake_deps,
        ):
            baseline = LangChainVectorBaseline(
                COMMERCE / "schema.ttl", COMMERCE / "data.ttl"
            )
            assert baseline.name == "langchain_vector"
            assert fake_chroma_cls.from_texts.called
            # Chunks should have been passed in
            call_kwargs = fake_chroma_cls.from_texts.call_args.kwargs
            assert "texts" in call_kwargs
            assert len(call_kwargs["texts"]) > 0
