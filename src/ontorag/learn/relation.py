from __future__ import annotations

import logging
from typing import Any

from ontorag.learn._utils import structured_call
from ontorag.learn.base import ExtractedTriple
from ontorag.stores.base import SchemaResult

logger = logging.getLogger(__name__)

_TOOL_DEF: dict[str, Any] = {
    "name": "report_triples",
    "description": (
        "Report proposed RDF triples (ABox assertions) extracted from text. "
        "predicate_uri must be an existing property URI from the TBox. "
        "Provide either object_uri (for object properties) or object_value (for data properties)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "triples": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "subject_label": {
                            "type": "string",
                            "description": "Human-readable entity name.",
                        },
                        "subject_uri": {
                            "type": ["string", "null"],
                            "description": "Known URI of the subject, or null for new entity.",
                        },
                        "predicate_uri": {
                            "type": "string",
                            "description": "Exact property URI from the TBox.",
                        },
                        "object_uri": {
                            "type": ["string", "null"],
                            "description": "URI of the object entity (for object properties).",
                        },
                        "object_value": {
                            "type": ["string", "null"],
                            "description": "Literal value (for data properties).",
                        },
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                    "required": ["subject_label", "predicate_uri", "confidence"],
                },
            },
        },
        "required": ["triples"],
    },
}

_SYSTEM = (
    "You are an ontology expert. Extract RDF triples from the given text using "
    "only predicates that exist in the provided TBox schema. "
    "Each triple must have a subject entity, a predicate URI from the schema, "
    "and either an object entity URI or a literal value. "
    "Respond using the provided tool."
)


def _schema_summary(schema: SchemaResult) -> str:
    lines = ["Properties (use only these predicate URIs):"]
    for prop in schema.properties:
        label = f" ({prop.label})" if prop.label else ""
        ptype = f" [{prop.prop_type}]"
        lines.append(f"  {prop.uri}{label}{ptype}")
    if not schema.properties:
        lines.append("  (no properties in schema — use any property URI from domain knowledge)")

    lines.append("\nClasses (for context):")
    for cls in schema.classes:
        label = f" ({cls.label})" if cls.label else ""
        lines.append(f"  {cls.uri}{label}")
    return "\n".join(lines)


def _collect_property_uris(schema: SchemaResult) -> frozenset[str]:
    """Gather property URIs from SchemaResult.properties (preferred) or fallback."""
    if schema.properties:
        return frozenset(p.uri for p in schema.properties)
    return frozenset()


async def extract_relations(
    llm: Any,
    schema: SchemaResult,
    text: str,
    entities: list[str] | None,
    min_confidence: float,
) -> list[ExtractedTriple]:
    """Task C: propose object/data property triples from text.

    Args:
        llm: LLM provider.
        schema: Current TBox SchemaResult.
        text: Source text.
        entities: Optional entity label whitelist.
        min_confidence: Filter threshold (applied here, before return).

    Returns:
        List of ExtractedTriple with valid predicate URIs.
    """
    prop_uris = _collect_property_uris(schema)

    prompt_parts = [
        f"Schema:\n{_schema_summary(schema)}",
        f"\nText:\n{text[:2000]}",
    ]
    if entities:
        prompt_parts.append(
            "\nFocus on these entities: " + ", ".join(entities)
        )
    prompt_parts.append(
        "\nExtract RDF triples. Use only predicate URIs listed in the schema above."
    )

    messages = [{"role": "user", "content": "\n".join(prompt_parts)}]

    try:
        raw = await structured_call(llm, messages, _TOOL_DEF, system=_SYSTEM)
    except Exception as exc:
        logger.warning("extract_relations LLM call failed: %s", exc)
        return []

    results: list[ExtractedTriple] = []
    for item in raw.get("triples", []):
        pred = item.get("predicate_uri", "")
        confidence = float(item.get("confidence", 0.0))

        if confidence < min_confidence:
            continue
        if prop_uris and pred not in prop_uris:
            logger.debug("extract_relations: skipping unknown predicate %r", pred)
            continue

        obj_uri = item.get("object_uri") or None

        results.append(ExtractedTriple(
            subject_label=item.get("subject_label", ""),
            subject_uri=item.get("subject_uri") or None,
            predicate_uri=pred,
            object_uri=obj_uri,
            object_value=item.get("object_value") or None,
            confidence=confidence,
        ))

    return results
