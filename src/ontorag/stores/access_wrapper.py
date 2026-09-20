"""Access-control wrapper for :class:`~ontorag.stores.base.GraphStore`.

Wraps any concrete store with a :class:`~ontorag.core.access.AccessPolicy` and
enforces read/write guards at the GraphStore boundary.  Store adapters
(``fuseki.py``, ``neo4j.py``) and the GraphStore Protocol are untouched.

Design decisions
----------------
* **Write methods** — ``load_rdf``, ``clear_graph``, and the triple mutation
  methods — check :meth:`~ontorag.core.access.AccessPolicy.can_write` before
  delegating.
* **Read methods that accept an ``ontology`` parameter** — ``get_schema``,
  ``get_class_detail``, ``find_entities``, ``describe_entity``,
  ``count_entities``, ``aggregate``, ``traverse``, ``find_path``,
  ``find_related``, ``property_path_closure`` — go through
  :meth:`_read_guard`: :meth:`~ontorag.core.access.AccessPolicy.can_read`
  for the given ontology, **including** ``ontology=None`` (the union/legacy
  view): a ``default:`` policy entry now applies to union reads too. When
  no ``default`` entry is set, ``ontology=None`` remains open **unless**
  some other ontology is read-restricted — see the filtered/fail-closed
  union handling below. ``dump_graph`` and ``query_pattern`` accept
  ``ontology``/act like a union read too but use plain
  :meth:`_require_read` instead — see their docstrings for why they never
  touch multiple ontologies' graphs at all, so filtering them would be a
  no-op.
* **Filtered union read (Fuseki, pure-SPARQL methods only)** — when a union
  read (``ontology=None``) is requested and something in the policy might
  restrict it (:meth:`~ontorag.core.access.AccessPolicy.has_read_restricted_ontology`
  or :attr:`~ontorag.core.access.AccessPolicy.deny_by_default`),
  :meth:`_read_guard` looks for the wrapped store's
  ``list_ontologies``/``restrict_default_graph`` capability pair
  (implemented by :class:`~ontorag.stores.fuseki.FusekiStore` — see its
  docstrings for the live-verified mechanism: the SPARQL 1.1 protocol's
  ``default-graph-uri`` parameter, which overrides
  ``tdb2:unionDefaultGraph`` with the RDF merge of exactly the readable
  ontologies' graphs, preserving multiplicity — not ``GRAPH ?g { }``
  iteration, which would risk reintroducing this repo's already-fixed
  union-graph duplicate-row bugs). When present, the query proceeds
  filtered to only the readable ontologies — the actual roadmap §3.2 fix.
  Only wired up for the methods above; ``dump_graph`` (GSP GET, a separate
  HTTP endpoint from ``_sparql_select``) and ``query_pattern`` (hardcodes
  ``GRAPH <urn:ontorag:data>`` — verified live it never reads a
  per-ontology graph at all) structurally cannot leak across ontologies,
  so there's nothing for this mechanism to filter for them.
* **Fail-closed union guard (fallback)** — when the filtering capability is
  absent (Neo4j, FalkorDB — not live-verified this round, so not wired up;
  a documented backend-parity gap) or the union scope itself
  (``ontology=None``) is explicitly denied, the union read is blocked
  outright instead of silently leaking a restricted ontology's data. This
  also still applies to ``dump_graph``, ``query_pattern``, and capability
  methods that can't be filtered even on Fuseki — see below.
* **Capability methods with an ``ontology`` parameter** — ``search_text``,
  ``find_similar``, ``sameas_closure`` (read); ``put_bayes_network``,
  ``clear_bayes_network``, ``put_causal_model``, ``clear_causal_model``
  (write); ``get_bayes_network``, ``get_causal_model`` (read) — are not
  defined as explicit methods on this class (some backends don't implement
  all of them), but ``__getattr__`` wraps them with the appropriate guard for
  either positional or keyword ontology arguments. ``build_embeddings`` is
  also dispatched this way, but checks both read and write access because an
  unscoped rebuild reads all ontology text before replacing a shared index.
  These stay on the fail-closed union path even on Fuseki: ``find_similar``
  is backed by Qdrant, not SPARQL, so ``restrict_default_graph`` would have
  no effect on it at all — filtering only every method it verifiably covers,
  not everything with an ``ontology`` parameter, is the point. Looking the
  method up on the wrapped store first means an unsupported capability still
  raises :class:`AttributeError`, so the existing
  ``getattr(store, name, None) is None`` → HTTP 501 pattern used by routes
  is preserved.
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
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal

from ontorag.core.access import AccessPolicy
from ontorag.core.ontology import validate_ontology_id
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
        "put_bayes_network",
        "clear_bayes_network",
        "put_causal_model",
        "clear_causal_model",
    }
)

# Ontology's position in each capability signature.  Capability methods are
# forwarded through ``__getattr__``, so their positional calls must receive the
# same policy enforcement as their keyword calls.
_CAPABILITY_ONTOLOGY_POSITION = {
    "build_embeddings": 2,
    "search_text": 3,
    "find_similar": 4,
    "sameas_closure": 1,
    "get_bayes_network": 0,
    "get_causal_model": 0,
    "put_bayes_network": 1,
    "clear_bayes_network": 0,
    "put_causal_model": 1,
    "clear_causal_model": 0,
}


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
            decision: ``"allow"``, ``"deny"``, or ``"allow-filtered"`` (a
                union read that was allowed but restricted to a subset of
                readable ontologies — see :meth:`_read_guard`).
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
        ontology = validate_ontology_id(ontology)
        if ontology is None and (
            self._policy.has_read_restricted_ontology() or self._policy.deny_by_default
        ):
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
        ontology = validate_ontology_id(ontology)
        if self._policy.can_write(ontology):
            self._audit(method, ontology, "write", "allow")
            return
        self._audit(method, ontology, "write", "deny")
        raise AccessDenied(
            f"{method}: write access denied for ontology {ontology!r}. "
            "Check ONTOLOGY_ACCESS configuration."
        )

    @asynccontextmanager
    async def _read_guard(self, ontology: str | None, method: str) -> AsyncIterator[None]:
        """Guard a pure-SPARQL read call, filtering the union when possible.

        For an explicit scope (``ontology is not None``) this is exactly
        :meth:`_require_read` — explicit scopes are already precise, nothing
        to filter.

        For a union read (``ontology is None``):

        * If nothing in the policy could possibly restrict it (no
          read-denied ontology and :attr:`~ontorag.core.access.AccessPolicy.deny_by_default`
          is off), falls back to :meth:`_require_read`'s plain
          ``can_read(None)`` check — fast path, no store round-trip.
        * If something might restrict it, the union scope itself is
          readable, and the wrapped store exposes the
          ``list_ontologies``/``restrict_default_graph`` capability pair
          (Fuseki only, this round — see ``fuseki.py``), enumerates the
          store's actual ontologies, computes which are readable, and
          delegates *inside* ``restrict_default_graph(...)`` so the query is
          filtered to exactly those — the real roadmap §3.2 fix, live-verified.
        * Otherwise (capability absent, or ``ontology=None`` itself is
          denied) falls back to :meth:`_require_read`'s fail-closed
          behavior — blocks the union read outright rather than risk a leak.

        Args:
            ontology: The ontology id being accessed, or ``None`` (union).
            method: Method name used in error messages and audit records.

        Yields:
            None. The caller's store delegation must happen inside this
            context so a filtered union call runs under the active
            ``restrict_default_graph`` restriction.

        Raises:
            AccessDenied: Per :meth:`_require_read`'s conditions, when
                filtering isn't applicable or isn't available.
        """
        if ontology is not None:
            self._require_read(ontology, method)
            yield
            return

        if not (self._policy.has_read_restricted_ontology() or self._policy.deny_by_default):
            self._require_read(ontology, method)
            yield
            return

        list_ontologies = getattr(self._store, "list_ontologies", None)
        restrict = getattr(self._store, "restrict_default_graph", None)
        if self._policy.can_read(None) and list_ontologies is not None and restrict is not None:
            all_ids = await list_ontologies()
            survivors = frozenset(oid for oid in all_ids if self._policy.can_read(oid))
            self._audit(method, None, "read", "allow-filtered")
            async with restrict(survivors, include_legacy=True):
                yield
            return

        self._require_read(ontology, method)
        yield

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
        whether ontology is passed positionally or by keyword. Everything else (e.g.
        ``status``, ``aclose``) passes through unguarded.

        Args:
            name: Attribute name.

        Returns:
            The attribute from the wrapped store, or a guarded wrapper around
            it when *name* is a known ontology-scoped capability method.
        """
        attr = getattr(self._store, name)
        if callable(attr) and name == "build_embeddings":
            return self._guarded_embedding_capability(attr)
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
            ontology = kwargs.get("ontology")
            if "ontology" not in kwargs:
                ontology_position = _CAPABILITY_ONTOLOGY_POSITION[name]
                if len(args) > ontology_position:
                    ontology = args[ontology_position]
            require(ontology, name)
            return await attr(*args, **kwargs)

        return _wrapped

    def _guarded_embedding_capability(self, attr: Any) -> Any:
        """Guard embedding rebuilds as reads plus writes without inventing a
        capability on stores that do not implement it.

        The unscoped rebuild reads every ontology and replaces a shared index.
        It therefore cannot safely proceed if a named ontology is hidden or
        read-only, even if the ``default`` scope is writable.
        """

        async def _wrapped(*args: Any, **kwargs: Any) -> Any:
            ontology = kwargs.get("ontology")
            if "ontology" not in kwargs and len(args) > _CAPABILITY_ONTOLOGY_POSITION[
                "build_embeddings"
            ]:
                ontology = args[_CAPABILITY_ONTOLOGY_POSITION["build_embeddings"]]

            if ontology is None and (
                self._policy.has_write_restricted_ontology() or self._policy.deny_by_default
            ):
                self._audit("build_embeddings", None, "write", "deny")
                raise AccessDenied(
                    "build_embeddings: union rebuild is blocked because the active policy "
                    "denies writes for at least one ontology. Rebuild a specific writable "
                    "ontology instead."
                )
            self._require_read(ontology, "build_embeddings")
            self._require_write(ontology, "build_embeddings")
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

    async def assert_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        object_is_uri: bool = False,
        ontology: str | None = None,
    ) -> None:
        """Guard a single-triple write then delegate."""
        self._require_write(ontology, "assert_triple")
        await self._store.assert_triple(
            subject,
            predicate,
            obj,
            object_is_uri=object_is_uri,
            ontology=ontology,
        )

    async def retract_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        object_is_uri: bool = False,
        ontology: str | None = None,
    ) -> None:
        """Guard a single-triple retraction then delegate."""
        self._require_write(ontology, "retract_triple")
        await self._store.retract_triple(
            subject,
            predicate,
            obj,
            object_is_uri=object_is_uri,
            ontology=ontology,
        )

    async def assert_triples(
        self,
        triples: list[tuple[str, str, str, bool]],
        *,
        ontology: str | None = None,
    ) -> int:
        """Guard a batch triple write then delegate."""
        self._require_write(ontology, "assert_triples")
        return await self._store.assert_triples(triples, ontology=ontology)

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
        async with self._read_guard(ontology, "get_schema"):
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
        async with self._read_guard(ontology, "get_class_detail"):
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
        async with self._read_guard(ontology, "find_entities"):
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
        async with self._read_guard(ontology, "describe_entity"):
            return await self._store.describe_entity(
                uri, predicates=predicates, ontology=ontology
            )

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
        async with self._read_guard(ontology, "count_entities"):
            return await self._store.count_entities(
                class_uri, filters=filters, ontology=ontology
            )

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
        async with self._read_guard(ontology, "aggregate"):
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
        async with self._read_guard(ontology, "traverse"):
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
        async with self._read_guard(ontology, "find_path"):
            return await self._store.find_path(
                uri_a, uri_b, max_depth=max_depth, ontology=ontology
            )

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
        async with self._read_guard(ontology, "find_related"):
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

        Deliberately uses :meth:`_require_read` (fail-closed only), not
        :meth:`_read_guard`: ``PatternQuery`` has no ontology field, and
        Fuseki's translator (``pattern_to_sparql``) hardcodes
        ``GRAPH <urn:ontorag:data>`` — verified live — so ``query_pattern``
        only ever reads the single legacy data graph, never a real union
        across per-ontology graphs. There is nothing for
        ``restrict_default_graph`` to filter (it would be a no-op detour,
        like ``dump_graph``); the only real gap is that it was previously
        unguarded by policy at all, which the plain ``can_read(None)`` check
        already closes.

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
        async with self._read_guard(ontology, "property_path_closure"):
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

        Deliberately uses :meth:`_require_read` (fail-closed only), not
        :meth:`_read_guard`: Fuseki's ``dump_graph`` fetches a specific
        named graph pair via GSP GET (``_gsp_get``, a different HTTP
        endpoint from ``_sparql_select``) — even with ``ontology=None`` it
        only ever reads the legacy ``urn:ontorag:schema``/``:data`` graphs,
        never a real ambient union of every ontology. The filtered-union
        restriction (``default-graph-uri``, honored only by
        ``_sparql_select``) would have no effect on it, so routing it
        through the filtering path would be a silent no-op that looks like
        it did something. The fail-closed guard over-blocks it exactly like
        ``get_bayes_network``/``get_causal_model`` (accepted safe false
        positive, see ``AccessPolicy.has_read_restricted_ontology``).

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
