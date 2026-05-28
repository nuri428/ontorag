"""Named-graph scoping helpers — single source of truth.

Two orthogonal dimensions decide which Fuseki named graph a triple lives in:

1. **Ontology** — an optional slug isolating one ontology's triples from
   another's. ``None`` is the default/legacy single-ontology graphs.
2. **Layer** — which *reasoning layer* the triple belongs to. v0.7.0 introduces
   the 4-layer named-graph model (see ``docs/design/named-graph-layers.md``)::

       OntologyLayer.semantic    TBox — class/property declarations (OWL 2)
       OntologyLayer.policy      SHACL shapes + SKOS schemes  (reserved — Phase 2)
       OntologyLayer.state       ABox — instance / time-series data
       OntologyLayer.provenance  PROV-O activity + DCAT meta  (reserved — Phase 4)

Backward compatibility (CRITICAL): the layer *names* are the new canonical
vocabulary, but the *physical* graph URIs of the two pre-v0.7 layers are kept
unchanged — ``semantic`` → ``urn:ontorag:schema`` and ``state`` →
``urn:ontorag:data``. Renaming the physical URIs would orphan every persisted
TDB2 triple and break the test suite that asserts on them. The strings
``"schema"``/``"data"`` therefore stay accepted everywhere as aliases for
``semantic``/``state`` (see :func:`resolve_layer`).

``ontology=None`` always means the default/legacy single-ontology graphs,
preserving backward compatibility.
"""

from __future__ import annotations

import re
from enum import Enum


class OntologyLayer(str, Enum):
    """A reasoning layer, each backed by its own Fuseki named graph.

    The ``str`` mixin makes a member compare and serialise as its value
    (JSON, SPARQL interpolation) without ``.value`` ceremony. The canonical
    names are the v0.7+ vocabulary; ``"schema"``/``"data"`` remain accepted
    aliases via :func:`resolve_layer`.
    """

    semantic = "semantic"  # TBox — pre-v0.7 name: "schema"
    policy = "policy"  # SHACL + SKOS — reserved (deferred Phase 2)
    state = "state"  # ABox / time-series — pre-v0.7 name: "data" (was "dynamic")
    provenance = "provenance"  # PROV-O + DCAT — reserved (deferred Phase 4)


# Per-layer graph-URI suffix. The two legacy layers keep their pre-v0.7
# physical suffixes ("schema"/"data") for backward compatibility; new layers
# use their own name. This map — not the enum value — decides the URI suffix.
_LAYER_SUFFIX: dict[OntologyLayer, str] = {
    OntologyLayer.semantic: "schema",
    OntologyLayer.policy: "policy",
    OntologyLayer.state: "data",
    OntologyLayer.provenance: "provenance",
}

# Default (ontology=None) named-graph URI per layer. Single source of truth for
# "which graph does layer L live in" when no per-ontology scope is given.
LAYER_GRAPH_URI: dict[OntologyLayer, str] = {
    layer: f"urn:ontorag:{suffix}" for layer, suffix in _LAYER_SUFFIX.items()
}

# Accepted string aliases → canonical layer. The pre-v0.7 vocabulary
# ("schema"/"data") maps onto the new layer names.
_LAYER_ALIASES: dict[str, OntologyLayer] = {
    "schema": OntologyLayer.semantic,
    "data": OntologyLayer.state,
}

# Legacy default graph URIs — retained as module constants for backward-compat
# imports (tests, core/sparql.py, embedding mixin). Derived from the layer map
# so there is exactly one source of truth for the literal strings.
DEFAULT_SCHEMA_GRAPH = LAYER_GRAPH_URI[OntologyLayer.semantic]  # urn:ontorag:schema
DEFAULT_DATA_GRAPH = LAYER_GRAPH_URI[OntologyLayer.state]  # urn:ontorag:data

_ONTOLOGY_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def resolve_layer(layer: str | OntologyLayer) -> OntologyLayer:
    """Coerce a layer name (or backward-compat alias) to an OntologyLayer.

    Accepts the canonical names (``"semantic"``, ``"policy"``, ``"state"``,
    ``"provenance"``) and the pre-v0.7 aliases ``"schema"`` → semantic and
    ``"data"`` → state.

    Args:
        layer: An OntologyLayer member or a layer-name/alias string.

    Returns:
        The canonical OntologyLayer.

    Raises:
        ValueError: If the value is not a known layer or alias.
    """
    if isinstance(layer, OntologyLayer):
        return layer
    key = layer.strip().lower()
    if key in _LAYER_ALIASES:
        return _LAYER_ALIASES[key]
    try:
        return OntologyLayer(key)
    except ValueError:
        valid = ", ".join(member.value for member in OntologyLayer)
        aliases = ", ".join(sorted(_LAYER_ALIASES))
        raise ValueError(
            f"Unknown ontology layer: {layer!r}. "
            f"Expected one of {{{valid}}} or an alias {{{aliases}}}."
        ) from None


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


def layer_graph_uri(ontology: str | None, layer: str | OntologyLayer) -> str:
    """Named-graph URI for an (ontology, layer) pair — always concrete.

    Unlike :func:`scoped_graph`, this never returns None: it names a specific
    layer graph even when ``ontology`` is None. Callers that read a single
    layer (e.g. SHACL shapes, PROV-O activity, Bayesian CPTs) target this URI
    directly rather than relying on the union default graph.

    Args:
        ontology: Validated ontology id, or None for the default/legacy graph.
        layer: OntologyLayer or a layer-name/alias string.

    Returns:
        The named-graph URI string, e.g. ``urn:ontorag:schema`` (default
        semantic) or ``urn:ontorag:pokemon:policy`` (named, policy layer).

    Raises:
        ValueError: If the layer or ontology id is invalid.
    """
    layer = resolve_layer(layer)
    ontology = validate_ontology_id(ontology)
    suffix = _LAYER_SUFFIX[layer]
    if ontology is None:
        return LAYER_GRAPH_URI[layer]
    return f"urn:ontorag:{ontology}:{suffix}"


def schema_graph_uri(ontology: str | None) -> str:
    """Named-graph URI for an ontology's TBox (None → legacy default).

    Thin wrapper over :func:`layer_graph_uri` for the semantic layer.
    """
    return layer_graph_uri(ontology, OntologyLayer.semantic)


def data_graph_uri(ontology: str | None) -> str:
    """Named-graph URI for an ontology's ABox (None → legacy default).

    Thin wrapper over :func:`layer_graph_uri` for the state layer.
    """
    return layer_graph_uri(ontology, OntologyLayer.state)


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
    return layer_graph_uri(ontology, resolve_layer(kind))


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
