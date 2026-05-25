"""Graph embedding mixin for Neo4jStore (C3 + C4).

Implements:
  - build_embeddings(): FastRP structural embeddings via GDS + textual embeddings
    via EmbeddingProvider. Writes _struct_embedding / _text_embedding node
    properties and maintains matching vector indexes.
  - find_similar(): kNN lookup using struct_vec / text_vec index or RRF fusion.

Both are *capabilities*, not part of the GraphStore protocol — backends that
do not expose these methods receive a 501 via the route's getattr guard
(same pattern as _neo4j_search_mixin.py).

Security:
  - $uri and $vec are bound parameters; never interpolated.
  - Index names are module-level constants (never user-supplied).
  - Any interpolated label / rel-type / property key (incl. text-property keys
    and GDS projection label/rel sets) goes through _safe_rel().
  - ``ontology`` ids are validated by ``validate_ontology_id`` before any use.

Per-ontology scoping:
  - ``ontology=None`` (default) → current all-graph behaviour (backward compat).
  - ``build_embeddings(ontology="<id>")`` scopes GDS projection and textual
    node fetch to nodes where ``$ontology_id IN n._ontology``.  Only those
    nodes' properties are written — other ontologies' node properties are
    untouched (natural isolation; no global delete/rebuild).
  - ``find_similar(ontology="<id>")`` post-filters the global kNN result to
    nodes where ``$ontology_id IN node._ontology``.  The query over-fetches
    (top_k * ``_SCOPE_OVERFETCH``) so the post-filtered result still reaches
    top_k in typical cases.  See the trade-off note in ``_find_similar_single``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Literal

from ontorag.core.cypher import _safe_rel
from ontorag.core.ontology import validate_ontology_id
from ontorag.stores._neo4j_scope import build_where, ontology_scope_filter
from ontorag.stores.base import SimilarHit

if TYPE_CHECKING:
    from ontorag.llm.embedding import EmbeddingProvider
    from ontorag.stores.neo4j import Neo4jStore

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Structural embedding: GDS FastRP dimension (fixed for reproducibility).
_STRUCT_DIM: int = 256

# Property name written by gds.fastRP.write and indexed by struct_vec.
_STRUCT_PROP: str = "_struct_embedding"
# Property name for textual embeddings (dimension = provider.dimension).
_TEXT_PROP: str = "_text_embedding"

# Native vector index names (hardcoded — never user-supplied).
_STRUCT_INDEX: str = "struct_vec"
_TEXT_INDEX: str = "text_vec"

# Rank-fusion constant (standard RRF k0 ≈ 60).
_RRF_K0: int = 60

# Text properties to gather for textual embeddings, in priority order.
_TEXT_PROPS_ORDERED: list[str] = [
    "rdfs__label",
    "rdfs__comment",
    "skos__definition",
]

# Minimum text length (chars) to bother embedding.
_MIN_TEXT_LEN: int = 3

# Node-fetch page size for textual embeddings — bounds peak memory so the whole
# ABox never lands in a single Python list (HIGH review #1).
_TEXT_PAGE_SIZE: int = 2000

# Over-fetch multiplier for scoped kNN post-filtering.
# When ontology is set, the vector index is queried with top_k * this factor
# so that after discarding nodes that don't carry the ontology id there are
# still top_k results.  Default 5 is conservative; callers can lower it via
# env var if needed.  Trade-off: higher recall, more latency.
_SCOPE_OVERFETCH: int = 5

#: kNN over-fetch multiplier when a class filter is active — many neighbours
#: belong to other classes (moves, types) and get dropped by the in-query
#: subClassOf filter, so fetch more candidates to still reach top_k.
_CLASS_FILTER_OVERFETCH: int = 10


class _Neo4jEmbeddingMixin:
    """Structural + textual graph-embedding capability mixed into Neo4jStore.

    Not part of the GraphStore protocol — exposed as optional capabilities.
    The MCP route guards with ``getattr(store, "build_embeddings", None)`` and
    ``getattr(store, "find_similar", None)`` (see api/routes/tools/similar.py).
    """

    # Provided by Neo4jStore at runtime
    _run: Any
    _run_write: Any
    _shorten: Any
    _expand: Any
    _ensure_prefix_map: Any
    _tbox_type_list: Any

    # ── C3 — build_embeddings ─────────────────────────────────────────────────

    async def build_embeddings(
        self: "Neo4jStore",
        mode: Literal["structural", "textual", "both"] = "both",
        embedding_provider: "EmbeddingProvider | None" = None,
        ontology: str | None = None,
    ) -> dict[str, int]:
        """Build structural and/or textual embeddings on ABox instance nodes.

        Structural: projects ABox instance nodes + their object-property
        relationships into a GDS catalog graph (unique name per run), runs
        ``gds.fastRP.write`` (dim=_STRUCT_DIM, randomSeed=42), drops the
        projection, and ensures ``struct_vec`` vector index is ONLINE.

        Textual: gathers text per ABox node (rdfs:label + rdfs:comment +
        skos:definition + other string props), batches through the
        EmbeddingProvider, writes ``_text_embedding``, and ensures ``text_vec``
        vector index sized to ``provider.dimension``.

        When ``ontology`` is provided only nodes whose ``_ontology`` list
        contains the given id are projected / embedded.  Other nodes' embedding
        properties are NOT touched — isolation is natural (only matched nodes
        are written).

        When ``ontology`` is None (default) the full ABox is used, matching
        the current behaviour (backward-compat).

        Args:
            mode: "structural", "textual", or "both".
            embedding_provider: Optional provider for textual mode.  Defaults
                to ``get_embedding_provider()`` when None.
            ontology: Optional ontology id to scope the build.  None means all
                nodes (current behaviour).

        Returns:
            Dict mapping mode name → number of nodes whose property was written.

        Raises:
            ValueError: If ``ontology`` does not match ``^[a-zA-Z0-9_-]+$``.
        """
        # Validate before any use — raises ValueError on bad id.
        ontology = validate_ontology_id(ontology)

        await self._ensure_prefix_map()
        result: dict[str, int] = {}

        if mode in ("structural", "both"):
            written = await self._build_structural(ontology=ontology)
            result["structural"] = written

        if mode in ("textual", "both"):
            if embedding_provider is None:
                from ontorag.llm.embedding import get_embedding_provider  # noqa: PLC0415

                embedding_provider = get_embedding_provider()
            written = await self._build_textual(embedding_provider, ontology=ontology)
            result["textual"] = written

        return result

    async def _build_structural(
        self: "Neo4jStore",
        ontology: str | None = None,
    ) -> int:
        """Run GDS FastRP on ABox instance nodes and write _struct_embedding.

        When ``ontology`` is provided the GDS graph projection is limited to
        nodes where the id appears in ``_ontology``.  GDS node filtering uses
        a Cypher projection (``gds.graph.project.cypher``) with a bound param.

        When ``ontology`` is None the standard label-based projection is used
        (``'Resource'`` label), which is faster for the all-graph case.

        Args:
            ontology: Validated ontology id or None.

        Returns:
            Number of nodes whose ``_struct_embedding`` property was written.
        """
        scope_frag, scope_params = ontology_scope_filter(ontology, node_alias="a")

        if ontology is None:
            # All-graph path: discover relationship types globally.
            rel_rows = await self._run(
                """
                MATCH (a:Resource)-[r]->(b:Resource)
                WHERE NOT a:_NsPrefDef AND NOT a:_GraphConfig
                  AND NOT b:_NsPrefDef AND NOT b:_GraphConfig
                RETURN DISTINCT type(r) AS rel_type
                """
            )
        else:
            # Scoped path: only rels where at least one endpoint is in scope.
            rel_rows = await self._run(
                """
                MATCH (a:Resource)-[r]->(b:Resource)
                WHERE NOT a:_NsPrefDef AND NOT a:_GraphConfig
                  AND NOT b:_NsPrefDef AND NOT b:_GraphConfig
                  AND $ontology_id IN a._ontology
                RETURN DISTINCT type(r) AS rel_type
                """,
                **scope_params,
            )

        rel_types: list[str] = []
        for row in rel_rows:
            rt = row.get("rel_type")
            if rt:
                try:
                    rel_types.append(_safe_rel(rt))
                except ValueError:
                    logger.warning("Skipping unsafe rel type in GDS projection: %r", rt)

        if not rel_types:
            logger.warning(
                "No relationship types found for GDS projection; "
                "structural embeddings require loaded ABox data."
            )
            return 0

        # Use a unique projection name so concurrent calls don't collide.
        proj_name = f"ontorag_embed_{uuid.uuid4().hex[:8]}"

        # Rel config dict: each rel-type → {orientation: 'UNDIRECTED'}.
        rel_config: dict[str, dict[str, str]] = {
            rt: {"orientation": "UNDIRECTED"} for rt in rel_types
        }

        try:
            await self._run_write(
                "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName",
                name=proj_name,
            )
        except Exception:
            pass  # drop fails when projection doesn't exist — that's fine

        # Build the projection.  For scoped builds we use gds.graph.project.cypher
        # so only in-scope nodes are included; for all-graph we use the faster
        # label-based projection.
        try:
            if ontology is None:
                proj_rows = await self._run_write(
                    "CALL gds.graph.project($name, 'Resource', $rel_config) "
                    "YIELD nodeCount RETURN nodeCount",
                    name=proj_name,
                    rel_config=rel_config,
                )
            else:
                # Cypher projection filters nodes by _ontology membership.
                # nodeQuery / relationshipQuery are plain Cypher strings
                # but the ontology id is passed as a bound parameter via
                # nodeParameters / relationshipParameters.
                node_query = (
                    "MATCH (n:Resource) "
                    "WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
                    "  AND $ontology_id IN n._ontology "
                    "RETURN id(n) AS id"
                )
                rel_query = (
                    "MATCH (a:Resource)-[r]->(b:Resource) "
                    "WHERE NOT a:_NsPrefDef AND NOT a:_GraphConfig "
                    "  AND NOT b:_NsPrefDef AND NOT b:_GraphConfig "
                    "  AND $ontology_id IN a._ontology "
                    "RETURN id(a) AS source, id(b) AS target"
                )
                proj_rows = await self._run_write(
                    "CALL gds.graph.project.cypher("
                    "  $name,"
                    "  $node_query,"
                    "  $rel_query,"
                    "  {parameters: {ontology_id: $ontology_id}}"
                    ") YIELD nodeCount RETURN nodeCount",
                    name=proj_name,
                    node_query=node_query,
                    rel_query=rel_query,
                    **scope_params,
                )

            node_count = proj_rows[0]["nodeCount"] if proj_rows else 0
            logger.info(
                "GDS projection '%s' created: %d nodes, %d rel-types (ontology=%r)",
                proj_name,
                node_count,
                len(rel_types),
                ontology,
            )
        except Exception as exc:
            logger.error("Failed to create GDS projection '%s': %s", proj_name, exc)
            return 0

        try:
            write_rows = await self._run_write(
                "CALL gds.fastRP.write($name, {"
                "  embeddingDimension: $dim,"
                "  writeProperty: $prop,"
                "  randomSeed: $seed"
                "}) YIELD nodePropertiesWritten RETURN nodePropertiesWritten",
                name=proj_name,
                dim=_STRUCT_DIM,
                prop=_STRUCT_PROP,
                seed=42,
            )
            written = write_rows[0]["nodePropertiesWritten"] if write_rows else 0
            logger.info("FastRP wrote %d _struct_embedding properties (ontology=%r)", written, ontology)
        except Exception as exc:
            logger.error("gds.fastRP.write failed: %s", exc)
            written = 0
        finally:
            try:
                await self._run_write(
                    "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName",
                    name=proj_name,
                )
            except Exception:
                pass  # best-effort cleanup

        # Ensure the vector index exists with the correct dimension.
        await self._ensure_vector_index(_STRUCT_INDEX, _STRUCT_PROP, _STRUCT_DIM)
        return written

    async def _build_textual(
        self: "Neo4jStore",
        provider: "EmbeddingProvider",
        ontology: str | None = None,
    ) -> int:
        """Embed textual representations of ABox nodes via EmbeddingProvider.

        When ``ontology`` is provided only nodes whose ``_ontology`` list
        contains the id are fetched; other nodes' ``_text_embedding`` property
        is not modified.

        Args:
            provider: EmbeddingProvider to batch-embed.
            ontology: Validated ontology id or None (all nodes).

        Returns:
            Number of nodes whose ``_text_embedding`` property was written.
        """
        await self._ensure_prefix_map()

        scope_frag, scope_params = ontology_scope_filter(ontology, node_alias="n")
        scope_where = build_where(
            [
                "NOT n:_NsPrefDef AND NOT n:_GraphConfig",
                scope_frag,
            ]
        )

        # Discover string-valued properties on in-scope ABox Resource nodes.
        str_prop_rows = await self._run(
            f"""
            MATCH (n:Resource)
            {scope_where}
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
            """,
            **scope_params,
        )
        all_str_keys: list[str] = []
        for row in str_prop_rows:
            k = row.get("k")
            if k:
                try:
                    all_str_keys.append(_safe_rel(k))
                except ValueError:
                    logger.warning("Skipping unsafe text property key: %r", k)

        if not all_str_keys:
            logger.info("No string properties found for textual embeddings.")
            return 0

        # Prioritise the known semantic properties; add remaining ones after.
        ordered_keys: list[str] = []
        for k in _TEXT_PROPS_ORDERED:
            if k in all_str_keys:
                ordered_keys.append(k)
        for k in all_str_keys:
            if k not in ordered_keys:
                ordered_keys.append(k)

        # Build the paged node-fetch query.
        prop_return = ", ".join(f"n.`{k}` AS `{k}`" for k in ordered_keys)
        fetch_cypher = (
            f"MATCH (n:Resource) "
            f"{scope_where} "
            f"WITH n ORDER BY n.uri "
            f"SKIP $skip LIMIT $page "
            f"RETURN n.uri AS uri, {prop_return}"
        )

        written = 0
        total_embedded = 0
        skip = 0
        while True:
            rows = await self._run(fetch_cypher, skip=skip, page=_TEXT_PAGE_SIZE, **scope_params)
            if not rows:
                break

            page_pairs = self._rows_to_text_pairs(rows, ordered_keys)
            skip += len(rows)

            if not page_pairs:
                if len(rows) < _TEXT_PAGE_SIZE:
                    break
                continue

            uris = [u for u, _ in page_pairs]
            texts = [t for _, t in page_pairs]
            total_embedded += len(texts)

            try:
                vectors = await provider.embed(texts)
            except Exception as exc:
                logger.error("EmbeddingProvider.embed() failed: %s", exc)
                return written

            if len(vectors) != len(uris):
                logger.error(
                    "Provider returned %d vectors for %d texts; aborting page.",
                    len(vectors),
                    len(uris),
                )
                return written

            written += await self._write_text_vectors(uris, vectors)

            if len(rows) < _TEXT_PAGE_SIZE:
                break

        logger.info(
            "Embedded %d nodes via %s (dim=%d, ontology=%r); wrote %d _text_embedding properties",
            total_embedded,
            provider.model,
            provider.dimension,
            ontology,
            written,
        )

        if written == 0:
            logger.info("No non-empty text found for textual embeddings.")
            return 0

        await self._ensure_vector_index(_TEXT_INDEX, _TEXT_PROP, provider.dimension)
        return written

    @staticmethod
    def _rows_to_text_pairs(
        rows: list[dict[str, Any]],
        ordered_keys: list[str],
    ) -> list[tuple[str, str]]:
        """Build (uri, combined_text) pairs from a page of node rows.

        Skips nodes whose combined text is shorter than ``_MIN_TEXT_LEN``.

        Args:
            rows: Raw node rows with a ``uri`` key and per-property keys.
            ordered_keys: Property keys to concatenate, in priority order.

        Returns:
            List of (uri, text) pairs for nodes with embeddable text.
        """
        pairs: list[tuple[str, str]] = []
        for row in rows:
            uri = row.get("uri")
            if not uri:
                continue
            parts: list[str] = []
            for k in ordered_keys:
                val = row.get(k)
                if val is None:
                    continue
                if isinstance(val, list):
                    for item in val:
                        if item and isinstance(item, str):
                            # Strip lang tag ("피카츄@ko" → "피카츄")
                            text = item.split("@")[0].strip()
                            if text:
                                parts.append(text)
                elif isinstance(val, str):
                    text = val.split("@")[0].strip()
                    if text:
                        parts.append(text)
            combined = " ".join(parts)
            if len(combined) >= _MIN_TEXT_LEN:
                pairs.append((uri, combined))
        return pairs

    async def _write_text_vectors(
        self: "Neo4jStore",
        uris: list[str],
        vectors: list[list[float]],
    ) -> int:
        """Write a batch of textual embeddings via a single UNWIND write.

        Args:
            uris: Node URIs (bound parameter values, never interpolated).
            vectors: Embedding vectors aligned with ``uris``.

        Returns:
            Number of nodes whose ``_text_embedding`` property was set.
        """
        pairs = [{"uri": u, "vec": v} for u, v in zip(uris, vectors)]
        try:
            write_rows = await self._run_write(
                "UNWIND $pairs AS pair "
                "MATCH (n:Resource {uri: pair.uri}) "
                f"SET n.`{_TEXT_PROP}` = pair.vec "
                "RETURN count(n) AS cnt",
                pairs=pairs,
            )
            return write_rows[0]["cnt"] if write_rows else 0
        except Exception as exc:
            logger.error("Batch write of textual embeddings failed: %s", exc)
            return 0

    # ── Index lifecycle ───────────────────────────────────────────────────────

    async def _get_vector_index_info(
        self: "Neo4jStore",
        index_name: str,
    ) -> dict[str, Any] | None:
        """Return info dict for a named vector index, or None if absent/not ONLINE.

        Args:
            index_name: The exact vector index name (hardcoded constant).

        Returns:
            Dict with at minimum "state" and "options" keys, or None.
        """
        # SHOW INDEXES does not support bound parameters in WHERE; filter in Python.
        rows = await self._run("SHOW INDEXES WHERE type = 'VECTOR'")
        for row in rows:
            if row.get("name") == index_name:
                return dict(row)
        return None

    async def _ensure_vector_index(
        self: "Neo4jStore",
        index_name: str,
        prop_name: str,
        dimension: int,
        wait_online_secs: float = 10.0,
    ) -> None:
        """Create or verify a native vector index on :Resource nodes.

        Creates ``CREATE VECTOR INDEX <index_name> IF NOT EXISTS``.  If an
        index with the same name already exists but has a different dimension,
        it is dropped and recreated.  After creation, polls until the index
        reaches ONLINE state (up to ``wait_online_secs``).

        Args:
            index_name: Hardcoded constant index name (never user input).
            prop_name: Node property holding the embedding vector.
            dimension: Expected vector dimension.
            wait_online_secs: Seconds to poll for ONLINE state after creation.
        """
        info = await self._get_vector_index_info(index_name)
        if info is not None:
            state = info.get("state", "")
            if state == "POPULATING":
                logger.info(
                    "Vector index '%s' is POPULATING; waiting up to %.0fs for ONLINE...",
                    index_name,
                    wait_online_secs,
                )
                online = await self._wait_for_index_online(
                    index_name, wait_online_secs
                )
                if online:
                    logger.info("Vector index '%s' is now ONLINE.", index_name)
                    info = await self._get_vector_index_info(index_name)
                    state = info.get("state", "") if info else ""
                else:
                    logger.warning(
                        "Vector index '%s' did not reach ONLINE within %.0fs; recreating.",
                        index_name,
                        wait_online_secs,
                    )
                    await self._run_write(f"DROP INDEX {index_name} IF EXISTS")
                    info = None

            if info is not None and state == "ONLINE":
                options = info.get("options") or {}
                idx_cfg = options.get("indexConfig") or {}
                existing_dim = idx_cfg.get("vector.dimensions")
                if existing_dim is not None and int(existing_dim) == dimension:
                    logger.debug(
                        "Vector index '%s' already ONLINE with correct dimension %d; no-op.",
                        index_name,
                        dimension,
                    )
                    return
                logger.info(
                    "Vector index '%s' has dimension %s (expected %d); recreating.",
                    index_name,
                    existing_dim,
                    dimension,
                )
                await self._run_write(f"DROP INDEX {index_name} IF EXISTS")
            elif info is not None:
                logger.warning(
                    "Vector index '%s' is in unexpected state '%s'; dropping and recreating.",
                    index_name,
                    state,
                )
                await self._run_write(f"DROP INDEX {index_name} IF EXISTS")

        create_stmt = (
            f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS "
            f"FOR (n:Resource) ON n.`{prop_name}` "
            f"OPTIONS {{indexConfig:{{"
            f"`vector.dimensions`:{dimension}, "
            f"`vector.similarity_function`:'cosine'"
            f"}}}}"
        )
        try:
            await self._run_write(create_stmt)
            logger.info(
                "Created vector index '%s' on property '%s' (dim=%d); waiting for ONLINE...",
                index_name,
                prop_name,
                dimension,
            )
            await self._wait_for_index_online(index_name, wait_online_secs)
        except Exception as exc:
            logger.error("Failed to create vector index '%s': %s", index_name, exc)

    async def _wait_for_index_online(
        self: "Neo4jStore",
        index_name: str,
        timeout_secs: float = 10.0,
        poll_interval: float = 0.5,
    ) -> bool:
        """Poll until the named index is ONLINE or the timeout is reached.

        Args:
            index_name: Index to monitor.
            timeout_secs: Maximum seconds to wait.
            poll_interval: Seconds between polls.

        Returns:
            True if the index became ONLINE within the timeout, False otherwise.
        """
        elapsed = 0.0
        while elapsed < timeout_secs:
            info = await self._get_vector_index_info(index_name)
            if info and info.get("state") == "ONLINE":
                return True
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        return False

    # ── C4 — find_similar ─────────────────────────────────────────────────────

    async def find_similar(
        self: "Neo4jStore",
        uri: str,
        top_k: int = 10,
        mode: Literal["structural", "textual", "hybrid"] = "structural",
        class_uri: str | None = None,
        ontology: str | None = None,
    ) -> list[SimilarHit]:
        """Find the most structurally / textually similar entities.

        Args:
            uri: Full URI of the query entity (bound parameter — never
                interpolated).
            top_k: Maximum results to return (1–100).
            mode: "structural", "textual", or "hybrid" (RRF fusion).
            class_uri: Optional class URI to restrict hits to instances of
                that class (rdfs:subClassOf-aware, via a native Cypher
                `[:rdfs__subClassOf*0..]` filter on the kNN result).  None
                (default) returns similar entities of any class.
            ontology: Optional ontology id to scope results.  When provided
                the kNN result is post-filtered to nodes whose ``_ontology``
                list contains the id (Neo4j vector indexes are global — no
                native payload filter).  ``_SCOPE_OVERFETCH`` controls how
                many extra candidates are fetched to compensate for post-filter
                attrition.  None means no filter (current default, backward-compat).

        Returns:
            Ranked list of SimilarHit with cosine scores (single-mode) or
            RRF fused scores (hybrid).  Returns [] if the index is absent,
            the node lacks the embedding, or any other error occurs.

        Raises:
            ValueError: If ``ontology`` does not match ``^[a-zA-Z0-9_-]+$``.
        """
        await self._ensure_prefix_map()

        # Validate ontology id before any use.
        ontology = validate_ontology_id(ontology)

        if mode == "hybrid":
            return await self._find_similar_hybrid(
                uri, top_k, class_uri=class_uri, ontology=ontology
            )
        if mode == "textual":
            return await self._find_similar_single(
                uri, top_k, "textual", class_uri=class_uri, ontology=ontology
            )
        return await self._find_similar_single(
            uri, top_k, "structural", class_uri=class_uri, ontology=ontology
        )

    async def _find_similar_single(
        self: "Neo4jStore",
        uri: str,
        top_k: int,
        mode: Literal["structural", "textual"],
        class_uri: str | None = None,
        ontology: str | None = None,
    ) -> list[SimilarHit]:
        """Execute a single-mode kNN query.

        When ``ontology`` is provided the global kNN result is post-filtered
        to nodes whose ``_ontology`` LIST property contains the id.  We
        over-fetch by ``_SCOPE_OVERFETCH`` to compensate for post-filter
        attrition.

        Trade-off note: Neo4j native vector indexes (db.index.vector.queryNodes)
        do not support payload filters — the index is global.  Post-filtering
        is therefore required for scoped queries.  If the graph contains very
        few in-scope nodes relative to the total node count the over-fetch
        factor may need to be increased; in practice ``_SCOPE_OVERFETCH=5``
        keeps recall ≥ top_k for all realistic graph sizes.

        Args:
            uri: Query entity URI (bound parameter).
            top_k: Maximum results to return.
            mode: "structural" or "textual".
            ontology: Optional ontology id for post-filtering.

        Returns:
            List of SimilarHit or [] on any error.
        """
        index_name = _STRUCT_INDEX if mode == "structural" else _TEXT_INDEX
        prop_name = _STRUCT_PROP if mode == "structural" else _TEXT_PROP

        # Verify the index is ONLINE before querying.
        info = await self._get_vector_index_info(index_name)
        if info is None or info.get("state") != "ONLINE":
            logger.debug(
                "Vector index '%s' absent or not ONLINE; returning [].", index_name
            )
            return []

        # Fetch the start node's embedding.
        try:
            vec_rows = await self._run(
                f"MATCH (n:Resource {{uri: $uri}}) RETURN n.`{prop_name}` AS vec",
                uri=uri,
            )
        except Exception as exc:
            logger.warning("Failed to fetch embedding for %r: %s", uri, exc)
            return []

        if not vec_rows:
            logger.debug("Node %r not found in the graph.", uri)
            return []

        vec = vec_rows[0].get("vec")
        if vec is None:
            logger.debug("Node %r has no %s embedding.", uri, prop_name)
            return []

        # Lazy import to avoid circular imports.
        from ontorag.stores.neo4j import _TBOX_TYPE_URIS  # noqa: PLC0415

        # Over-fetch: when scoped (ontology) or class-filtered, request more
        # candidates so that post-filter / in-query-filter attrition leaves at
        # least top_k results in the typical case.
        overfetch = 1
        if ontology is not None:
            overfetch = max(overfetch, _SCOPE_OVERFETCH)
        if class_uri is not None:
            overfetch = max(overfetch, _CLASS_FILTER_OVERFETCH)
        k_fetch = top_k * overfetch + 1 if overfetch > 1 else top_k + 1

        # subClassOf-aware class filter applied inside the kNN pipeline (native
        # Cypher path; class_uri is a bound param → injection-safe).
        class_clause = ""
        params: dict[str, Any] = {"index_name": index_name, "k": k_fetch, "vec": vec}
        if class_uri is not None:
            class_clause = (
                "  AND EXISTS { MATCH (node)-[:rdf__type]->(:Resource)"
                "-[:rdfs__subClassOf*0..]->(:Resource {uri: $class_uri}) } "
            )
            params["class_uri"] = class_uri

        try:
            rows = await self._run(
                "CALL db.index.vector.queryNodes($index_name, $k, $vec) "
                "YIELD node, score "
                "WHERE node.uri IS NOT NULL "
                f"{class_clause}"
                "OPTIONAL MATCH (node)-[:rdf__type]->(cls:Resource) "
                "RETURN node.uri AS uri, "
                "       node.rdfs__label AS raw_label, "
                "       node._ontology AS node_ontology, "
                "       cls.uri AS cls_uri, "
                "       score",
                **params,
            )
        except Exception as exc:
            logger.warning("Vector query on '%s' failed: %s", index_name, exc)
            return []

        return self._rows_to_similar_hits(
            rows, uri, top_k, mode, _TBOX_TYPE_URIS, ontology=ontology
        )

    async def _find_similar_hybrid(
        self: "Neo4jStore",
        uri: str,
        top_k: int,
        class_uri: str | None = None,
        ontology: str | None = None,
    ) -> list[SimilarHit]:
        """Fuse structural + textual rankings via Reciprocal Rank Fusion.

        Runs each single-mode query with top_k*2 candidates, then fuses
        scores using RRF(k0=60).

        Args:
            uri: Query entity URI.
            top_k: Maximum results to return after fusion.
            class_uri: Optional class URI propagated to both single-mode calls
                (each is class-filtered before fusion).
            ontology: Optional ontology id propagated to both single-mode calls.

        Returns:
            List of SimilarHit with mode="hybrid" or [] on any error.
        """
        struct_hits = await self._find_similar_single(
            uri, top_k * 2, "structural", class_uri=class_uri, ontology=ontology
        )
        text_hits = await self._find_similar_single(
            uri, top_k * 2, "textual", class_uri=class_uri, ontology=ontology
        )

        if not struct_hits and not text_hits:
            return []

        # Build rank-indexed dicts for RRF.  Rank is 0-indexed here (rank+1 in formula).
        rrf_scores: dict[str, float] = {}
        rrf_meta: dict[str, tuple[str | None, str | None]] = {}  # uri → (label, class_uri)

        for rank, hit in enumerate(struct_hits):
            rrf_scores[hit.uri] = rrf_scores.get(hit.uri, 0.0) + 1.0 / (_RRF_K0 + rank + 1)
            rrf_meta[hit.uri] = (hit.label, hit.class_uri)

        for rank, hit in enumerate(text_hits):
            rrf_scores[hit.uri] = rrf_scores.get(hit.uri, 0.0) + 1.0 / (_RRF_K0 + rank + 1)
            if hit.uri not in rrf_meta:
                rrf_meta[hit.uri] = (hit.label, hit.class_uri)
            elif hit.class_uri is not None and rrf_meta[hit.uri][1] is None:
                # Backfill class_uri if structural hit had None.
                rrf_meta[hit.uri] = (rrf_meta[hit.uri][0] or hit.label, hit.class_uri)

        # Sort by descending fused score, take top_k.
        sorted_uris = sorted(rrf_scores, key=lambda u: rrf_scores[u], reverse=True)
        results: list[SimilarHit] = []
        for candidate_uri in sorted_uris[:top_k]:
            label, class_uri = rrf_meta.get(candidate_uri, (None, None))
            results.append(
                SimilarHit(
                    uri=candidate_uri,
                    label=label,
                    class_uri=class_uri,
                    score=rrf_scores[candidate_uri],
                    mode="hybrid",
                )
            )
        return results

    # ── Mapping helpers ───────────────────────────────────────────────────────

    def _rows_to_similar_hits(
        self: "Neo4jStore",
        rows: list[dict[str, Any]],
        exclude_uri: str,
        top_k: int,
        mode: Literal["structural", "textual"],
        tbox_type_uris: frozenset[str],
        ontology: str | None = None,
    ) -> list[SimilarHit]:
        """Map raw Cypher rows to deduplicated SimilarHit list.

        When ``ontology`` is provided each row is post-filtered: the
        ``node_ontology`` field (``node._ontology`` list) must contain the id.
        Rows without this field (legacy data without ``_ontology`` tag) are
        also excluded when a scope is active to avoid false positives.

        Args:
            rows: Raw query rows (uri, raw_label, node_ontology, cls_uri, score).
            exclude_uri: The start node URI to exclude from results.
            top_k: Maximum results after deduplication and post-filter.
            mode: Embedding mode label for the hits.
            tbox_type_uris: Set of TBox class URIs to exclude as class_uri.
            ontology: Optional ontology id for post-filtering.

        Returns:
            Deduplicated, sorted SimilarHit list (up to top_k).
        """
        from ontorag.stores._neo4j_values import first_scalar  # noqa: PLC0415

        seen: dict[str, SimilarHit] = {}

        for row in rows:
            hit_uri = row.get("uri")
            if not hit_uri or hit_uri == exclude_uri:
                continue

            # Post-filter: when a scope is active only include nodes that
            # carry the ontology id in their _ontology list.  Nodes without
            # the property (legacy untagged data) are excluded to avoid mixing
            # scoped and unscoped results (conservative — better to miss than
            # to contaminate).
            if ontology is not None:
                node_ont = row.get("node_ontology")
                if not isinstance(node_ont, list) or ontology not in node_ont:
                    continue

            score = float(row.get("score") or 0.0)

            cls_uri_raw = row.get("cls_uri")
            cls_hit: str | None = (
                cls_uri_raw
                if cls_uri_raw and cls_uri_raw not in tbox_type_uris
                else None
            )

            raw_label = row.get("raw_label")
            label: str | None = None
            if raw_label is not None:
                label_val = first_scalar(raw_label)
                if label_val is not None:
                    label = str(label_val).split("@")[0]

            existing = seen.get(hit_uri)
            if existing is not None:
                if existing.score >= score:
                    updates: dict[str, Any] = {}
                    if cls_hit and existing.class_uri is None:
                        updates["class_uri"] = cls_hit
                    if label and existing.label is None:
                        updates["label"] = label
                    if updates:
                        seen[hit_uri] = existing.model_copy(update=updates)
                    continue
                # Better score from another rdf:type row — replace, keeping
                # resolved metadata (immutability policy, MEDIUM #3).
                resolved_cls = cls_hit or existing.class_uri
                resolved_label = label or existing.label
            else:
                resolved_cls = cls_hit
                resolved_label = label

            seen[hit_uri] = SimilarHit(
                uri=hit_uri,
                label=resolved_label,
                class_uri=resolved_cls,
                score=score,
                mode=mode,
            )

        ordered = sorted(seen.values(), key=lambda h: h.score, reverse=True)
        return ordered[:top_k]
