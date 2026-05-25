"""Neo4j multi-ontology scoping helpers.

Single source of truth for the ``_ontology`` list-property filter used by
all read methods when ``ontology`` is not None.

Design (docs/design/multi-ontology.md — Neo4j wrinkle):
  - Each :Resource node carries an ``_ontology`` *list* property.
  - When a resource URI is shared across two ontologies (e.g. owl:Class) both
    ids appear in the list so the node is reachable from either scope.
  - ``ontology=None`` → no filter (union, backward-compat).
  - ``ontology="<id>"`` → ``$ontology_id IN n._ontology`` filter appended to
    the relevant MATCH clause's WHERE block (bound param, never interpolated).
"""

from __future__ import annotations

from ontorag.core.ontology import validate_ontology_id


def ontology_scope_filter(
    ontology: str | None,
    node_alias: str = "n",
) -> tuple[str, dict]:
    """Return a Cypher WHERE fragment and params for ontology scoping.

    The returned fragment is suitable for appending to an existing WHERE block
    with ``AND``, or as a standalone ``WHERE`` clause:

        frag, params = ontology_scope_filter(ontology, node_alias="inst")
        where = f"WHERE {frag}" if frag else ""

    When ``ontology`` is None (union/all) the fragment is empty and the params
    dict is empty — callers must check ``if frag`` before prepending AND.

    Args:
        ontology: Validated or unvalidated ontology slug, or None.
        node_alias: The Cypher node variable name to filter on.

    Returns:
        ``(cypher_fragment, params_dict)``
        - ``cypher_fragment``: empty string when ontology is None, else
          ``"$ontology_id IN {node_alias}._ontology"``.
        - ``params_dict``: ``{"ontology_id": ontology}`` or ``{}``.

    Raises:
        ValueError: If ontology is non-None and not ``^[a-zA-Z0-9_-]+$``.
    """
    ontology = validate_ontology_id(ontology)
    if ontology is None:
        return "", {}
    # Use a fixed param name so callers can pass **params directly;
    # collisions are prevented by the unique param name "ontology_id".
    return f"$ontology_id IN {node_alias}._ontology", {"ontology_id": ontology}


def build_where(conditions: list[str]) -> str:
    """Assemble a Cypher WHERE clause from condition fragments.

    Empty/falsey fragments are dropped, so an empty ``ontology_scope_filter``
    result (``""``) contributes nothing.  This is more robust than appending a
    leading ``" AND "`` to a hard-coded WHERE body, which breaks if conditions
    are reordered or the first condition becomes optional.

    Args:
        conditions: Cypher boolean fragments (e.g.
            ``"t.uri IN $prop_types"``, ``"$ontology_id IN p._ontology"``).
            Falsey entries are skipped.

    Returns:
        ``"WHERE a AND b"`` for the non-empty fragments, or ``""`` when none.
    """
    parts = [c for c in conditions if c]
    if not parts:
        return ""
    return "WHERE " + " AND ".join(parts)
