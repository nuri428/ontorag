from __future__ import annotations

import logging
from typing import Any

from ontorag.learn._utils import class_uris, structured_call
from ontorag.learn.base import TermTypingResult
from ontorag.stores.base import SchemaResult

logger = logging.getLogger(__name__)

_TOOL_DEF: dict[str, Any] = {
    "name": "report_term_typings",
    "description": (
        "Report ranked TBox class assignments for the given text mention. "
        "Only use class URIs that exist in the provided schema."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "typings": {
                "type": "array",
                "description": "Ranked list of class assignments, best match first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "class_uri": {
                            "type": "string",
                            "description": "Exact class URI from the schema.",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "reasoning": {"type": "string"},
                    },
                    "required": ["class_uri", "confidence"],
                },
            },
        },
        "required": ["typings"],
    },
}

_SYSTEM = (
    "You are an ontology expert. Given a text mention and an OWL ontology schema, "
    "rank the existing TBox classes that best describe this entity or concept. "
    "Only output class URIs that appear in the schema. "
    "Respond using the provided tool."
)


def _schema_summary(schema: SchemaResult) -> str:
    lines = [f"Namespace: {list(schema.namespaces.values())[0] if schema.namespaces else '(unknown)'}"]
    lines.append(f"Classes ({schema.total_classes}):")
    for cls in schema.classes:
        label = f" ({cls.label})" if cls.label else ""
        parent = f" subClassOf {cls.parent_uri}" if cls.parent_uri else ""
        lines.append(f"  {cls.uri}{label}{parent}")
    return "\n".join(lines)


async def type_term(
    llm: Any,
    schema: SchemaResult,
    term: str,
    context: str | None,
    top_k: int,
) -> list[TermTypingResult]:
    """Task A: map a text mention to ranked TBox classes.

    Args:
        llm: LLM provider (must have complete()).
        schema: Current TBox SchemaResult.
        term: Text mention to classify.
        context: Optional surrounding text for disambiguation.
        top_k: Maximum number of results to return.

    Returns:
        Ranked list of TermTypingResult, filtered to valid TBox class URIs.
    """
    valid_uris = class_uris(schema)

    prompt_parts = [
        f"Schema:\n{_schema_summary(schema)}",
        f"\nTerm to classify: {term!r}",
    ]
    if context:
        prompt_parts.append(f"Context: {context[:500]}")
    prompt_parts.append(f"\nReturn the top {top_k} TBox classes that best match this term.")

    messages = [{"role": "user", "content": "\n".join(prompt_parts)}]

    try:
        raw = await structured_call(llm, messages, _TOOL_DEF, system=_SYSTEM)
    except Exception as exc:
        logger.warning("type_term LLM call failed for %r: %s", term, exc)
        return []

    results: list[TermTypingResult] = []
    for item in raw.get("typings", []):
        uri = item.get("class_uri", "")
        if uri not in valid_uris:
            logger.debug("type_term: skipping unknown URI %r", uri)
            continue
        cls_match = next((c for c in schema.classes if c.uri == uri), None)
        label = cls_match.label or uri.split("#")[-1].split("/")[-1] if cls_match else ""
        results.append(TermTypingResult(
            term=term,
            class_uri=uri,
            label=label,
            confidence=float(item.get("confidence", 0.0)),
            reasoning=item.get("reasoning"),
        ))

    results.sort(key=lambda r: r.confidence, reverse=True)
    return results[:top_k]
