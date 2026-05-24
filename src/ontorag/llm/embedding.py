from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Text embedding backend for the textual graph-embedding track.

    Mirrors the LLM provider layer: a thin async interface with an env-driven
    factory (`get_embedding_provider`). Implementations must expose their
    output `dimension` so the store can create a matching vector index before
    any vectors are written.
    """

    model: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per input, in order."""
        ...


class OpenAIEmbeddingProvider:
    """OpenAI embeddings (`text-embedding-3-*`).

    The `dimensions` request parameter (supported by the v3 models) lets us
    pin a smaller dimension than the model default — handy for keeping vector
    indexes compact.
    """

    _supports_dimensions = True

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        dimension: int = 1536,
        base_url: str | None = None,
    ) -> None:
        from openai import AsyncOpenAI  # noqa: PLC0415 — optional dep, import lazily

        if dimension <= 0:
            raise ValueError(
                f"Embedding dimension must be a positive integer, got {dimension}. "
                "Check EMBEDDING_DIMENSION."
            )
        self.model = model
        self.dimension = dimension
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict[str, object] = {"model": self.model, "input": texts}
        if self._supports_dimensions:
            kwargs["dimensions"] = self.dimension
        response = await self._client.embeddings.create(**kwargs)  # type: ignore[arg-type]
        return [item.embedding for item in response.data]

    @classmethod
    def from_env(cls) -> OpenAIEmbeddingProvider:
        return cls(
            api_key=os.environ["OPENAI_API_KEY"],
            model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
            dimension=int(os.environ.get("EMBEDDING_DIMENSION", "1536")),
        )


class OllamaEmbeddingProvider(OpenAIEmbeddingProvider):
    """Ollama embeddings via its OpenAI-compatible `/v1/embeddings` endpoint.

    Ollama does not accept the `dimensions` parameter, so the model's native
    output size is used as-is (e.g. nomic-embed-text → 768).
    """

    _supports_dimensions = False

    @classmethod
    def from_env(cls) -> OllamaEmbeddingProvider:
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return cls(
            api_key="ollama",  # ignored by Ollama, required by the SDK
            model=os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
            dimension=int(os.environ.get("EMBEDDING_DIMENSION", "768")),
            base_url=f"{base_url.rstrip('/')}/v1",
        )


def get_embedding_provider() -> EmbeddingProvider:
    """Build an embedding provider from EMBEDDING_PROVIDER (openai | ollama).

    Mirrors `ontorag.api.deps.get_llm_provider`. Default is openai.

    Raises:
        ValueError: if EMBEDDING_PROVIDER is unknown.
    """
    provider = os.environ.get("EMBEDDING_PROVIDER", "openai").strip().lower()
    if provider == "openai":
        return OpenAIEmbeddingProvider.from_env()
    if provider == "ollama":
        return OllamaEmbeddingProvider.from_env()
    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER: {provider!r}. Valid values: openai, ollama."
    )
