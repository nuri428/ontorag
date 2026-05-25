from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ontorag.llm.embedding import (
    EmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    get_embedding_provider,
)


def _mock_openai_client(vectors: list[list[float]]) -> AsyncMock:
    """Build a mock AsyncOpenAI whose embeddings.create returns `vectors`."""
    client = AsyncMock()
    client.embeddings.create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])
    )
    return client


@pytest.mark.asyncio
async def test_openai_embed_passes_dimensions_and_returns_vectors():
    with patch("openai.AsyncOpenAI") as ctor:
        client = _mock_openai_client([[0.1, 0.2], [0.3, 0.4]])
        ctor.return_value = client
        provider = OpenAIEmbeddingProvider(api_key="k", model="text-embedding-3-small", dimension=2)

        out = await provider.embed(["a", "b"])

    assert out == [[0.1, 0.2], [0.3, 0.4]]
    _, kwargs = client.embeddings.create.call_args
    assert kwargs["dimensions"] == 2  # v3 models accept the dimensions param
    assert kwargs["input"] == ["a", "b"]


@pytest.mark.asyncio
async def test_ollama_embed_omits_dimensions():
    with patch("openai.AsyncOpenAI") as ctor:
        client = _mock_openai_client([[1.0, 2.0, 3.0]])
        ctor.return_value = client
        provider = OllamaEmbeddingProvider(api_key="ollama", model="nomic-embed-text", dimension=3)

        out = await provider.embed(["x"])

    assert out == [[1.0, 2.0, 3.0]]
    _, kwargs = client.embeddings.create.call_args
    assert "dimensions" not in kwargs  # Ollama rejects the param


@pytest.mark.asyncio
async def test_embed_empty_input_short_circuits():
    with patch("openai.AsyncOpenAI") as ctor:
        client = _mock_openai_client([])
        ctor.return_value = client
        provider = OpenAIEmbeddingProvider(api_key="k", dimension=2)

        assert await provider.embed([]) == []
    client.embeddings.create.assert_not_called()


def test_factory_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with patch("openai.AsyncOpenAI"):
        provider = get_embedding_provider()
    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert isinstance(provider, EmbeddingProvider)  # runtime_checkable protocol


def test_factory_ollama(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    with patch("openai.AsyncOpenAI"):
        provider = get_embedding_provider()
    assert isinstance(provider, OllamaEmbeddingProvider)


def test_factory_unknown_raises(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cohere")
    with pytest.raises(ValueError, match="Unknown EMBEDDING_PROVIDER"):
        get_embedding_provider()
