from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

from ontorag.llm.base import _CompletionMessage, _TextBlock, _ToolUseBlock

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
        self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=0)
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
        force_tool_use: bool = False,
        force_tool_name: str | None = None,
    ) -> _CompletionMessage:
        """Send one API request and return a normalized _CompletionMessage.

        Args:
            messages: Conversation history in Anthropic format.
            tools: Tool definitions in Anthropic format.
            system: System prompt (optional).
            force_tool_use: If True, pass tool_choice=any so the LLM must call a tool.
            force_tool_name: If set, force exactly this named tool (tool_choice=tool).

        Returns:
            Provider-agnostic _CompletionMessage (text and/or tool_use blocks).
        """
        kwargs: dict[str, Any] = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=messages,
            tools=tools,
        )
        if system:
            kwargs["system"] = system
        if force_tool_name and tools:
            kwargs["tool_choice"] = {"type": "tool", "name": force_tool_name}
        elif force_tool_use and tools:
            kwargs["tool_choice"] = {"type": "any"}
        logger.debug("LLM request: model=%s turns=%d", self._model, len(messages))
        raw = await self._client.messages.create(**kwargs)

        content: list[_TextBlock | _ToolUseBlock] = []
        for block in raw.content:
            if block.type == "text":
                content.append(_TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(_ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=dict(block.input) if block.input else {},
                ))
        return _CompletionMessage(
            content=content,
            stop_reason=raw.stop_reason or "end_turn",
        )
