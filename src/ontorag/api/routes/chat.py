from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ontorag.api.deps import get_store
from ontorag.chat.agent import AgentLoop
from ontorag.llm.factory import get_llm_provider
from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    """Chat request body."""

    message: str


def _get_llm():
    try:
        return get_llm_provider()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/chat", include_in_schema=True)
async def chat(
    body: ChatRequest,
    store: FusekiStore = Depends(get_store),
) -> StreamingResponse:
    """Run an agentic ontology Q&A turn and stream SSE events.

    The agent interprets the user message, calls ontology tools as needed,
    and emits a stream of typed events visible to the client.

    SSE event types:
      thinking   — agent reasoning step
      tool_call  — LLM requested a tool (name + input)
      tool_result — tool execution result
      text       — LLM final text chunk
      done       — turn complete
      error      — unrecoverable error

    Args:
        body.message: User question in natural language.

    Returns:
        text/event-stream — Server-Sent Events.
    """
    llm = _get_llm()
    agent = AgentLoop(store, llm)

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in agent.run(body.message):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("Agent loop error")
            error_event = {"type": "error", "content": str(exc)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
