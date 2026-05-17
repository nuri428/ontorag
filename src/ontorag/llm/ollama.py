from __future__ import annotations

import os

from ontorag.llm.openai import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    """Ollama adapter using its OpenAI-compatible API endpoint.

    Ollama exposes /v1/chat/completions, so the OpenAI SDK works as-is
    with a custom base_url. No separate dependency required.
    """

    @classmethod
    def from_env(cls) -> OllamaProvider:
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        model = os.environ.get("LLM_MODEL", "llama3.1")
        return cls(
            api_key="ollama",  # Ollama ignores the key but the SDK requires a value
            model=model,
            base_url=f"{base_url.rstrip('/')}/v1",
        )
