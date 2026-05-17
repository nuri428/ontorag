from __future__ import annotations

import re
from typing import Any

from ontorag.llm.base import _CompletionMessage, _ToolUseBlock
from ontorag.stores.base import SchemaResult

_STANDARD_PREFIXES = frozenset(
    {
        "rdf",
        "rdfs",
        "owl",
        "xsd",
        "skos",
        "dc",
        "dcterms",
        "foaf",
        "schema",
    }
)


def primary_namespace(schema: SchemaResult) -> str:
    """Return the first non-standard namespace URI from the TBox, or a fallback.

    Used to mint URIs for newly discovered entities.
    """
    for prefix, uri in schema.namespaces.items():
        if prefix.lower() not in _STANDARD_PREFIXES:
            return uri
    return "urn:ontorag:learned:"


def mint_uri(term: str, schema: SchemaResult) -> str:
    """Derive a URI for a new entity using the TBox primary namespace.

    Args:
        term: Human-readable entity label.
        schema: Current TBox schema (for namespace detection).

    Returns:
        URI string, e.g. "http://example.org/pokemon#Eevee".
    """
    base = primary_namespace(schema)
    slug = re.sub(r"\s+", "_", term.strip())
    slug = re.sub(r"[^\w]", "", slug, flags=re.ASCII)
    return f"{base}{slug}"


def class_uris(schema: SchemaResult) -> frozenset[str]:
    """Return the set of all TBox class URIs."""
    return frozenset(c.uri for c in schema.classes)


async def structured_call(
    llm: Any,
    messages: list[dict[str, Any]],
    tool_def: dict[str, Any],
    system: str | None = None,
) -> dict[str, Any]:
    """Call the LLM forcing exactly one named tool and return its input dict.

    Args:
        llm: Any provider with a complete() method.
        messages: Anthropic-format message list.
        tool_def: Anthropic tool definition dict (must have "name" key).
        system: Optional system prompt.

    Returns:
        The tool's ``input`` dict from the LLM response.

    Raises:
        RuntimeError: If the LLM did not call the expected tool.
    """
    tool_name = tool_def["name"]
    response: _CompletionMessage = await llm.complete(
        messages,
        [tool_def],
        system=system,
        force_tool_name=tool_name,
    )
    for block in response.content:
        if isinstance(block, _ToolUseBlock) and block.name == tool_name:
            return block.input or {}
    raise RuntimeError(
        f"LLM did not call the required tool '{tool_name}'. "
        f"Got blocks: {[type(b).__name__ for b in response.content]}"
    )
