"""Tests for AgentLoop SSE event generation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock


from ontorag.chat.agent import AgentLoop
from ontorag.llm.base import _CompletionMessage, _TextBlock, _ToolUseBlock


def _make_llm_response(stop_reason: str, blocks: list) -> _CompletionMessage:
    return _CompletionMessage(content=blocks, stop_reason=stop_reason)


def _make_store():
    store = AsyncMock()
    store.get_schema = AsyncMock(
        return_value=MagicMock(model_dump=lambda: {"classes": []})
    )
    store.get_class_detail = AsyncMock(
        return_value=MagicMock(model_dump=lambda: {"class_uri": "pk:Pokemon"})
    )
    store.find_entities = AsyncMock(
        return_value=[MagicMock(model_dump=lambda: {"uri": "pk:Pikachu"})]
    )
    store.describe_entity = AsyncMock(
        return_value=MagicMock(model_dump=lambda: {"uri": "pk:Pikachu"})
    )
    store.count_entities = AsyncMock(return_value=3)
    store.traverse = AsyncMock(return_value=MagicMock(model_dump=lambda: {"nodes": []}))
    store.find_path = AsyncMock(return_value=MagicMock(model_dump=lambda: {"path": []}))
    store.find_related = AsyncMock(return_value=[])
    store.query_pattern = AsyncMock(
        return_value=MagicMock(model_dump=lambda: {"rows": []})
    )
    return store


async def _collect(gen) -> list[dict[str, Any]]:
    events = []
    async for event in gen:
        events.append(event)
    return events


# ── basic text response ──────────────────────────────────────────────────────


async def test_agent_text_only_response():
    store = _make_store()
    llm = AsyncMock()
    llm.complete = AsyncMock(
        return_value=_make_llm_response(
            "end_turn", [_TextBlock(text="포켓몬은 12종입니다.")]
        )
    )

    agent = AgentLoop(store, llm)
    events = await _collect(agent.run("포켓몬 개수?"))

    types = [e["type"] for e in events]
    assert "thinking" in types
    assert "text" in types
    assert "done" in types

    text_events = [e for e in events if e["type"] == "text"]
    assert text_events[0]["content"] == "포켓몬은 12종입니다."


# ── tool call and result ─────────────────────────────────────────────────────


async def test_agent_tool_call_then_text():
    store = _make_store()
    llm = AsyncMock()

    tool_block = _ToolUseBlock(id="call_1", name="get_schema", input={})
    text_block = _TextBlock(text="스키마 확인 완료")

    llm.complete = AsyncMock(
        side_effect=[
            _make_llm_response("tool_use", [tool_block]),
            _make_llm_response("end_turn", [text_block]),
        ]
    )

    agent = AgentLoop(store, llm)
    events = await _collect(agent.run("스키마 보여줘"))

    types = [e["type"] for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert "text" in types
    assert "done" in types

    tc_event = next(e for e in events if e["type"] == "tool_call")
    assert tc_event["tool"] == "get_schema"
    store.get_schema.assert_awaited_once()


# ── tool dispatch ────────────────────────────────────────────────────────────


async def test_agent_find_entities_tool():
    store = _make_store()
    llm = AsyncMock()

    tool_block = _ToolUseBlock(
        id="call_2",
        name="find_entities",
        input={"class_uri": "pk:Pokemon"},
    )
    llm.complete = AsyncMock(
        side_effect=[
            _make_llm_response("tool_use", [tool_block]),
            _make_llm_response("end_turn", [_TextBlock(text="찾았습니다")]),
        ]
    )

    agent = AgentLoop(store, llm)
    events = await _collect(agent.run("포켓몬 목록"))

    # Tool dispatcher always queries with cap+1 (31) to detect has_more
    store.find_entities.assert_awaited_once_with(
        class_uri="pk:Pokemon", filters=None, limit=31
    )
    result_events = [e for e in events if e["type"] == "tool_result"]
    assert len(result_events) == 1
    assert result_events[0]["tool"] == "find_entities"
    # New result shape — dict, not list
    content = result_events[0]["content"]
    assert isinstance(content, dict)
    assert "entities" in content
    assert "returned" in content
    assert "has_more" in content
    # Default include_properties=true — entities carry the properties dict.
    # (C2 was rolled back: keeping result size bounded via the 30-row cap,
    # but not at the cost of forcing an extra describe_entity round-trip.)
    for e in content["entities"]:
        assert "properties" in e


async def test_agent_find_entities_caps_at_30():
    store = _make_store()
    # Return 31 entities — store returns extras to signal overflow
    from ontorag.stores.base import EntityResult

    store.find_entities = AsyncMock(
        return_value=[
            EntityResult(uri=f"ex:e{i}", label=f"e{i}", class_uri="ex:C", properties={})
            for i in range(31)
        ]
    )
    llm = AsyncMock()
    tool_block = _ToolUseBlock(
        id="c", name="find_entities", input={"class_uri": "ex:C"}
    )
    llm.complete = AsyncMock(
        side_effect=[
            _make_llm_response("tool_use", [tool_block]),
            _make_llm_response("end_turn", [_TextBlock(text="ok")]),
        ]
    )
    agent = AgentLoop(store, llm)
    events = await _collect(agent.run("foo"))
    result = [e for e in events if e["type"] == "tool_result"][0]["content"]
    assert result["returned"] == 30
    assert result["has_more"] is True


async def test_agent_find_entities_can_opt_out_of_properties():
    """include_properties=false strips the per-entity properties dict
    (used for pure enumeration questions)."""
    from ontorag.stores.base import EntityResult

    store = _make_store()
    store.find_entities = AsyncMock(
        return_value=[
            EntityResult(
                uri="ex:e1", label="e1", class_uri="ex:C", properties={"p": "v"}
            )
        ]
    )
    llm = AsyncMock()
    tool_block = _ToolUseBlock(
        id="c",
        name="find_entities",
        input={"class_uri": "ex:C", "include_properties": False},
    )
    llm.complete = AsyncMock(
        side_effect=[
            _make_llm_response("tool_use", [tool_block]),
            _make_llm_response("end_turn", [_TextBlock(text="ok")]),
        ]
    )
    agent = AgentLoop(store, llm)
    events = await _collect(agent.run("foo"))
    result = [e for e in events if e["type"] == "tool_result"][0]["content"]
    assert "properties" not in result["entities"][0]


async def test_agent_count_entities_tool():
    store = _make_store()
    llm = AsyncMock()

    tool_block = _ToolUseBlock(
        id="c3", name="count_entities", input={"class_uri": "pk:Pokemon"}
    )
    llm.complete = AsyncMock(
        side_effect=[
            _make_llm_response("tool_use", [tool_block]),
            _make_llm_response("end_turn", [_TextBlock(text="3개입니다")]),
        ]
    )

    agent = AgentLoop(store, llm)
    await _collect(agent.run("몇 개야?"))

    store.count_entities.assert_awaited_once_with("pk:Pokemon")


async def test_agent_traverse_tool():
    store = _make_store()
    llm = AsyncMock()

    tool_block = _ToolUseBlock(
        id="c4",
        name="traverse_graph",
        input={"start_uri": "pk:Venusaur", "max_depth": 2, "direction": "outgoing"},
    )
    llm.complete = AsyncMock(
        side_effect=[
            _make_llm_response("tool_use", [tool_block]),
            _make_llm_response("end_turn", [_TextBlock(text="순회 완료")]),
        ]
    )

    agent = AgentLoop(store, llm)
    await _collect(agent.run("진화 체인?"))

    store.traverse.assert_awaited_once()


async def test_agent_unknown_tool_returns_error():
    store = _make_store()
    llm = AsyncMock()

    tool_block = _ToolUseBlock(id="c5", name="nonexistent_tool", input={})
    llm.complete = AsyncMock(
        side_effect=[
            _make_llm_response("tool_use", [tool_block]),
            _make_llm_response("end_turn", [_TextBlock(text="처리함")]),
        ]
    )

    agent = AgentLoop(store, llm)
    events = await _collect(agent.run("존재하지 않는 툴"))

    result_events = [e for e in events if e["type"] == "tool_result"]
    assert any("error" in str(e.get("content", "")) for e in result_events)


# ── done event always last ───────────────────────────────────────────────────


async def test_agent_done_is_last_event():
    store = _make_store()
    llm = AsyncMock()
    llm.complete = AsyncMock(
        return_value=_make_llm_response("end_turn", [_TextBlock(text="ok")])
    )

    agent = AgentLoop(store, llm)
    events = await _collect(agent.run("test"))

    assert events[-1]["type"] == "done"
