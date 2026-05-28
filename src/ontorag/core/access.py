"""Per-ontology access control — config-driven read/write/none permission model.

This module provides a SCOPE-LOCK guard with NO user identity.  It prevents
accidental writes and reads to protected ontologies, enforced at the
GraphStore boundary via :class:`~ontorag.stores.access_wrapper.AccessControlledStore`.

Configuration
-------------
Set the ``ONTOLOGY_ACCESS`` environment variable to a comma-separated list of
``id:perm`` pairs::

    ONTOLOGY_ACCESS="poke:rw,shop:r,secret:none"

Accepted permission tokens:

* ``rw`` or ``w``  → :attr:`Permission.write`  (implies read)
* ``r``  or ``ro`` → :attr:`Permission.read`   (read-only)
* ``none`` or ``-``→ :attr:`Permission.none`   (no access)

Default behaviour (open)
------------------------
An ontology **not listed** in the policy gets full read+write access so that
a repository with no ``ONTOLOGY_ACCESS`` set behaves exactly as before this
feature was added.

``ontology=None`` (the legacy default graph) is treated as read+write unless
explicitly listed as ``default:…`` in the policy string.

Examples
--------
::

    export ONTOLOGY_ACCESS="poke:rw,shop:r,secret:none"

* ``poke``   → read + write
* ``shop``   → read-only; ``load_rdf`` raises :class:`AccessDenied`
* ``secret`` → no access; all methods raise :class:`AccessDenied`
* ``other``  → unlisted → read + write (default-open)
* ``None``   → legacy default graph → read + write (default-open)
"""

from __future__ import annotations

import logging
import os
from enum import Enum

from ontorag.core.ontology import validate_ontology_id

logger = logging.getLogger(__name__)

_ENV_VAR = "ONTOLOGY_ACCESS"

# Sentinel string used to specify policy for ontology=None (the legacy graph).
_DEFAULT_GRAPH_KEY = "default"


class Permission(str, Enum):
    """Access level for a single ontology scope.

    ``write`` implies read — callers should use :meth:`AccessPolicy.can_read`
    and :meth:`AccessPolicy.can_write` rather than comparing enum values
    directly.
    """

    none = "none"
    read = "read"
    write = "write"


def _parse_perm_token(token: str, raw_entry: str) -> Permission:
    """Map a permission token string to a :class:`Permission`.

    Args:
        token: Lower-cased token from the config string (e.g. ``"rw"``).
        raw_entry: The full entry (e.g. ``"poke:rw"``) — used in error messages.

    Returns:
        The matching :class:`Permission`.

    Raises:
        ValueError: If the token is not a recognised permission string.
    """
    if token in ("rw", "w"):
        return Permission.write
    if token in ("r", "ro"):
        return Permission.read
    if token in ("none", "-"):
        return Permission.none
    raise ValueError(
        f"Unknown permission token {token!r} in {raw_entry!r}. "
        "Expected one of: rw, w, r, ro, none, -"
    )


class AccessPolicy:
    """Parsed per-ontology access policy.

    Build via :meth:`from_env` or :meth:`from_string`.  Once constructed the
    object is immutable — do not modify ``_rules`` directly.

    Attributes:
        _rules: Mapping from ontology id (or ``"default"`` for ``None``) to
            :class:`Permission`.  An id absent from the map gets full
            read+write (open-by-default principle).
    """

    def __init__(self, rules: dict[str, Permission]) -> None:
        self._rules: dict[str, Permission] = dict(rules)

    # ── factory methods ────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> AccessPolicy | None:
        """Build a policy from the ``ONTOLOGY_ACCESS`` environment variable.

        Returns:
            A parsed :class:`AccessPolicy`, or ``None`` when the env var is
            absent or empty (signals "no policy — don't wrap").

        Raises:
            ValueError: If the env var contains a malformed entry.
        """
        raw = os.environ.get(_ENV_VAR, "").strip()
        if not raw:
            return None
        return cls.from_string(raw)

    @classmethod
    def from_string(cls, config: str) -> AccessPolicy:
        """Parse a comma-separated ``id:perm`` config string into a policy.

        Args:
            config: E.g. ``"poke:rw,shop:r,secret:none"``.  Leading/trailing
                whitespace around tokens is stripped.  The special id
                ``"default"`` sets the permission for ``ontology=None``.

        Returns:
            A new :class:`AccessPolicy`.

        Raises:
            ValueError: If any entry is malformed, has an invalid id, or uses
                an unknown permission token.
        """
        rules: dict[str, Permission] = {}

        for raw_entry in config.split(","):
            entry = raw_entry.strip()
            if not entry:
                continue

            parts = entry.split(":", maxsplit=1)
            if len(parts) != 2:
                raise ValueError(
                    f"Malformed ONTOLOGY_ACCESS entry {entry!r}. "
                    "Expected format: 'id:perm' (e.g. 'poke:rw')."
                )

            raw_id, raw_perm = parts[0].strip(), parts[1].strip().lower()

            # Validate the id — "default" is the reserved key for ontology=None.
            if raw_id != _DEFAULT_GRAPH_KEY:
                validate_ontology_id(raw_id)  # raises ValueError if invalid

            perm = _parse_perm_token(raw_perm, entry)
            rules[raw_id] = perm
            logger.debug("Access policy: %s → %s", raw_id, perm.value)

        return cls(rules)

    # ── permission checks ──────────────────────────────────────────────────────

    def _permission_for(self, ontology: str | None) -> Permission:
        """Return the effective :class:`Permission` for an ontology scope.

        Args:
            ontology: Validated ontology id or ``None`` for the legacy default
                graph.

        Returns:
            The explicit permission if listed; otherwise :attr:`Permission.write`
            (open-by-default).
        """
        key = _DEFAULT_GRAPH_KEY if ontology is None else ontology
        return self._rules.get(key, Permission.write)

    def can_read(self, ontology: str | None) -> bool:
        """Return ``True`` if read access is granted for the given ontology scope.

        ``write`` implies read, so an ontology with :attr:`Permission.write`
        is readable.

        Args:
            ontology: Ontology id or ``None`` for the legacy default graph.

        Returns:
            ``True`` when the permission is ``read`` or ``write``.
        """
        perm = self._permission_for(ontology)
        return perm in (Permission.read, Permission.write)

    def can_write(self, ontology: str | None) -> bool:
        """Return ``True`` if write access is granted for the given ontology scope.

        Args:
            ontology: Ontology id or ``None`` for the legacy default graph.

        Returns:
            ``True`` only when the permission is :attr:`Permission.write`.
        """
        return self._permission_for(ontology) is Permission.write
