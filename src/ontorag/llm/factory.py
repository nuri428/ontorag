from __future__ import annotations

import os
from typing import Union

from ontorag.llm.anthropic import AnthropicProvider
from ontorag.llm.ollama import OllamaProvider
from ontorag.llm.openai import OpenAIProvider

LLMProvider = Union[AnthropicProvider, OpenAIProvider, OllamaProvider]


def get_llm_provider() -> LLMProvider:
    """Create an LLM provider from environment variables.

    Reads LLM_PROVIDER (anthropic | openai | ollama) and the corresponding
    credentials. Raises ValueError if required env vars are missing.

    Returns:
        Configured LLM provider instance.
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()

    if provider == "anthropic":
        return AnthropicProvider.from_env()
    if provider == "openai":
        return OpenAIProvider.from_env()
    if provider == "ollama":
        return OllamaProvider.from_env()

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r}. Valid values: anthropic, openai, ollama"
    )
