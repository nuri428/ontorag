"""LLM-based column → TBox property mapping with save/load/validate."""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from ontorag.stores.base import SchemaResult

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are an RDF ontology expert. Map CSV/JSON column names to OWL property URIs "
    "from a given TBox schema. Respond ONLY with valid JSON."
)

_PROMPT_TMPL = """\
TBox properties available:
{properties}

Column names to map (file: {filename}):
{columns}

Class URI (if known): {class_uri}

For each column, propose the best matching property URI from the TBox.
If no property fits, use null.

Respond with JSON only:
{{"mappings": [{{"column": "<col>", "predicate_uri": "<uri_or_null>", "confidence": 0.0}}]}}
"""

_MAPPING_FILE_FIELDS = frozenset(
    f.name
    for f in fields(
        type("_sentinel", (), {"__dataclass_fields__": {}})  # resolved below
    )
)


@dataclass
class ColumnMapping:
    column_name: str
    predicate_uri: str | None
    confidence: float


@dataclass
class MappingFile:
    schema_hash: str
    class_uri: str | None
    id_column: str | None
    columns: list[ColumnMapping]
    last_row: int = 0


_MAPPING_FILE_FIELDS = frozenset(f.name for f in fields(MappingFile))


def compute_schema_hash(schema: SchemaResult) -> str:
    """Stable hash of (class_uri_set, property_uri_set) — order-independent."""
    class_uris = sorted(c.uri for c in schema.classes)
    prop_uris = sorted(p.uri for p in schema.properties)
    payload = json.dumps(
        {"classes": class_uris, "properties": prop_uris}, sort_keys=True
    )
    return hashlib.md5(payload.encode(), usedforsecurity=False).hexdigest()


def validate_mapping_hash(mapping_file: MappingFile, schema: SchemaResult) -> bool:
    return mapping_file.schema_hash == compute_schema_hash(schema)


def save_mapping(mapping_file: MappingFile, path: Path) -> None:
    data = asdict(mapping_file)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_mapping(path: Path) -> MappingFile:
    data = json.loads(path.read_text(encoding="utf-8"))
    columns = [ColumnMapping(**c) for c in data.pop("columns", [])]
    # Drop unknown keys for forward compatibility (future MappingFile versions)
    known = {k: v for k, v in data.items() if k in _MAPPING_FILE_FIELDS}
    return MappingFile(**known, columns=columns)


async def propose_mapping(
    llm: Any,
    schema: SchemaResult,
    columns: list[str],
    class_uri: str | None = None,
    filename: str = "",
) -> list[ColumnMapping]:
    """Ask the LLM to map column names to TBox property URIs."""
    valid_uris = {p.uri for p in schema.properties}
    properties_text = "\n".join(f"  {p.uri} ({p.label})" for p in schema.properties)
    prompt = _PROMPT_TMPL.format(
        properties=properties_text,
        columns="\n".join(f"  - {c}" for c in columns),
        class_uri=class_uri or "(auto-detect)",
        filename=filename,
    )

    try:
        response = await llm.complete(
            messages=[{"role": "user", "content": prompt}],
            system=_SYSTEM,
            tools=[],
            force_tool_name=None,
        )
        text = next((b.text for b in response.content if hasattr(b, "text")), "")
        parsed = json.loads(text)
        # LLM may return the array directly instead of {"mappings": [...]}
        if isinstance(parsed, list):
            raw = parsed
        elif isinstance(parsed, dict):
            raw = parsed.get("mappings", [])
        else:
            logger.warning(
                "propose_mapping: unexpected JSON type %s — ignoring", type(parsed)
            )
            return []
    except json.JSONDecodeError as exc:
        # LLM returned non-JSON — recoverable format error, return empty
        logger.warning("propose_mapping: LLM returned invalid JSON — %s", exc)
        return []
    except Exception:
        # Network error, auth failure, etc. — propagate so caller can surface it
        raise

    result: list[ColumnMapping] = []
    for item in raw:
        col = item.get("column", "")
        uri = item.get("predicate_uri")
        conf = float(item.get("confidence", 0.0))
        if uri is not None and uri not in valid_uris:
            uri = None
        result.append(
            ColumnMapping(column_name=col, predicate_uri=uri, confidence=conf)
        )
    return result


def mint_subject_uri(
    row: dict[str, Any],
    id_column: str | None,
    namespace: str,
    filepath: str,
    row_index: int,
) -> str:
    """Return a subject URI for a row.

    If id_column is given, percent-encode the value as a URI path segment.
    Falls back to deterministic UUID5 when the column is absent or the value
    is blank — so reloading the same file always yields the same URI.
    """
    if id_column and id_column in row:
        slug = urllib.parse.quote(str(row[id_column]).strip(), safe="-._~")
        if slug:
            return f"{namespace.rstrip('/')}/{slug}"
    seed = f"{filepath}:{row_index}"
    deterministic_uuid = uuid.uuid5(uuid.NAMESPACE_URL, seed)
    return f"{namespace.rstrip('/')}/entity-{deterministic_uuid}"
