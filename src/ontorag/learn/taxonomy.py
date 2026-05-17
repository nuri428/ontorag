from __future__ import annotations

import logging
from typing import Any

from ontorag.learn._utils import class_uris, structured_call
from ontorag.learn.base import TaxonomyRelation
from ontorag.stores.base import SchemaResult

logger = logging.getLogger(__name__)

_TOOL_DEF: dict[str, Any] = {
    "name": "report_taxonomy_relations",
    "description": (
        "Report proposed rdfs:subClassOf relations discovered from text. "
        "parent_uri must be an existing TBox class URI."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "child_term": {
                            "type": "string",
                            "description": "Human-readable label of the more specific concept.",
                        },
                        "parent_uri": {
                            "type": "string",
                            "description": "Exact URI of the existing TBox superclass.",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                    },
                    "required": ["child_term", "parent_uri", "confidence"],
                },
            },
        },
        "required": ["relations"],
    },
}

_SYSTEM = (
    "You are an ontology expert. Given text and an OWL TBox schema, propose "
    "rdfs:subClassOf relations: which new or mentioned concepts should be "
    "subclasses of existing TBox classes? "
    "Only use parent_uri values that exist in the schema. "
    "Respond using the provided tool."
)


def _schema_summary(schema: SchemaResult) -> str:
    lines = ["Classes (existing TBox):"]
    for cls in schema.classes:
        label = f" ({cls.label})" if cls.label else ""
        parent = f" subClassOf {cls.parent_uri}" if cls.parent_uri else ""
        lines.append(f"  {cls.uri}{label}{parent}")
    return "\n".join(lines)


async def discover_taxonomy(
    llm: Any,
    schema: SchemaResult,
    text: str,
    candidate_classes: list[str] | None,
) -> list[TaxonomyRelation]:
    """Task B: propose rdfs:subClassOf relations from text evidence.

    Args:
        llm: LLM provider.
        schema: Current TBox SchemaResult.
        text: Source text to analyse.
        candidate_classes: Optional whitelist of new class labels to consider.

    Returns:
        List of TaxonomyRelation with valid parent_uri values.
    """
    valid_uris = class_uris(schema)

    prompt_parts = [
        f"Schema:\n{_schema_summary(schema)}",
        f"\nText:\n{text[:2000]}",
    ]
    if candidate_classes:
        prompt_parts.append(
            "\nFocus on these candidate concepts: " + ", ".join(candidate_classes)
        )
    prompt_parts.append(
        "\nPropose rdfs:subClassOf relations: which concepts in the text are "
        "specialisations of existing TBox classes?"
    )

    messages = [{"role": "user", "content": "\n".join(prompt_parts)}]

    try:
        raw = await structured_call(llm, messages, _TOOL_DEF, system=_SYSTEM)
    except Exception as exc:
        logger.warning("discover_taxonomy LLM call failed: %s", exc)
        return []

    results: list[TaxonomyRelation] = []
    for item in raw.get("relations", []):
        parent = item.get("parent_uri", "")
        if parent not in valid_uris:
            logger.debug("discover_taxonomy: skipping unknown parent URI %r", parent)
            continue
        results.append(
            TaxonomyRelation(
                child_term=item.get("child_term", ""),
                parent_uri=parent,
                confidence=float(item.get("confidence", 0.0)),
            )
        )

    return results
