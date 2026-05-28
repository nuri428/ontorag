"""Access-control wrapper for :class:`~ontorag.stores.base.GraphStore`.

Wraps any concrete store with a :class:`~ontorag.core.access.AccessPolicy` and
enforces read/write guards at the GraphStore boundary.  Store adapters
(``fuseki.py``, ``neo4j.py``) and the GraphStore Protocol are untouched.

Design decisions
----------------
* **Write methods** — ``load_rdf`` and ``clear_graph`` — check
  :meth:`~ontorag.core.access.AccessPolicy.can_write` before delegating.
* **Read methods that accept an ``ontology`` parameter** — ``get_schema``,
  ``get_class_detail``, ``find_entities``, ``describe_entity``,
  ``count_entities``, ``aggregate``, ``traverse``, ``find_path``,
  ``find_related``, ``query_pattern``, ``property_path_closure``,
  ``dump_graph`` — check :meth:`~ontorag.core.access.AccessPolicy.can_read`
  when an explicit (non-``None``) ontology is given.  ``ontology=None`` (the
  union/legacy view) is always allowed through so callers that have never set
  an explicit scope are unaffected.
* **Everything else** — capability methods such as ``search_text``,
  ``find_similar``, ``build_embeddings``, ``status``, ``aclose``, and any
  future methods — is delegated transparently via ``__getattr__``.  This
  ensures the wrapper never silently blocks unrelated calls.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from ontorag.core.access import AccessPolicy
from ontorag.stores.base import (
    AggFunc,
    AggregateResult,
    ClassDetail,
    EntityFilter,
    EntityResult,
    LoadResult,
    PatternQuery,
    QueryResult,
    SchemaResult,
    StoreStatus,
    TraversalDirection,
    TraversalResult,
)

logger = logging.getLogger(__name__)


class AccessDenied(PermissionError):
    """Raised when an operation is blocked by the active :class:`AccessPolicy`.

    Inherits from :exc:`PermissionError` so callers that catch the standard
    exception hierarchy are also covered.
    """


class AccessControlledStore:
    """Transparent GraphStore wrapper that enforces per-ontology access control.

    This class satisfies the :class:`~ontorag.stores.base.GraphStore` protocol
    (``runtime_checkable`` structural matching).  All guarded methods are
    defined explicitly; unguarded capability methods are forwarded via
    ``__getattr__``.

    Args:
        store: The wrapped concrete store (FusekiStore, Neo4jStore, …).
        policy: The parsed :class:`~ontorag.core.access.AccessPolicy` to apply.

    Example::

        store = create_store()              # concrete adapter
        policy = AccessPolicy.from_env()   # None when env var is unset
        if policy is not None:
            store = AccessControlledStore(store, policy)
    """

    def __init__(self, store: Any, policy: AccessPolicy) -> None:
        self._store = store
        self._policy = policy

    # ── helpers ────────────────────────────────────────────────────────────────

    def _require_read(self, ontology: str | None, method: str) -> None:
        """Raise :class:`AccessDenied` when read is denied for *ontology*.

        ``ontology=None`` (the union/legacy default graph) is always allowed
        through — callers that never set an explicit scope are not affected.

        Args:
            ontology: The ontology id being accessed, or ``None``.
            method: Method name used in the error message.

        Raises:
            AccessDenied: If the policy denies read access.
        """
        if ontology is not None and not self._policy.can_read(ontology):
            raise AccessDenied(
                f"{method}: read access denied for ontology {ontology!r}. "
                "Check ONTOLOGY_ACCESS configuration."
            )

    def _require_write(self, ontology: str | None, method: str) -> None:
        """Raise :class:`AccessDenied` when write is denied for *ontology*.

        Args:
            ontology: The ontology id being written to, or ``None``.
            method: Method name used in the error message.

        Raises:
            AccessDenied: If the policy denies write access.
        """
        if not self._policy.can_write(ontology):
            raise AccessDenied(
                f"{method}: write access denied for ontology {ontology!r}. "
                "Check ONTOLOGY_ACCESS configuration."
            )

    # ── transparent delegation (unguarded) ────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        """Delegate any attribute not defined here to the wrapped store.

        This catches capability methods (``search_text``, ``find_similar``,
        ``build_embeddings``, …) as well as any future methods added to the
        protocol — they all pass through without an access check.

        Args:
            name: Attribute name.

        Returns:
            The attribute from the wrapped store.
        """
        return getattr(self._store, name)

    # ── store management (pass-through, no check) ─────────────────────────────

    async def status(self) -> StoreStatus:
        """Delegate to the wrapped store — no access check.

        Returns:
            Current store status.
        """
        return await self._store.status()

    async def aclose(self) -> None:
        """Delegate to the wrapped store — no access check."""
        await self._store.aclose()

    # ── WRITE methods ─────────────────────────────────────────────────────────

    async def load_rdf(
        self,
        path: str,
        mode: Literal["schema", "data", "auto"] = "auto",
        replace: bool = False,
        ontology: str | None = None,
    ) -> LoadResult:
        """Guard write access then delegate to the wrapped store.

        Args:
            path: Local file path.
            mode: Load mode (schema / data / auto).
            replace: Replace existing data graph if ``True``.
            ontology: Target ontology scope.

        Returns:
            Load result from the wrapped store.

        Raises:
            AccessDenied: If the policy denies write for *ontology*.
        """
        self._require_write(ontology, "load_rdf")
        return await self._store.load_rdf(path, mode=mode, replace=replace, ontology=ontology)

    async def clear_graph(
        self,
        target: Literal["schema", "data", "all"],
        ontology: str | None = None,
    ) -> dict[str, int]:
        """Guard write access then delegate to the wrapped store.

        Args:
            target: Which graph(s) to clear.
            ontology: Target ontology scope.

        Returns:
            Mapping of graph name → triple count removed.

        Raises:
            AccessDenied: If the policy denies write for *ontology*.
        """
        self._require_write(ontology, "clear_graph")
        return await self._store.clear_graph(target, ontology=ontology)

    # ── READ methods (ontology-scoped) ────────────────────────────────────────

    async def get_schema(self, ontology: str | None = None) -> SchemaResult:
        """Guard read access then delegate.

        Args:
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Compact schema overview.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "get_schema")
        return await self._store.get_schema(ontology=ontology)

    async def get_class_detail(
        self, class_uri: str, ontology: str | None = None
    ) -> ClassDetail:
        """Guard read access then delegate.

        Args:
            class_uri: Full URI or prefixed name of the class.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Full class detail.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "get_class_detail")
        return await self._store.get_class_detail(class_uri, ontology=ontology)

    async def find_entities(
        self,
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[EntityResult]:
        """Guard read access then delegate.

        Args:
            class_uri: Class URI.
            filters: Optional filter conditions.
            limit: Maximum results.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Matching entities.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "find_entities")
        return await self._store.find_entities(
            class_uri, filters=filters, limit=limit, ontology=ontology
        )

    async def describe_entity(
        self,
        uri: str,
        predicates: list[str] | None = None,
        ontology: str | None = None,
    ) -> EntityResult:
        """Guard read access then delegate.

        Args:
            uri: Entity URI.
            predicates: Optional predicate filter.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Entity with properties.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "describe_entity")
        return await self._store.describe_entity(uri, predicates=predicates, ontology=ontology)

    async def count_entities(
        self,
        class_uri: str,
        filters: list[EntityFilter] | None = None,
        ontology: str | None = None,
    ) -> int:
        """Guard read access then delegate.

        Args:
            class_uri: Class URI.
            filters: Optional filter conditions.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Count of matching instances.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "count_entities")
        return await self._store.count_entities(class_uri, filters=filters, ontology=ontology)

    async def aggregate(
        self,
        class_uri: str,
        group_by: str,
        agg: AggFunc = AggFunc.count,
        ontology: str | None = None,
    ) -> list[AggregateResult]:
        """Guard read access then delegate.

        Args:
            class_uri: Class to aggregate over.
            group_by: Property URI to group by.
            agg: Aggregation function.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Aggregated results.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "aggregate")
        return await self._store.aggregate(class_uri, group_by, agg=agg, ontology=ontology)

    async def traverse(
        self,
        start_uri: str,
        predicate: str | None = None,
        max_depth: int = 2,
        direction: TraversalDirection = TraversalDirection.outgoing,
        ontology: str | None = None,
    ) -> TraversalResult:
        """Guard read access then delegate.

        Args:
            start_uri: Starting entity URI.
            predicate: Predicate to follow.
            max_depth: Maximum traversal depth.
            direction: Traversal direction.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Traversal result.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "traverse")
        return await self._store.traverse(
            start_uri,
            predicate=predicate,
            max_depth=max_depth,
            direction=direction,
            ontology=ontology,
        )

    async def find_path(
        self,
        uri_a: str,
        uri_b: str,
        max_depth: int = 4,
        ontology: str | None = None,
    ) -> TraversalResult:
        """Guard read access then delegate.

        Args:
            uri_a: Starting entity URI.
            uri_b: Target entity URI.
            max_depth: Maximum path length.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Shortest path result.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "find_path")
        return await self._store.find_path(uri_a, uri_b, max_depth=max_depth, ontology=ontology)

    async def find_related(
        self,
        class_uri_a: str,
        predicate: str,
        class_uri_b: str,
        filters_a: list[EntityFilter] | None = None,
        filters_b: list[EntityFilter] | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[dict[str, Any]]:
        """Guard read access then delegate.

        Args:
            class_uri_a: Subject class URI.
            predicate: Connecting predicate.
            class_uri_b: Object class URI.
            filters_a: Optional filters for subjects.
            filters_b: Optional filters for objects.
            limit: Maximum result pairs.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            Matching entity pairs.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "find_related")
        return await self._store.find_related(
            class_uri_a,
            predicate,
            class_uri_b,
            filters_a=filters_a,
            filters_b=filters_b,
            limit=limit,
            ontology=ontology,
        )

    async def query_pattern(self, query: PatternQuery) -> QueryResult:
        """Delegate directly — PatternQuery has no ontology scope parameter.

        The Layer 2 DSL does not carry an ontology scope, so no access check
        is applied here.  If you need scoped pattern queries, pass
        explicit GRAPH-scoped triples in the pattern.

        Args:
            query: JSON DSL query.

        Returns:
            Query results.
        """
        return await self._store.query_pattern(query)

    async def property_path_closure(
        self,
        predicate_uri: str,
        start_uri: str | None = None,
        start_label: str | None = None,
        start_class_uri: str | None = None,
        limit: int = 100,
        ontology: str | None = None,
    ) -> list[dict[str, Any]]:
        """Guard read access then delegate.

        Args:
            predicate_uri: Transitive predicate to follow.
            start_uri: Instance URI start mode.
            start_label: Label lookup start mode.
            start_class_uri: Class-wide closure or disambiguation.
            limit: Max entities to return.
            ontology: Ontology scope, or ``None`` for union.

        Returns:
            List of reachable entity dicts.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "property_path_closure")
        return await self._store.property_path_closure(
            predicate_uri,
            start_uri=start_uri,
            start_label=start_label,
            start_class_uri=start_class_uri,
            limit=limit,
            ontology=ontology,
        )

    async def dump_graph(
        self,
        target: Literal["schema", "data", "all"],
        fmt: Literal["ttl", "json", "jsonl", "xlsx"] = "ttl",
        ontology: str | None = None,
    ) -> bytes:
        """Guard read access then delegate.

        Args:
            target: Which graph(s) to export.
            fmt: Serialisation format.
            ontology: Ontology scope, or ``None`` for default/legacy.

        Returns:
            Serialised bytes.

        Raises:
            AccessDenied: If the policy denies read for an explicit *ontology*.
        """
        self._require_read(ontology, "dump_graph")
        return await self._store.dump_graph(target, fmt=fmt, ontology=ontology)
