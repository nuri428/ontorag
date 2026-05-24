from __future__ import annotations

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
"""

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Literal

from ontorag.core.cypher import _safe_rel
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

        Both modes are idempotent: existing embeddings are overwritten and
        indexes are recreated only when the dimension has changed.

        Args:
            mode: "structural", "textual", or "both".
            embedding_provider: Optional provider for textual mode.  Defaults
                to ``get_embedding_provider()`` when None.

        Returns:
            Dict mapping mode name → number of nodes whose property was written.
        """
        await self._ensure_prefix_map()
        result: dict[str, int] = {}

        if mode in ("structural", "both"):
            written = await self._build_structural()
            result["structural"] = written

        if mode in ("textual", "both"):
            if embedding_provider is None:
                from ontorag.llm.embedding import get_embedding_provider  # noqa: PLC0415

                embedding_provider = get_embedding_provider()
            written = await self._build_textual(embedding_provider)
            result["textual"] = written

        return result

    async def _build_structural(self: "Neo4jStore") -> int:
        """Run GDS FastRP on ABox instance nodes and write _struct_embedding.

        Returns:
            Number of nodes whose ``_struct_embedding`` property was written.
        """
        # Collect all relationship types present on ABox nodes (validated).
        rel_rows = await self._run(
            """
            MATCH (a:Resource)-[r]->(b:Resource)
            WHERE NOT a:_NsPrefDef AND NOT a:_GraphConfig
              AND NOT b:_NsPrefDef AND NOT b:_GraphConfig
            RETURN DISTINCT type(r) AS rel_type
            """
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

        # Build the rel-config dict: each rel-type → {orientation: 'UNDIRECTED'}.
        # We build the dict OUTSIDE the Cypher and pass it as a bound parameter
        # so rel-type keys are never interpolated into the query string.
        rel_config: dict[str, dict[str, str]] = {
            rt: {"orientation": "UNDIRECTED"} for rt in rel_types
        }

        try:
            # Drop any leftover projection with the same name (should not happen
            # with uuid names, but keeps the function idempotent).
            await self._run_write(
                "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName",
                name=proj_name,
            )
        except Exception:
            pass  # drop fails when projection doesn't exist — that's fine

        # Project ABox Resource nodes only.  We pass the label as a string
        # literal in Cypher (it's the constant "Resource", not user input).
        # Rel config is a bound parameter dict.
        try:
            proj_rows = await self._run_write(
                "CALL gds.graph.project($name, 'Resource', $rel_config) "
                "YIELD nodeCount RETURN nodeCount",
                name=proj_name,
                rel_config=rel_config,
            )
            node_count = proj_rows[0]["nodeCount"] if proj_rows else 0
            logger.info(
                "GDS projection '%s' created: %d nodes, %d rel-types",
                proj_name,
                node_count,
                len(rel_types),
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
            logger.info("FastRP wrote %d _struct_embedding properties", written)
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
    ) -> int:
        """Embed textual representations of ABox nodes via EmbeddingProvider.

        Returns:
            Number of nodes whose ``_text_embedding`` property was written.
        """
        await self._ensure_prefix_map()

        # Discover all string-valued properties on ABox Resource nodes
        # (reuse the property-discovery logic from the search mixin).
        str_prop_rows = await self._run(
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

        # Page the node fetch so the whole ABox never lands in memory at once.
        # Each page is fetched, embedded, and written before advancing — bounded
        # memory regardless of graph size (HIGH review #1).
        prop_return = ", ".join(f"n.`{k}` AS `{k}`" for k in ordered_keys)
        fetch_cypher = (
            "MATCH (n:Resource) "
            "WHERE NOT n:_NsPrefDef AND NOT n:_GraphConfig "
            "WITH n ORDER BY n.uri "
            "SKIP $skip LIMIT $page "
            f"RETURN n.uri AS uri, {prop_return}"
        )

        written = 0
        total_embedded = 0
        skip = 0
        while True:
            rows = await self._run(fetch_cypher, skip=skip, page=_TEXT_PAGE_SIZE)
            if not rows:
                break

            page_pairs = self._rows_to_text_pairs(rows, ordered_keys)
            skip += len(rows)

            # Some pages may yield no embeddable text — still advance the cursor.
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

            # Short page → no more nodes to fetch.
            if len(rows) < _TEXT_PAGE_SIZE:
                break

        logger.info(
            "Embedded %d nodes via %s (dim=%d); wrote %d _text_embedding properties",
            total_embedded,
            provider.model,
            provider.dimension,
            written,
        )

        if written == 0:
            logger.info("No non-empty text found for textual embeddings.")
            return 0

        # Ensure the vector index for textual embeddings.
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
                # Index is being built; wait for it to become ONLINE before
                # inspecting its dimension (recreating mid-POPULATING can error).
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
                    # Did not reach ONLINE — treat as unusable and recreate so a
                    # stale wrong-dim index can never be queried (MEDIUM #2).
                    logger.warning(
                        "Vector index '%s' did not reach ONLINE within %.0fs; recreating.",
                        index_name,
                        wait_online_secs,
                    )
                    await self._run_write(f"DROP INDEX {index_name} IF EXISTS")
                    info = None

            if info is not None and state == "ONLINE":
                # Check if the dimension matches — stored in indexConfig.  This
                # path is reached both for a directly-ONLINE index and for one
                # that just finished POPULATING, so an OpenAI(1536)→Ollama(768)
                # switch never leaves a wrong-dim index in place (MEDIUM #2).
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
                # Dimension mismatch — drop and recreate.
                logger.info(
                    "Vector index '%s' has dimension %s (expected %d); recreating.",
                    index_name,
                    existing_dim,
                    dimension,
                )
                await self._run_write(f"DROP INDEX {index_name} IF EXISTS")
            elif info is not None:
                # FAILED or unknown — drop and recreate.
                logger.warning(
                    "Vector index '%s' is in unexpected state '%s'; dropping and recreating.",
                    index_name,
                    state,
                )
                await self._run_write(f"DROP INDEX {index_name} IF EXISTS")

        # prop_name is a module-level constant (_STRUCT_PROP / _TEXT_PROP) —
        # never user-supplied.  _safe_rel is designed for n10s-shortened
        # identifiers (``prefix__local``); our internal ``_*_embedding``
        # props intentionally do not follow that pattern and are safe by
        # construction (defined as string literals in this module).
        # We therefore skip _safe_rel validation here and rely on the fact
        # that callers always pass one of the two module constants.

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
            # Poll for ONLINE state — newly created indexes begin POPULATING.
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
    ) -> list[SimilarHit]:
        """Find the most structurally / textually similar entities.

        Args:
            uri: Full URI of the query entity (bound parameter — never
                interpolated).
            top_k: Maximum results to return (1–100).
            mode: "structural", "textual", or "hybrid" (RRF fusion).

        Returns:
            Ranked list of SimilarHit with cosine scores (single-mode) or
            RRF fused scores (hybrid).  Returns [] if the index is absent,
            the node lacks the embedding, or any other error occurs.
        """
        await self._ensure_prefix_map()

        if mode == "hybrid":
            return await self._find_similar_hybrid(uri, top_k)
        if mode == "textual":
            return await self._find_similar_single(uri, top_k, "textual")
        return await self._find_similar_single(uri, top_k, "structural")

    async def _find_similar_single(
        self: "Neo4jStore",
        uri: str,
        top_k: int,
        mode: Literal["structural", "textual"],
    ) -> list[SimilarHit]:
        """Execute a single-mode kNN query.

        Args:
            uri: Query entity URI (bound parameter).
            top_k: Maximum results to return.
            mode: "structural" or "textual".

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

        # Fetch top_k+1 to account for the start node appearing in results.
        k_plus = top_k + 1
        try:
            rows = await self._run(
                "CALL db.index.vector.queryNodes($index_name, $k, $vec) "
                "YIELD node, score "
                "WHERE node.uri IS NOT NULL "
                "OPTIONAL MATCH (node)-[:rdf__type]->(cls:Resource) "
                "RETURN node.uri AS uri, "
                "       node.rdfs__label AS raw_label, "
                "       cls.uri AS cls_uri, "
                "       score",
                index_name=index_name,
                k=k_plus,
                vec=vec,
            )
        except Exception as exc:
            logger.warning("Vector query on '%s' failed: %s", index_name, exc)
            return []

        return self._rows_to_similar_hits(rows, uri, top_k, mode, _TBOX_TYPE_URIS)

    async def _find_similar_hybrid(
        self: "Neo4jStore",
        uri: str,
        top_k: int,
    ) -> list[SimilarHit]:
        """Fuse structural + textual rankings via Reciprocal Rank Fusion.

        Runs each single-mode query with top_k*2 candidates, then fuses
        scores using RRF(k0=60).

        Args:
            uri: Query entity URI.
            top_k: Maximum results to return after fusion.

        Returns:
            List of SimilarHit with mode="hybrid" or [] on any error.
        """
        struct_hits = await self._find_similar_single(uri, top_k * 2, "structural")
        text_hits = await self._find_similar_single(uri, top_k * 2, "textual")

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
    ) -> list[SimilarHit]:
        """Map raw Cypher rows to deduplicated SimilarHit list.

        Args:
            rows: Raw query rows (uri, raw_label, cls_uri, score).
            exclude_uri: The start node URI to exclude from results.
            top_k: Maximum results after deduplication.
            mode: Embedding mode label for the hits.
            tbox_type_uris: Set of TBox class URIs to exclude as class_uri.

        Returns:
            Deduplicated, sorted SimilarHit list (up to top_k).
        """
        from ontorag.stores._neo4j_values import first_scalar  # noqa: PLC0415

        seen: dict[str, SimilarHit] = {}

        for row in rows:
            hit_uri = row.get("uri")
            if not hit_uri or hit_uri == exclude_uri:
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
                    # Keep the higher-scoring hit, but backfill a class_uri /
                    # label it was missing — via model_copy, never in-place
                    # mutation (immutability policy, MEDIUM #3).
                    updates: dict[str, Any] = {}
                    if cls_hit and existing.class_uri is None:
                        updates["class_uri"] = cls_hit
                    if label and existing.label is None:
                        updates["label"] = label
                    if updates:
                        seen[hit_uri] = existing.model_copy(update=updates)
                    continue
                # Better score from another rdf:type row — replace, but never
                # drop a class_uri / label resolved on the previous row.
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
