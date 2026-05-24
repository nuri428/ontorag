from __future__ import annotations

"""BM25 full-text search mixin for Neo4jStore (B2 + B3).

Implements:
  - _ensure_fulltext_index(): discover string-valued properties on :Resource
    nodes, validate each key with _safe_rel, create/recreate the named index.
  - search_text(): CALL db.index.fulltext.queryNodes with a bound $search_query
    parameter and optional subClassOf-aware class_uri filter. Only an ONLINE
    index is queried (POPULATING/FAILED → []); rows are over-fetched then
    deduplicated and sliced to `limit` to avoid multi-rdf:type under-delivery.

This is a *capability*, not part of the GraphStore protocol — backends that
do not expose search_text receive a 501 via the route's getattr guard.
"""

import logging
from typing import TYPE_CHECKING, Any

from ontorag.core.cypher import _safe_rel
from ontorag.stores._neo4j_values import first_scalar
from ontorag.stores.base import SearchHit

if TYPE_CHECKING:
    from ontorag.stores.neo4j import Neo4jStore

logger = logging.getLogger(__name__)

# Index name used across the lifecycle: creation, property-set comparison, drop.
_FULLTEXT_INDEX_NAME = "ontorag_fulltext"

# Hard cap on subClassOf traversal depth (mirrors _Neo4jTraversalMixin._MAX_DEPTH_HARD).
_MAX_DEPTH_HARD = 6


class _Neo4jSearchMixin:
    """BM25 full-text search capability mixed into Neo4jStore.

    Not part of the GraphStore protocol — exposed as an optional capability.
    The MCP route guards with ``getattr(store, "search_text", None)``.
    """

    # Provided by Neo4jStore at runtime
    _run: Any
    _run_write: Any
    _shorten: Any
    _shorten_prefixed: Any
    _expand: Any
    _ensure_prefix_map: Any

    # Cached "index is ONLINE and queryable" flag — lets search_text skip the
    # SHOW INDEXES round-trip once the index is known good.  Reset to False on
    # any drop/recreate or when a non-ONLINE state is observed.  Defaults to
    # None ("unknown") so the first search verifies against the DB.
    _fulltext_index_ready: bool | None = None

    # ── B2 — index lifecycle ──────────────────────────────────────────────────

    async def _discover_text_property_keys(self: "Neo4jStore") -> list[str]:
        """Discover string / string-array property keys on :Resource nodes.

        Scans all :Resource nodes, collects keys whose values are strings or
        string-valued lists, validates each through _safe_rel, and always
        ensures rdfs__label is first when present.

        Returns:
            Sorted list of validated n10s-shortened property keys, with
            ``rdfs__label`` first (if present).
        """
        # Query: sample up to 1000 nodes; collect all distinct string-valued keys.
        # UNWIND keys(n) yields the raw property key (n10s-shortened form).
        # valueType() is available in Neo4j 5+ and reliably distinguishes
        # string scalars from string arrays vs. numeric/boolean arrays.
        rows = await self._run(
            """
            MATCH (n:Resource)
            WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig
            WITH n LIMIT 1000
            UNWIND keys(n) AS k
            WITH k, n[k] AS v
            WHERE k <> 'uri'
              AND v IS NOT NULL
              AND (
                    valueType(v) = 'STRING NOT NULL'
                    OR valueType(v) STARTS WITH 'LIST<STRING'
              )
            RETURN DISTINCT k
            ORDER BY k
            """
        )
        raw_keys: list[str] = [r["k"] for r in rows if r.get("k")]

        # Validate each key through _safe_rel; skip non-conforming keys.
        validated: list[str] = []
        skipped: list[str] = []
        for key in raw_keys:
            try:
                _safe_rel(key)
                validated.append(key)
            except ValueError:
                skipped.append(key)

        if skipped:
            logger.warning(
                "Skipped %d property keys that failed _safe_rel validation: %s",
                len(skipped),
                skipped,
            )

        # Promote rdfs__label to the front for better default scoring.
        if "rdfs__label" in validated:
            validated.remove("rdfs__label")
            validated.insert(0, "rdfs__label")

        return validated

    async def _get_existing_index_properties(self: "Neo4jStore") -> list[str] | None:
        """Return the property list of the *queryable* ontorag_fulltext index.

        Note: SHOW INDEXES in Neo4j 5 does not support bound parameters in
        WHERE clauses.  We fetch all FULLTEXT indexes and filter in Python.

        Only an index in the ``ONLINE`` state is queryable.  A ``POPULATING``
        index already exposes its property list but raises if queried, and a
        ``FAILED`` index is unusable — both are treated as absent (return
        None) so callers fall back to the empty-result / recreate paths
        instead of triggering a 500.

        Returns:
            List of property names when the index exists and is ONLINE,
            otherwise None.
        """
        rows = await self._run("SHOW INDEXES WHERE type = 'FULLTEXT'")
        for row in rows:
            if row.get("name") == _FULLTEXT_INDEX_NAME:
                # A non-ONLINE index (POPULATING / FAILED) is not queryable.
                if row.get("state") != "ONLINE":
                    self._fulltext_index_ready = False
                    return None
                # 'properties' column is present in Neo4j 5+
                props = row.get("properties") or []
                self._fulltext_index_ready = True
                return list(props) if isinstance(props, list) else []
        self._fulltext_index_ready = False
        return None

    async def _ensure_fulltext_index(self: "Neo4jStore") -> None:
        """Ensure the BM25 full-text index is up-to-date with current text properties.

        Called at the end of load_rdf. Discovers string-valued property keys on
        :Resource nodes, then creates or recreates the named index if the
        discovered property set differs from the existing index.

        Handles gracefully:
        - Index already exists and matches — no-op.
        - Index exists but property set changed — drop + recreate.
        - No text properties found — skip index creation.
        - Neo4j errors during populating state — log and continue.
        """
        try:
            text_keys = await self._discover_text_property_keys()

            if not text_keys:
                logger.info("No string-valued properties found; skipping full-text index creation.")
                return

            existing_props = await self._get_existing_index_properties()

            if existing_props is not None:
                # Compare sets — order doesn't matter for index correctness,
                # but to avoid churn we only recreate when the set actually changes.
                if set(existing_props) == set(text_keys):
                    logger.debug(
                        "Full-text index '%s' already covers the correct property set (%d keys); skipping.",
                        _FULLTEXT_INDEX_NAME,
                        len(text_keys),
                    )
                    return
                # Property set changed — drop the old index.
                logger.info(
                    "Property set changed (old=%s, new=%s); dropping index '%s' for recreation.",
                    sorted(existing_props),
                    sorted(text_keys),
                    _FULLTEXT_INDEX_NAME,
                )
                self._fulltext_index_ready = False
                await self._run_write(f"DROP INDEX {_FULLTEXT_INDEX_NAME} IF EXISTS")

            # Build the property list: each key is backtick-quoted after _safe_rel validation.
            prop_list = ", ".join(f"n.`{k}`" for k in text_keys)
            create_stmt = (
                f"CREATE FULLTEXT INDEX {_FULLTEXT_INDEX_NAME} IF NOT EXISTS "
                f"FOR (n:Resource) ON EACH [{prop_list}]"
            )
            logger.info(
                "Creating full-text index '%s' on %d properties: %s",
                _FULLTEXT_INDEX_NAME,
                len(text_keys),
                text_keys,
            )
            await self._run_write(create_stmt)
            # A freshly created index begins POPULATING — do NOT mark it ready;
            # the next search will verify the ONLINE state via SHOW INDEXES.
            self._fulltext_index_ready = None
            logger.info("Full-text index '%s' created successfully.", _FULLTEXT_INDEX_NAME)

        except Exception as exc:
            # Full-text index creation is best-effort — never break load_rdf.
            self._fulltext_index_ready = False
            logger.warning(
                "Failed to ensure full-text index '%s': %s. search_text will be unavailable.",
                _FULLTEXT_INDEX_NAME,
                exc,
            )

    # ── B3 — search ───────────────────────────────────────────────────────────

    async def search_text(
        self: "Neo4jStore",
        query: str,
        class_uri: str | None = None,
        limit: int = 20,
    ) -> list[SearchHit]:
        """Search for entities using BM25 full-text (Lucene) index.

        The query is passed verbatim as a bound parameter to Lucene; it is
        never interpolated into the Cypher string.  If class_uri is provided,
        results are restricted to instances of that class or any of its
        subclasses (reusing the rdfs__subClassOf*0..N inference pattern from
        find_entities).

        Returns an empty list if the index does not exist (no data loaded yet)
        or if no matches are found.

        Args:
            query: Lucene query string (e.g. "Pikachu" or "pika*").
            class_uri: Optional full URI of a class to restrict results to.
            limit: Maximum number of hits to return (default 20, max 200).

        Returns:
            List of SearchHit ordered by BM25 score descending.
        """
        await self._ensure_prefix_map()

        # Guard: only an ONLINE index is queryable.  When the cache says the
        # index is ready we skip the SHOW INDEXES round-trip; otherwise verify
        # against the DB (which also refreshes the cache).  POPULATING / FAILED
        # / missing all yield None → return [] instead of raising a 500.
        if self._fulltext_index_ready is not True:
            existing = await self._get_existing_index_properties()
            if existing is None:
                logger.debug(
                    "Full-text index '%s' not ONLINE/available; returning empty results.",
                    _FULLTEXT_INDEX_NAME,
                )
                return []

        # A node with N rdf:type edges yields N rows; if we LIMITed at row
        # granularity we'd consume slots for duplicates and under-deliver
        # distinct hits.  Over-fetch an internal cap, then dedup + slice in
        # Python so the caller always gets up to `limit` *distinct* hits.
        internal_limit = max(limit * 5, limit + 50)

        # Lazy import avoids a circular import (neo4j.py imports this mixin).
        from ontorag.stores.neo4j import _TBOX_TYPE_URIS  # noqa: PLC0415

        try:
            rows = await self._query_index(query, class_uri, internal_limit)
        except Exception as exc:
            # Defensive: a race (index dropped/repopulating between the guard
            # and the query) must not surface as a 500.
            self._fulltext_index_ready = False
            logger.warning(
                "Full-text query against '%s' failed (%s); returning empty results.",
                _FULLTEXT_INDEX_NAME,
                exc,
            )
            return []

        if not rows:
            return []

        # De-duplicate by URI — keep the highest-scoring row per node and
        # prefer a non-TBox rdf:type for the reported class_uri.
        seen: dict[str, SearchHit] = {}
        for row in rows:
            uri = row.get("uri")
            if not uri:
                continue
            score = float(row.get("score") or 0.0)

            cls_uri_raw = row.get("cls_uri")
            # Report an rdf:type only when it is an ABox class, never a
            # vocabulary type (owl:Class, rdfs:Class, owl:*Property, …).
            cls_hit: str | None = (
                cls_uri_raw
                if cls_uri_raw and cls_uri_raw not in _TBOX_TYPE_URIS
                else None
            )

            existing_hit = seen.get(uri)
            if existing_hit is not None:
                # Keep the best score; backfill a class_uri if we now have one.
                # Immutable update (no in-place mutation of the pydantic model).
                if cls_hit and existing_hit.class_uri is None:
                    existing_hit = existing_hit.model_copy(update={"class_uri": cls_hit})
                    seen[uri] = existing_hit
                if existing_hit.score >= score:
                    continue

            raw_label = row.get("raw_label")
            label: str | None = None
            if raw_label is not None:
                label_val = first_scalar(raw_label)
                if label_val is not None:
                    label = str(label_val)
                    # Strip lang tag if present (keepLangTag=true stores "text@lang")
                    if "@" in label:
                        label = label.split("@")[0]

            # Preserve an already-resolved class_uri if this winning row lacks one.
            resolved_cls = cls_hit or (existing_hit.class_uri if existing_hit else None)

            seen[uri] = SearchHit(
                uri=uri,
                label=label,
                class_uri=resolved_cls,
                score=score,
            )

        # Sort by score descending, then slice to the caller's requested limit.
        ordered = sorted(seen.values(), key=lambda h: h.score, reverse=True)
        return ordered[:limit]

    async def _query_index(
        self: "Neo4jStore",
        query: str,
        class_uri: str | None,
        internal_limit: int,
    ) -> list[dict[str, Any]]:
        """Run the full-text query, returning raw (possibly duplicated) rows.

        Uses $search_query (not $query) to avoid colliding with the Neo4j
        driver's own ``query`` positional parameter in session.run().

        Args:
            query: Lucene query string (bound, never interpolated).
            class_uri: Optional class URI for subClassOf-aware filtering.
            internal_limit: Over-fetch cap applied in Cypher before Python dedup.

        Returns:
            Raw result rows (uri, raw_label, cls_uri, score). A node may appear
            multiple times when it has multiple rdf:type edges.
        """
        if class_uri is not None:
            # Subclass-aware filter: node must be an instance of class_uri or any subclass.
            cypher = f"""
            CALL db.index.fulltext.queryNodes($index_name, $search_query)
              YIELD node, score
            WHERE node:Resource
              AND (node)-[:rdf__type]->()-[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->(:Resource {{uri: $class_uri}})
            OPTIONAL MATCH (node)-[:rdf__type]->(cls:Resource)
            RETURN
              node.uri AS uri,
              node.rdfs__label AS raw_label,
              cls.uri AS cls_uri,
              score
            ORDER BY score DESC
            LIMIT $limit
            """
            return await self._run(
                cypher,
                index_name=_FULLTEXT_INDEX_NAME,
                search_query=query,
                class_uri=class_uri,
                limit=internal_limit,
            )

        cypher = """
        CALL db.index.fulltext.queryNodes($index_name, $search_query)
          YIELD node, score
        WHERE node:Resource
        OPTIONAL MATCH (node)-[:rdf__type]->(cls:Resource)
        RETURN
          node.uri AS uri,
          node.rdfs__label AS raw_label,
          cls.uri AS cls_uri,
          score
        ORDER BY score DESC
        LIMIT $limit
        """
        return await self._run(
            cypher,
            index_name=_FULLTEXT_INDEX_NAME,
            search_query=query,
            limit=internal_limit,
        )
