from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class SSEEvent:
    """A single server-sent event emitted during an agent turn.

    type values:
      thinking    — agent reasoning step
      tool_call   — LLM requested a tool
      tool_result — tool execution result
      text        — LLM final text chunk
      done        — turn complete
      error       — unrecoverable error
    """

    type: str
    content: Any = None
    tool: str | None = None


# ── Internal duck-typed response types (provider-agnostic) ────────────────────


@dataclass
class _TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.input is None:
            self.input = {}


@dataclass
class _CompletionMessage:
    """Duck-typed Anthropic Message for provider-agnostic AgentLoop."""

    content: list[_TextBlock | _ToolUseBlock]
    stop_reason: str  # "end_turn" | "tool_use"
    usage: dict[str, int] | None = None  # prompt_tokens, cached_tokens, completion_tokens


def openai_response_to_message(response: Any) -> _CompletionMessage:
    """Convert an OpenAI ChatCompletion response to _CompletionMessage."""
    choice = response.choices[0]
    msg = choice.message
    finish_reason = choice.finish_reason  # "stop" | "tool_calls"

    content: list[_TextBlock | _ToolUseBlock] = []

    if msg.content:
        content.append(_TextBlock(text=msg.content))

    for tc in msg.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments)
        except (json.JSONDecodeError, AttributeError):
            args = {}
        content.append(
            _ToolUseBlock(
                id=tc.id,
                name=tc.function.name,
                input=args,
            )
        )

    stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"

    usage = None
    if getattr(response, "usage", None) is not None:
        u = response.usage
        cached = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        usage = {
            "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
            "cached_tokens": cached,
            "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
        }

    return _CompletionMessage(content=content, stop_reason=stop_reason, usage=usage)
