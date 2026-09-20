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
  ``find_related``, ``property_path_closure``, ``dump_graph`` — check
  :meth:`~ontorag.core.access.AccessPolicy.can_read` for the given ontology,
  **including** ``ontology=None`` (the union/legacy view): a ``default:``
  policy entry now applies to union reads too. When no ``default`` entry is
  set, ``ontology=None`` remains open **unless** some other ontology is
  explicitly denied read — see the fail-closed union guard below.
* **Fail-closed union guard** — a union read (``ontology=None``) is blocked
  outright whenever the policy denies read for *any* explicitly-listed
  ontology, even if the union scope itself is open. This is a deliberate
  over-restriction: there is currently no way to rewrite a union read to
  "only the readable ontologies" (that needs a live-verified, per-backend
  dataset-restriction change — see
  :meth:`~ontorag.core.access.AccessPolicy.has_read_restricted_ontology`),
  so allowing an open union through would leak the denied ontology's data.
  It also blocks legitimate union queries that only wanted the allowed
  ontologies — an accepted interim trade-off, not the final fix (roadmap
  §3.2's "union of allowed ontologies only").
* ``query_pattern`` (Layer 2 JSON DSL) has no ontology-scoped parameter and
  the SPARQL/Cypher translators run against the full/union dataset with no
  named-graph restriction, so it is guarded as an ``ontology=None`` read —
  including the fail-closed union guard above. This closes the total
  bypass; per-pattern ontology scoping would require a
  ``PatternQuery.ontology`` field plus changes in every backend translator
  and is out of scope here.
* **Capability methods with an ``ontology`` parameter** — ``search_text``,
  ``find_similar``, ``sameas_closure`` (read); ``build_embeddings``,
  ``put_bayes_network``, ``clear_bayes_network``, ``put_causal_model``,
  ``clear_causal_model`` (write); ``get_bayes_network``, ``get_causal_model``
  (read) — are not defined as explicit methods on this class (some backends
  don't implement all of them), but ``__getattr__`` wraps them with the same
  read/write guard, keyed on the ``ontology=`` keyword argument callers
  already pass. Looking the method up on the wrapped store first means an
  unsupported capability still raises :class:`AttributeError`, so the
  existing ``getattr(store, name, None) is None`` → HTTP 501 pattern used by
  routes is preserved.
* **Everything else** — ``status``, ``aclose``, and any future capability
  method without an ``ontology`` parameter — is delegated transparently,
  unguarded. This ensures the wrapper never silently blocks unrelated calls.
* **Audit** — every guard decision (allow or deny) is logged via the
  ``ontorag.access.audit`` logger with the method name, ontology scope,
  mode (read/write), and decision. This is a minimal subset of the roadmap's
  §3.3 audit event (no ``request_id``/``subject``/``tenant`` — those require
  ``RequestContext``, which is out of scope; see ``CLAUDE.md`` "Open
  questions").
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

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

if TYPE_CHECKING:
    from rdflib import Graph

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("ontorag.access.audit")

# Capability methods (not defined explicitly below, dispatched via
# __getattr__) that carry an `ontology=` kwarg and must be guarded.
_READ_GUARDED_CAPABILITIES = frozenset(
    {
        "search_text",
        "find_similar",
        "sameas_closure",
        "get_bayes_network",
        "get_causal_model",
    }
)
_WRITE_GUARDED_CAPABILITIES = frozenset(
    {
        "build_embeddings",
        "put_bayes_network",
        "clear_bayes_network",
        "put_causal_model",
        "clear_causal_model",
    }
)


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

    def _audit(self, method: str, ontology: str | None, mode: str, decision: str) -> None:
        """Log a structured audit line for one access-control decision.

        Args:
            method: Guarded method name.
            ontology: The ontology scope being accessed, or ``None`` (union).
            mode: ``"read"`` or ``"write"``.
            decision: ``"allow"`` or ``"deny"``.
        """
        log = audit_logger.warning if decision == "deny" else audit_logger.info
        log(
            "access_decision method=%s ontology=%s mode=%s decision=%s",
            method,
            ontology,
            mode,
            decision,
            extra={
                "audit_method": method,
                "audit_ontology": ontology,
                "audit_mode": mode,
                "audit_decision": decision,
            },
        )

    def _require_read(self, ontology: str | None, method: str) -> None:
        """Raise :class:`AccessDenied` when read is denied for *ontology*.

        ``ontology=None`` (the union/legacy default graph) is checked against
        the policy's ``default`` entry just like any other scope — it is only
        open when no ``default`` entry was configured (open-by-default).

        Fail-closed union guard: even when the ``default`` scope itself is
        open, a union read is blocked outright if the policy denies read for
        *any* other ontology. There is currently no way to rewrite a union
        read to "only the readable ontologies" (see
        :meth:`~ontorag.core.access.AccessPolicy.has_read_restricted_ontology`
        for why), so allowing the union through would leak the denied
        ontology's data. This is a deliberate over-restriction — it also
        blocks legitimate union queries that only wanted the allowed
        ontologies — documented as an interim measure pending a verified,
        per-backend filtered-union implementation
        (``docs/design/agentic-governed-rag-roadmap.ko.md`` §3.2).

        Args:
            ontology: The ontology id being accessed, or ``None``.
            method: Method name used in the error message.

        Raises:
            AccessDenied: If the policy denies read access, or if
                ``ontology`` is ``None`` and the policy denies read for some
                other explicitly-listed ontology (fail-closed union guard).
        """
        if ontology is None and self._policy.has_read_restricted_ontology():
            self._audit(method, ontology, "read", "deny")
            raise AccessDenied(
                f"{method}: union read (ontology=None) is blocked because the "
                "active policy denies read for at least one ontology, and "
                "union reads cannot yet be filtered to only the readable "
                "ontologies. Query a specific ontology instead."
            )
        if self._policy.can_read(ontology):
            self._audit(method, ontology, "read", "allow")
            return
        self._audit(method, ontology, "read", "deny")
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
        if self._policy.can_write(ontology):
            self._audit(method, ontology, "write", "allow")
            return
        self._audit(method, ontology, "write", "deny")
        raise AccessDenied(
            f"{method}: write access denied for ontology {ontology!r}. "
            "Check ONTOLOGY_ACCESS configuration."
        )

    # ── transparent delegation (unguarded) ────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        """Delegate any attribute not defined here to the wrapped store.

        Looking the attribute up on the wrapped store first means an
        unsupported capability raises :class:`AttributeError` exactly as it
        would without this wrapper, preserving the
        ``getattr(store, name, None) is None`` → HTTP 501 pattern used by
        routes. Capability methods in :data:`_READ_GUARDED_CAPABILITIES` /
        :data:`_WRITE_GUARDED_CAPABILITIES` (``search_text``,
        ``find_similar``, ``build_embeddings``, ``sameas_closure``, the
        Bayesian/causal get/put/clear methods, …) are additionally wrapped
        with the same read/write guard used by the explicit methods below,
        keyed on the ``ontology=`` keyword argument. Everything else (e.g.
        ``status``, ``aclose``) passes through unguarded.

        Args:
            name: Attribute name.

        Returns:
            The attribute from the wrapped store, or a guarded wrapper around
            it when *name* is a known ontology-scoped capability method.
        """
        attr = getattr(self._store, name)
        if callable(attr) and name in _READ_GUARDED_CAPABILITIES:
            return self._guarded_capability(name, attr, mode="read")
        if callable(attr) and name in _WRITE_GUARDED_CAPABILITIES:
            return self._guarded_capability(name, attr, mode="write")
        return attr

    def _guarded_capability(self, name: str, attr: Any, *, mode: Literal["read", "write"]) -> Any:
        """Wrap a capability method with an ontology-scoped access guard.

        Args:
            name: Capability method name, used in audit/error messages.
            attr: The bound method on the wrapped store.
            mode: ``"read"`` or ``"write"`` — which guard to apply.

        Returns:
            An async callable with the same signature as *attr* that checks
            the policy before delegating.
        """
        require = self._require_read if mode == "read" else self._require_write

        async def _wrapped(*args: Any, **kwargs: Any) -> Any:
            require(kwargs.get("ontology"), name)
            return await attr(*args, **kwargs)

        return _wrapped

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
        graph: Graph | None = None,
    ) -> LoadResult:
        """Guard write access then delegate to the wrapped store.

        Args:
            path: Local file path.
            mode: Load mode (schema / data / auto).
            replace: Replace existing data graph if ``True``.
            ontology: Target ontology scope.
            graph: Optional pre-parsed RDF graph (skips re-parsing *path*).

        Returns:
            Load result from the wrapped store.

        Raises:
            AccessDenied: If the policy denies write for *ontology*.
        """
        self._require_write(ontology, "load_rdf")
        return await self._store.load_rdf(
            path, mode=mode, replace=replace, ontology=ontology, graph=graph
        )

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
        """Guard as an ``ontology=None`` (union) read, then delegate.

        ``PatternQuery`` has no ontology scope field and backend translators
        run it against the full/union dataset, so — absent per-pattern
        scoping (out of scope; would need a ``PatternQuery.ontology`` field
        plus every backend translator updated) — the closest correct guard is
        the same one applied to any other union read: it is blocked only when
        the policy has an explicit ``default:none``/``default:r`` entry
        that denies it.

        Args:
            query: JSON DSL query.

        Returns:
            Query results.

        Raises:
            AccessDenied: If the policy denies read for the union scope.
        """
        self._require_read(None, "query_pattern")
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
