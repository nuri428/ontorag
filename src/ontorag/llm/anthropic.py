from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

logger = logging.getLogger(__name__)


class AnthropicProvider:
    """Thin async wrapper around the Anthropic Messages API.

    Handles one round-trip per call. The agent loop in chat/agent.py
    drives multi-turn tool-use iteration.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 4096,
    ) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        self._client = anthropic.AsyncAnthropic(api_key=key)
        self._model = model
        self._max_tokens = max_tokens

    @classmethod
    def from_env(cls) -> AnthropicProvider:
        """Create from environment variables: ANTHROPIC_API_KEY, LLM_MODEL."""
        return cls(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
    ) -> anthropic.types.Message:
        """Send one API request and return the raw Message.

        Args:
            messages: Conversation history in Anthropic format.
            tools: Tool definitions in Anthropic format.
            system: System prompt (optional).

        Returns:
            Anthropic Message object (may contain text and/or tool_use blocks).
        """
        kwargs: dict[str, Any] = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=messages,
            tools=tools,
        )
        if system:
            kwargs["system"] = system
        logger.debug("LLM request: model=%s turns=%d", self._model, len(messages))
        return await self._client.messages.create(**kwargs)
