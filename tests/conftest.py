from __future__ import annotations

import pytest

from ontorag.llm.base import _CompletionMessage, _ToolUseBlock
from ontorag.stores.base import ClassSummary, LoadResult, SchemaResult


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture()
def pokemon_schema() -> SchemaResult:
    """Minimal Pokémon TBox for testing (no Fuseki required)."""
    return SchemaResult(
        total_classes=4,
        total_properties=3,
        namespaces={"pk": "http://example.org/pokemon#"},
        classes=[
            ClassSummary(
                uri="http://example.org/pokemon#Pokemon",
                label="Pokemon",
                parent_uri=None,
                property_count=3,
                instance_count=5,
            ),
            ClassSummary(
                uri="http://example.org/pokemon#LegendaryPokemon",
                label="Legendary Pokemon",
                parent_uri="http://example.org/pokemon#Pokemon",
                property_count=3,
                instance_count=1,
            ),
            ClassSummary(
                uri="http://example.org/pokemon#Type",
                label="Type",
                parent_uri=None,
                property_count=1,
                instance_count=18,
            ),
            ClassSummary(
                uri="http://example.org/pokemon#Trainer",
                label="Trainer",
                parent_uri=None,
                property_count=2,
                instance_count=3,
            ),
        ],
    )


def make_tool_response(tool_name: str, data: dict) -> _CompletionMessage:
    """Build a _CompletionMessage that looks like a single tool_use response."""
    return _CompletionMessage(
        content=[
            _ToolUseBlock(
                id="test-id-001",
                name=tool_name,
                input=data,
            )
        ],
        stop_reason="tool_use",
    )


class MockLLM:
    """Deterministic mock LLM — returns a preset _CompletionMessage on complete()."""

    def __init__(self, response: _CompletionMessage) -> None:
        self._response = response
        self.calls: list[dict] = []

    async def complete(self, messages, tools, system=None, force_tool_use=False, force_tool_name=None):
        self.calls.append({"messages": messages, "tools": tools, "force_tool_name": force_tool_name})
        return self._response


class MockGraphStore:
    """Minimal mock GraphStore — returns the injected schema."""

    def __init__(self, schema: SchemaResult) -> None:
        self._schema = schema
        self.load_calls: list[dict] = []

    async def get_schema(self) -> SchemaResult:
        return self._schema

    async def load_rdf(self, path: str, mode: str = "auto", **kwargs) -> LoadResult:
        self.load_calls.append({"path": path, "mode": mode})
        return LoadResult(triples_loaded=5, source=path, mode="data")
