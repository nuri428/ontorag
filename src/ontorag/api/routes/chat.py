from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ontorag.api.deps import get_store
from ontorag.chat import store as chat_store
from ontorag.chat.agent import AgentLoop, _format_schema_for_prompt
from ontorag.llm.factory import get_llm_provider
from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    """Chat request body."""

    message: str
    session_id: str | None = None


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
        body.session_id: Optional session ID for persistent conversation history.

    Returns:
        text/event-stream — Server-Sent Events.
    """
    llm = _get_llm()

    # Restore conversation history if a session_id is provided
    initial_history: list = []
    if body.session_id:
        initial_history = await chat_store.get_history(body.session_id)

    # Load schema once per request and inject into the agent's system prompt.
    # If the store is unavailable or has no schema yet, proceed without context
    # — the LLM will call get_schema on its first turn instead.
    schema_context: str | None = None
    try:
        schema = await store.get_schema()
        schema_context = _format_schema_for_prompt(schema)
    except Exception:
        logger.warning("Schema load failed for chat request — proceeding without schema context")

    agent = AgentLoop(store, llm, schema_context=schema_context, initial_history=initial_history)
    is_first_message = len(initial_history) == 0

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in agent.run(body.message):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("Agent loop error")
            error_event = {"type": "error", "content": str(exc)}
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
        finally:
            # Persist history after the stream completes (including on error/disconnect)
            if body.session_id:
                title = body.message[:40] if is_first_message else None
                await chat_store.save_session(body.session_id, agent._history, title=title)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
