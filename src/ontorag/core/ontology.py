"""Multi-ontology scoping helpers — single source of truth.

Maps an optional ontology id to the Fuseki named-graph URIs and validates ids
before they are interpolated anywhere (graph URI or Cypher). ``ontology=None``
means the default/legacy single-ontology graphs, preserving backward
compatibility.
"""

from __future__ import annotations

import re

DEFAULT_SCHEMA_GRAPH = "urn:ontorag:schema"
DEFAULT_DATA_GRAPH = "urn:ontorag:data"

_ONTOLOGY_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_ontology_id(ontology: str | None) -> str | None:
    """Return the id unchanged if valid (or None), else raise.

    Args:
        ontology: Ontology slug or None.

    Returns:
        The validated id, or None.

    Raises:
        ValueError: If the id is non-None and not ``^[a-zA-Z0-9_-]+$`` — this
            guards every downstream graph-URI / Cypher interpolation.
    """
    if ontology is None:
        return None
    if not _ONTOLOGY_ID_RE.match(ontology):
        raise ValueError(
            f"Invalid ontology id: {ontology!r}. Expected ^[a-zA-Z0-9_-]+$."
        )
    return ontology


def schema_graph_uri(ontology: str | None) -> str:
    """Named-graph URI for an ontology's TBox (None → legacy default)."""
    ontology = validate_ontology_id(ontology)
    if ontology is None:
        return DEFAULT_SCHEMA_GRAPH
    return f"urn:ontorag:{ontology}:schema"


def data_graph_uri(ontology: str | None) -> str:
    """Named-graph URI for an ontology's ABox (None → legacy default)."""
    ontology = validate_ontology_id(ontology)
    if ontology is None:
        return DEFAULT_DATA_GRAPH
    return f"urn:ontorag:{ontology}:data"
