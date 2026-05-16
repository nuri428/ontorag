"""Tests for OpenAI provider format conversion and factory."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ontorag.llm.base import _CompletionMessage, _TextBlock, _ToolUseBlock, openai_response_to_message
from ontorag.llm.openai import _anthropic_messages_to_openai, _anthropic_tools_to_openai


# ── _anthropic_tools_to_openai ───────────────────────────────────────────────

def test_tools_to_openai_basic():
    tools = [
        {
            "name": "get_schema",
            "description": "Returns schema",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }
    ]
    result = _anthropic_tools_to_openai(tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "get_schema"
    assert result[0]["function"]["description"] == "Returns schema"
    assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}, "required": []}


def test_tools_to_openai_missing_description():
    tools = [{"name": "my_tool", "input_schema": {"type": "object", "properties": {}}}]
    result = _anthropic_tools_to_openai(tools)
    assert result[0]["function"]["description"] == ""


# ── _anthropic_messages_to_openai ───────────────────────────────────────────

def test_messages_to_openai_user_string():
    messages = [{"role": "user", "content": "Hello"}]
    result = _anthropic_messages_to_openai(messages, system=None)
    assert result == [{"role": "user", "content": "Hello"}]


def test_messages_to_openai_with_system():
    messages = [{"role": "user", "content": "Hi"}]
    result = _anthropic_messages_to_openai(messages, system="You are helpful")
    assert result[0] == {"role": "system", "content": "You are helpful"}
    assert result[1] == {"role": "user", "content": "Hi"}


def test_messages_to_openai_assistant_with_text():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
        }
    ]
    result = _anthropic_messages_to_openai(messages, system=None)
    assert len(result) == 1
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "Hello!"
    assert "tool_calls" not in result[0]


def test_messages_to_openai_assistant_with_tool_use():
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_001",
                    "name": "get_schema",
                    "input": {"limit": 10},
                }
            ],
        }
    ]
    result = _anthropic_messages_to_openai(messages, system=None)
    assert len(result) == 1
    msg = result[0]
    assert msg["role"] == "assistant"
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "tool_001"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_schema"


def test_messages_to_openai_tool_result():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool_001",
                    "content": '{"classes": []}',
                }
            ],
        }
    ]
    result = _anthropic_messages_to_openai(messages, system=None)
    assert len(result) == 1
    msg = result[0]
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "tool_001"
    assert msg["content"] == '{"classes": []}'


# ── openai_response_to_message ───────────────────────────────────────────────

def _make_openai_response(content: str | None, tool_calls=None, finish_reason="stop"):
    """Build a minimal mock OpenAI ChatCompletion response."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls or []

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    return response


def test_openai_response_text():
    response = _make_openai_response("Hello from GPT")
    msg = openai_response_to_message(response)
    assert msg.stop_reason == "end_turn"
    assert len(msg.content) == 1
    assert msg.content[0].type == "text"
    assert msg.content[0].text == "Hello from GPT"


def test_openai_response_tool_call():
    import json

    tc = MagicMock()
    tc.id = "call_abc"
    tc.function.name = "get_schema"
    tc.function.arguments = json.dumps({"limit": 5})

    response = _make_openai_response(None, tool_calls=[tc], finish_reason="tool_calls")
    msg = openai_response_to_message(response)
    assert msg.stop_reason == "tool_use"
    assert len(msg.content) == 1
    block = msg.content[0]
    assert block.type == "tool_use"
    assert block.name == "get_schema"
    assert block.input == {"limit": 5}
    assert block.id == "call_abc"


def test_openai_response_empty():
    response = _make_openai_response(None)
    msg = openai_response_to_message(response)
    assert msg.stop_reason == "end_turn"
    assert msg.content == []


# ── LLM factory ─────────────────────────────────────────────────────────────

def test_factory_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from ontorag.llm.factory import get_llm_provider
    from ontorag.llm.anthropic import AnthropicProvider
    provider = get_llm_provider()
    assert isinstance(provider, AnthropicProvider)


def test_factory_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from ontorag.llm.factory import get_llm_provider
    from ontorag.llm.openai import OpenAIProvider
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)


def test_factory_ollama(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    from ontorag.llm.factory import get_llm_provider
    from ontorag.llm.ollama import OllamaProvider
    provider = get_llm_provider()
    assert isinstance(provider, OllamaProvider)


def test_factory_unknown_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "foobar")
    from ontorag.llm.factory import get_llm_provider
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm_provider()
