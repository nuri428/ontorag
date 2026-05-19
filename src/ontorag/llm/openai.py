from __future__ import annotations

import json
import logging
import os
from typing import Any

from ontorag.llm.base import _CompletionMessage, openai_response_to_message

logger = logging.getLogger(__name__)


def _anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic tool format to OpenAI tool format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get(
                    "input_schema", {"type": "object", "properties": {}}
                ),
            },
        }
        for t in tools
    ]


def _anthropic_messages_to_openai(
    messages: list[dict[str, Any]],
    system: str | None,
) -> list[dict[str, Any]]:
    """Convert Anthropic-format message history to OpenAI format."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for m in messages:
        role = m["role"]
        content = m["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # list of blocks (assistant turn or tool results)
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        }
                    )
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": " ".join(text_parts) or None,
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)

        elif role == "user":
            for block in content:
                if block.get("type") == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block.get("content", ""),
                        }
                    )
                else:
                    out.append({"role": "user", "content": block.get("text", "")})

    return out


class OpenAIProvider:
    """OpenAI chat completions adapter.

    Returns duck-typed _CompletionMessage so AgentLoop works unchanged.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        base_url: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "openai 패키지가 설치되지 않았습니다. `pip install openai` 를 실행하세요."
            ) from exc

        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key and not base_url:
            raise ValueError("OPENAI_API_KEY 또는 base_url이 필요합니다")

        kwargs: dict[str, Any] = {"api_key": key or "ollama", "max_retries": 0}
        if base_url:
            kwargs["base_url"] = base_url

        self._client = AsyncOpenAI(**kwargs)
        self._model = model
        self._max_tokens = max_tokens

    @classmethod
    def from_env(cls) -> OpenAIProvider:
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY"),
            model=os.environ.get("LLM_MODEL", "gpt-4o"),
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        force_tool_use: bool = False,
        force_tool_name: str | None = None,
    ) -> _CompletionMessage:
        """Call OpenAI API and return Anthropic-compatible _CompletionMessage."""
        openai_messages = _anthropic_messages_to_openai(messages, system)
        openai_tools = _anthropic_tools_to_openai(tools)

        kwargs: dict[str, Any] = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=openai_messages,
            tools=openai_tools,
        )
        if force_tool_name and openai_tools:
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": force_tool_name},
            }
        elif force_tool_use and openai_tools:
            kwargs["tool_choice"] = "required"

        logger.debug(
            "OpenAI request: model=%s turns=%d", self._model, len(openai_messages)
        )
        response = await self._client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
        return openai_response_to_message(response)
