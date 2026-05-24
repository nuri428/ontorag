from __future__ import annotations

"""Shared n10s ARRAY-value unwrapping helpers for the Neo4j adapter.

n10s with ``handleMultival=ARRAY`` stores EVERY RDF property as a Cypher list.
These two helpers convert those lists back to protocol-shaped values; they were
previously duplicated across the entity/schema mixins.

- ``unpack_value``: ARRAY → scalar when single-valued, list when multi-valued,
  None when empty. Preserves the Fuseki parity where a property is a scalar
  unless there are genuinely multiple values.
- ``first_scalar``: always the first element (or the value itself / None).
  Used for fields that are single-valued by construction (labels, comments).
"""

from typing import Any


def unpack_value(val: Any) -> Any:
    """Unwrap an ARRAY-config value to scalar, list, or None.

    Args:
        val: Raw Neo4j property value (list or scalar).

    Returns:
        The sole element if the list has length 1, the list if it has more,
        None if empty, or the value unchanged if not a list.
    """
    if isinstance(val, list):
        if len(val) == 0:
            return None
        if len(val) == 1:
            return val[0]
        return val
    return val


def first_scalar(val: Any) -> Any:
    """Return the first element of a list value, or the value itself.

    Args:
        val: Raw Neo4j value (list or scalar).

    Returns:
        First list element, the scalar, or None for an empty list / None.
    """
    if val is None:
        return None
    if isinstance(val, list):
        return val[0] if val else None
    return val
