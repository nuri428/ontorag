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


def scoped_graph(ontology: str | None, kind: str) -> str | None:
    """Return the named-graph URI for a scope + graph kind, or None for union.

    Single source of truth for the scoping decision used across the Fuseki
    store and its mixins.

    Args:
        ontology: Validated ontology id or None (union/default).
        kind: ``"schema"`` or ``"data"``.

    Returns:
        The named-graph URI string, or None when ontology is None — None
        signals that queries should use the union default graph (no ``GRAPH``
        wrapper), which ``tdb2:unionDefaultGraph true`` makes the union of all
        named graphs (backward-compatible with the legacy default graphs).

    Raises:
        ValueError: If ``kind`` is not ``"schema"`` or ``"data"``.
    """
    if kind not in ("schema", "data"):
        raise ValueError(f"kind must be 'schema' or 'data', got {kind!r}")
    if ontology is None:
        return None
    return schema_graph_uri(ontology) if kind == "schema" else data_graph_uri(ontology)


def graph_clause(graph_uri: str | None, body: str) -> str:
    """Wrap a SPARQL graph-pattern body in a GRAPH clause, or bare braces.

    Single source of truth for emitting scoped vs. union SPARQL fragments.

    Args:
        graph_uri: Named-graph URI, or None for the union default graph.
        body: SPARQL graph pattern body (the part inside ``{ }``).

    Returns:
        ``GRAPH <uri> { body }`` when a URI is given, else ``{ body }`` (the
        union default graph — no GRAPH keyword).
    """
    if graph_uri is None:
        return f"{{ {body} }}"
    return f"GRAPH <{graph_uri}> {{ {body} }}"
