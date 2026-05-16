from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SSEEvent:
    """A single server-sent event emitted during an agent turn.

    type values:
      thinking   — agent reasoning step (shown to client, not stored)
      tool_call  — LLM requested a tool
      tool_result — tool execution result
      text       — LLM final text chunk
      done       — turn complete
      error      — unrecoverable error
    """

    type: str
    content: Any = None
    tool: str | None = None
