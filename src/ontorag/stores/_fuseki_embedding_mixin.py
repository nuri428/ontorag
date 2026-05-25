from __future__ import annotations

"""Graph-embedding capability mixin for FusekiStore (structural + textual).

Implements:
  - ``build_embeddings(mode, embedding_provider)`` — FastRP structural
    embeddings via :func:`ontorag.core.fastrp.fastrp_embeddings` (Fuseki has
    no GDS), textual embeddings via :class:`ontorag.llm.embedding.EmbeddingProvider`.
    Vectors are stored in Qdrant (named collections ``ontorag_struct`` /
    ``ontorag_text``).
  - ``find_similar(uri, top_k, mode)`` — Qdrant kNN lookup + optional
    Reciprocal Rank Fusion (RRF) for hybrid mode.

Both methods are **capabilities**, not part of the GraphStore protocol — the
MCP route guards with ``getattr(store, "find_similar", None)`` (same pattern
as ``_fuseki_search_mixin.py``).

Security:
  - All entity URIs and class URIs interpolated into SPARQL go through
    ``uri_ref()`` which validates the URI format before use.
  - Qdrant parameters (collection name, vector, point ID) are bound values —
    never raw-interpolated.
  - User-supplied ``uri`` in ``find_similar`` is validated by ``uri_ref``
    before any use.
"""

import logging
from typing import TYPE_CHECKING, Any, Literal

from ontorag.core.sparql import uri_ref
from ontorag.stores._qdrant import (
    STRUCT_COLLECTION,
    TEXT_COLLECTION,
    QdrantWrapper,
)
from ontorag.stores.base import SimilarHit

if TYPE_CHECKING:
    from ontorag.llm.embedding import EmbeddingProvider
    from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

#: FastRP structural embedding dimension (fixed for reproducibility).
_STRUCT_DIM: int = 256

#: Rank-fusion constant (standard RRF k0 ≈ 60).
_RRF_K0: int = 60

#: Minimum text length (chars) to bother embedding.
_MIN_TEXT_LEN: int = 3

#: Textual-embedding page size — bounds peak memory + provider batch size so
#: the whole ABox never lands in one Python list / one giant embed() call
#: (mirrors ``_TEXT_PAGE_SIZE`` in ``_neo4j_embedding_mixin.py``).
_TEXT_PAGE_SIZE: int = 2000

# Vocabulary type URIs that should not be reported as instance class_uri.
# Mirrors ``_TBOX_TYPE_URIS`` in ``_fuseki_search_mixin.py``.
_TBOX_TYPE_URIS: frozenset[str] = frozenset(
    {
        "http://www.w3.org/2002/07/owl#Class",
        "http://www.w3.org/2002/07/owl#ObjectProperty",
        "http://www.w3.org/2002/07/owl#DatatypeProperty",
        "http://www.w3.org/2002/07/owl#AnnotationProperty",
        "http://www.w3.org/2002/07/owl#TransitiveProperty",
        "http://www.w3.org/2002/07/owl#FunctionalProperty",
        "http://www.w3.org/2002/07/owl#InverseFunctionalProperty",
        "http://www.w3.org/2002/07/owl#SymmetricProperty",
        "http://www.w3.org/2002/07/owl#AsymmetricProperty",
        "http://www.w3.org/2002/07/owl#ReflexiveProperty",
        "http://www.w3.org/2002/07/owl#IrreflexiveProperty",
        "http://www.w3.org/2002/07/owl#Ontology",
        "http://www.w3.org/2000/01/rdf-schema#Class",
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#Property",
    }
)

# Named graphs (mirrors fuseki.py constants).
_DATA = "urn:ontorag:data"
_SCHEMA = "urn:ontorag:schema"

#: SPARQL ``NOT IN (...)`` list of TBox vocab types, derived from
#: ``_TBOX_TYPE_URIS`` so it stays in sync.  Used to exclude vocabulary
#: nodes (owl:Class, owl:ObjectProperty, …) from FastRP projection and from
#: the resolved ``class_uri`` of similarity hits.  These are constant W3C
#: vocabulary URIs — never user input — so direct ``<...>`` interpolation is
#: safe (no uri_ref needed).
_TBOX_NOT_IN_FILTER: str = ", ".join(f"<{u}>" for u in sorted(_TBOX_TYPE_URIS))


class _FusekiEmbeddingMixin:
    """Structural + textual graph-embedding capability mixed into FusekiStore.

    Not part of the GraphStore protocol — exposed as optional capabilities.
    The MCP route guards with ``getattr(store, "build_embeddings", None)`` and
    ``getattr(store, "find_similar", None)`` (see api/routes/tools/similar.py).

    Vectors are stored in Qdrant using the module-level wrappers in
    ``stores/_qdrant.py``.  A ``QdrantWrapper`` is created lazily on first use
    and cached on ``self._qdrant``.
    """

    # Provided by FusekiStore at runtime.
    _sparql_select: Any
    _qdrant: "QdrantWrapper | None"

    def _get_qdrant(self: "FusekiStore") -> QdrantWrapper:
        """Return the cached QdrantWrapper, creating it on first access.

        Reads ``QDRANT_URL`` from the environment (default
        ``http://localhost:6333``).  ``FusekiStore.__init__`` initialises
        ``self._qdrant = None``, so this is a simple None check.

        Returns:
            Shared QdrantWrapper instance for this store.

        Raises:
            ValueError: If qdrant-client is not installed.
        """
        if self._qdrant is None:
            self._qdrant = QdrantWrapper.from_env()
        return self._qdrant

    # ── build_embeddings ──────────────────────────────────────────────────────

    async def build_embeddings(
        self: "FusekiStore",
        mode: Literal["structural", "textual", "both"] = "both",
        embedding_provider: "EmbeddingProvider | None" = None,
    ) -> dict[str, int]:
        """Build structural and/or textual embeddings for ABox instances.

        Structural: extracts instance URIs and object-property edges from the
        ABox via SPARQL; computes FastRP embeddings (pure-Python,
        :func:`ontorag.core.fastrp.fastrp_embeddings`); upserts to the
        ``ontorag_struct`` Qdrant collection (dim 256).

        Textual: gathers text per instance (rdfs:label, rdfs:comment,
        skos:definition, other string literals) via SPARQL; batches through
        the embedding provider; upserts to ``ontorag_text`` (dim =
        ``provider.dimension``).  Nodes with no usable text are skipped.

        Both modes are idempotent — each Qdrant collection is dropped and
        recreated at the start of its build (clear-on-build), so entities
        deleted from the graph since the last build leave no zombie points.

        Staleness: vectors are NOT auto-refreshed on ``load_rdf`` /
        ``clear_graph``.  After changing the data (e.g. ``ontorag clear data``
        or loading new instances) re-run ``ontorag embed`` to rebuild a
        consistent index — until then ``find_similar`` reflects the last build.

        Args:
            mode: "structural", "textual", or "both".
            embedding_provider: Optional provider for textual mode.  Defaults
                to ``get_embedding_provider()`` when None.

        Returns:
            Dict mapping mode name → number of entities whose vector was upserted.
        """
        result: dict[str, int] = {}

        if mode in ("structural", "both"):
            count = await self._build_structural_embeddings()
            result["structural"] = count

        if mode in ("textual", "both"):
            if embedding_provider is None:
                from ontorag.llm.embedding import get_embedding_provider  # noqa: PLC0415

                embedding_provider = get_embedding_provider()
            count = await self._build_textual_embeddings(embedding_provider)
            result["textual"] = count

        return result

    async def _build_structural_embeddings(self: "FusekiStore") -> int:
        """Extract ABox graph, run FastRP, upsert to Qdrant.

        Returns:
            Number of entity vectors upserted to ``ontorag_struct``.
        """
        from ontorag.core.fastrp import fastrp_embeddings  # noqa: PLC0415

        # Fetch all ABox instance URIs (subjects with rdf:type in <_DATA>),
        # excluding vocabulary-typed subjects (owl:Class etc.) so TBox nodes
        # never inflate the FastRP projection (HIGH #4).  Subjects must have
        # at least one *real* (non-vocab) type to count as an ABox instance.
        node_sparql = f"""
SELECT DISTINCT ?inst
WHERE {{
  GRAPH <{_DATA}> {{
    ?inst a ?type .
    FILTER(!isBlank(?inst))
    FILTER(?type NOT IN ({_TBOX_NOT_IN_FILTER}))
  }}
}}
ORDER BY STR(?inst)
"""
        try:
            node_result = await self._sparql_select(node_sparql)
        except Exception as exc:
            logger.error("SPARQL node fetch failed in _build_structural_embeddings: %s", exc)
            return 0

        nodes: list[str] = [
            b["inst"]["value"]
            for b in node_result.get("results", {}).get("bindings", [])
            if "inst" in b
        ]

        if not nodes:
            logger.info("No ABox instances found; structural embeddings skipped.")
            return 0

        # Fetch object-property edges between ABox instances.
        edge_sparql = f"""
SELECT DISTINCT ?subj ?obj
WHERE {{
  GRAPH <{_DATA}> {{
    ?subj ?pred ?obj .
    FILTER(!isBlank(?subj) && !isBlank(?obj) && isIRI(?obj))
  }}
  FILTER EXISTS {{ GRAPH <{_DATA}> {{ ?obj a ?t . }} }}
}}
"""
        try:
            edge_result = await self._sparql_select(edge_sparql)
        except Exception as exc:
            logger.warning("SPARQL edge fetch failed (embeddings will be seed-only): %s", exc)
            edge_result = {"results": {"bindings": []}}

        edges: list[tuple[str, str]] = [
            (b["subj"]["value"], b["obj"]["value"])
            for b in edge_result.get("results", {}).get("bindings", [])
            if "subj" in b and "obj" in b
        ]

        logger.info(
            "Running FastRP on %d nodes, %d edges (dim=%d)...",
            len(nodes),
            len(edges),
            _STRUCT_DIM,
        )

        try:
            vectors = fastrp_embeddings(edges=edges, nodes=nodes, dim=_STRUCT_DIM, seed=42)
        except Exception as exc:
            logger.error("fastrp_embeddings failed: %s", exc)
            return 0

        qdrant = self._get_qdrant()
        # Clear-on-build: drop the collection first so entities deleted from
        # the graph since the last build leave no zombie points (MEDIUM #2).
        await qdrant.delete_collection(STRUCT_COLLECTION)
        await qdrant.ensure_collection(STRUCT_COLLECTION, dim=_STRUCT_DIM)

        pairs = [(uri, vec) for uri, vec in vectors.items()]
        count = await qdrant.upsert(STRUCT_COLLECTION, pairs)
        logger.info("Structural: upserted %d vectors to Qdrant '%s'.", count, STRUCT_COLLECTION)
        return count

    async def _build_textual_embeddings(
        self: "FusekiStore",
        provider: "EmbeddingProvider",
    ) -> int:
        """Fetch instance text, embed, upsert to Qdrant — paged for bounded memory.

        The text-gathering SELECT is paged with ``OFFSET``/``LIMIT`` (page
        size ``_TEXT_PAGE_SIZE``) so the whole ABox never lands in one Python
        list and ``provider.embed`` is called per page rather than once for
        the entire graph (HIGH #3, mirrors the Neo4j paged loop).

        Clear-on-build: the collection is dropped before the first page so
        entities deleted since the last build leave no zombie points
        (MEDIUM #2).

        Args:
            provider: EmbeddingProvider to use for batched embedding.

        Returns:
            Number of entity vectors upserted to ``ontorag_text``.
        """
        qdrant = self._get_qdrant()
        collection_ready = False  # created lazily on the first non-empty page
        total_upserted = 0
        offset = 0

        while True:
            page_sparql = self._textual_page_sparql(offset, _TEXT_PAGE_SIZE)
            try:
                page_result = await self._sparql_select(page_sparql)
            except Exception as exc:
                logger.error(
                    "SPARQL text fetch failed in _build_textual_embeddings "
                    "(offset=%d): %s",
                    offset,
                    exc,
                )
                return total_upserted

            rows = page_result.get("results", {}).get("bindings", [])
            if not rows:
                break

            page_pairs = self._rows_to_text_pairs(rows)
            offset += len(rows)

            # A page may yield no embeddable text — still advance the cursor.
            if not page_pairs:
                if len(rows) < _TEXT_PAGE_SIZE:
                    break
                continue

            uris = [u for u, _ in page_pairs]
            texts = [t for _, t in page_pairs]

            try:
                vectors = await provider.embed(texts)
            except Exception as exc:
                logger.error("EmbeddingProvider.embed() failed: %s", exc)
                return total_upserted

            if len(vectors) != len(uris):
                logger.error(
                    "Provider returned %d vectors for %d texts (offset=%d); aborting.",
                    len(vectors),
                    len(uris),
                    offset,
                )
                return total_upserted

            # Create (and clear) the collection on the first non-empty page —
            # delaying until here means an all-empty ABox never recreates an
            # otherwise-valid collection.
            if not collection_ready:
                await qdrant.delete_collection(TEXT_COLLECTION)
                await qdrant.ensure_collection(TEXT_COLLECTION, dim=provider.dimension)
                collection_ready = True

            total_upserted += await qdrant.upsert(TEXT_COLLECTION, list(zip(uris, vectors)))

            # Short page → no more rows to fetch.
            if len(rows) < _TEXT_PAGE_SIZE:
                break

        if total_upserted == 0:
            logger.info("No embeddable text found for textual embeddings.")
            return 0

        logger.info(
            "Textual: upserted %d vectors to Qdrant '%s' via %s (dim=%d).",
            total_upserted,
            TEXT_COLLECTION,
            provider.model,
            provider.dimension,
        )
        return total_upserted

    @staticmethod
    def _textual_page_sparql(offset: int, limit: int) -> str:
        """Build the paged text-gathering SELECT for textual embeddings.

        Args:
            offset: SPARQL OFFSET for this page.
            limit: SPARQL LIMIT (page size).

        Returns:
            A SPARQL query string returning one row per ABox instance with
            sampled rdfs:label / rdfs:comment / skos:definition.
        """
        # ``offset`` / ``limit`` are integers controlled by the loop — never
        # user input — so direct interpolation is safe.
        return f"""
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?inst
       (SAMPLE(?lbl) AS ?label)
       (SAMPLE(?cmt) AS ?comment)
       (SAMPLE(?def) AS ?definition)
WHERE {{
  GRAPH <{_DATA}> {{
    ?inst a ?type .
    FILTER(!isBlank(?inst))
    FILTER(?type NOT IN ({_TBOX_NOT_IN_FILTER}))
    OPTIONAL {{ ?inst rdfs:label ?lbl . }}
    OPTIONAL {{ ?inst rdfs:comment ?cmt . }}
    OPTIONAL {{ ?inst skos:definition ?def . }}
  }}
}}
GROUP BY ?inst
ORDER BY STR(?inst)
OFFSET {offset}
LIMIT {limit}
"""

    @staticmethod
    def _rows_to_text_pairs(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """Build (uri, combined_text) pairs from a page of SPARQL rows.

        Skips rows whose combined text is shorter than ``_MIN_TEXT_LEN``.

        Args:
            rows: Raw SPARQL bindings with ``inst`` + sampled text vars.

        Returns:
            List of (uri, text) pairs with embeddable text.
        """
        pairs: list[tuple[str, str]] = []
        for b in rows:
            uri = b.get("inst", {}).get("value")
            if not uri:
                continue
            parts: list[str] = []
            for key in ("label", "comment", "definition"):
                val = b.get(key, {}).get("value")
                if val:
                    # Strip language tags ("피카츄@ko" → "피카츄").
                    parts.append(val.split("@")[0].strip())
            combined = " ".join(p for p in parts if p)
            if len(combined) >= _MIN_TEXT_LEN:
                pairs.append((uri, combined))
        return pairs

    # ── find_similar ─────────────────────────────────────────────────────────

    async def find_similar(
        self: "FusekiStore",
        uri: str,
        top_k: int = 10,
        mode: Literal["structural", "textual", "hybrid"] = "structural",
    ) -> list[SimilarHit]:
        """Find the most similar ontology entities using graph embeddings.

        Args:
            uri: Full URI of the query entity.  Validated by ``uri_ref``
                before any SPARQL or Qdrant use.
            top_k: Maximum results to return (1–100).
            mode: "structural", "textual", or "hybrid" (RRF fusion).

        Returns:
            Ranked list of SimilarHit.  Returns ``[]`` when the Qdrant
            collection is absent, the node has no embedding, or any error
            occurs — never raises / 500 for missing index.
        """
        # Validate the user-supplied URI before any use.
        try:
            uri_ref(uri)
        except ValueError:
            logger.warning("find_similar: invalid URI %r; returning [].", uri)
            return []

        if mode == "hybrid":
            return await self._find_similar_hybrid(uri, top_k)
        return await self._find_similar_single(uri, top_k, mode)

    async def _find_similar_single(
        self: "FusekiStore",
        uri: str,
        top_k: int,
        mode: Literal["structural", "textual"],
    ) -> list[SimilarHit]:
        """Execute a single-mode kNN query against Qdrant.

        Args:
            uri: Query entity URI (already validated by caller).
            top_k: Maximum results to return.
            mode: "structural" or "textual".

        Returns:
            List of SimilarHit or ``[]`` on any error.
        """
        collection = STRUCT_COLLECTION if mode == "structural" else TEXT_COLLECTION
        qdrant = self._get_qdrant()

        # Retrieve the start node's stored vector.
        vec = await qdrant.retrieve_vector(collection, uri)
        if vec is None:
            logger.debug(
                "find_similar: '%s' has no %s embedding in '%s'; returning [].",
                uri,
                mode,
                collection,
            )
            return []

        # Over-fetch to allow dedup / self-exclusion.
        k_plus = top_k + 1
        hits = await qdrant.query(collection, vec, k_plus)
        if not hits:
            return []

        # Resolve label + class_uri for all hit URIs via a single SPARQL query.
        # uri_ref validates each URI before interpolation.
        hit_uris = [hit_uri for hit_uri, _ in hits if hit_uri and hit_uri != uri]
        if not hit_uris:
            return []

        meta = await self._resolve_entity_meta(hit_uris)

        results: list[SimilarHit] = []
        for hit_uri, score in hits:
            if not hit_uri or hit_uri == uri:
                continue
            m = meta.get(hit_uri, {})
            results.append(
                SimilarHit(
                    uri=hit_uri,
                    label=m.get("label"),
                    class_uri=m.get("class_uri"),
                    score=score,
                    mode=mode,
                )
            )
            if len(results) >= top_k:
                break

        return results

    async def _find_similar_hybrid(
        self: "FusekiStore",
        uri: str,
        top_k: int,
    ) -> list[SimilarHit]:
        """Fuse structural + textual rankings via Reciprocal Rank Fusion.

        Runs both single-mode queries with top_k*2 candidates, then fuses
        scores using RRF(k0=60).  Mirrors the Neo4j implementation exactly.

        Args:
            uri: Query entity URI.
            top_k: Maximum results to return after fusion.

        Returns:
            List of SimilarHit with ``mode="hybrid"`` or ``[]`` when both
            modes return nothing.
        """
        struct_hits = await self._find_similar_single(uri, top_k * 2, "structural")
        text_hits = await self._find_similar_single(uri, top_k * 2, "textual")

        if not struct_hits and not text_hits:
            return []

        # Build rank-indexed RRF accumulator.
        rrf_scores: dict[str, float] = {}
        rrf_meta: dict[str, tuple[str | None, str | None]] = {}

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

    # ── SPARQL helper ─────────────────────────────────────────────────────────

    async def _resolve_entity_meta(
        self: "FusekiStore",
        uris: list[str],
    ) -> dict[str, dict[str, str | None]]:
        """Resolve label and a deterministic non-vocab class_uri per entity URI.

        TBox vocabulary types (owl:Class, owl:ObjectProperty, …) are filtered
        out INSIDE the SPARQL via ``FILTER(?cls NOT IN (...))`` so the
        ``SAMPLE`` aggregate can only ever pick a real ABox class — never a
        vocab type (HIGH #2).  ``MIN(STR(?cls))`` makes the picked class
        deterministic when an instance has several non-vocab classes, so
        ``class_uri`` is stable across repeated calls.

        All URIs are validated by ``uri_ref`` before interpolation; any URI
        that fails validation is silently excluded from the query.

        Args:
            uris: Entity URIs to resolve (from Qdrant kNN hits).

        Returns:
            Mapping ``uri → {"label": str|None, "class_uri": str|None}``.
        """
        # Validate every URI before building the VALUES clause.
        safe_uris: list[str] = []
        for u in uris:
            try:
                safe_uris.append(uri_ref(u))
            except ValueError:
                logger.warning("_resolve_entity_meta: skipping unsafe URI %r", u)

        if not safe_uris:
            return {}

        values_block = " ".join(safe_uris)

        # Filter vocab types in SPARQL → SAMPLE/MIN only see real ABox classes.
        # MIN(STR(?cls)) makes the chosen class deterministic; SAMPLE(?lbl) for
        # the label (any one matching label is acceptable).
        sparql = f"""
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?inst
       (SAMPLE(?lbl) AS ?label)
       (MIN(STR(?cls)) AS ?class_uri)
WHERE {{
  VALUES ?inst {{ {values_block} }}
  GRAPH <{_DATA}> {{
    ?inst a ?cls .
    FILTER(?cls NOT IN ({_TBOX_NOT_IN_FILTER}))
    OPTIONAL {{ ?inst rdfs:label ?lbl . }}
  }}
}}
GROUP BY ?inst
"""
        try:
            result = await self._sparql_select(sparql)
        except Exception as exc:
            logger.warning("_resolve_entity_meta SPARQL failed: %s", exc)
            return {}

        meta: dict[str, dict[str, str | None]] = {}
        for b in result.get("results", {}).get("bindings", []):
            inst = b.get("inst", {}).get("value")
            if not inst:
                continue
            label = b.get("label", {}).get("value")
            # class_uri is already vocab-filtered in SPARQL; MIN gives a
            # deterministic single value (or None when the only types were vocab).
            cls_out = b.get("class_uri", {}).get("value") or None
            meta[inst] = {"label": label, "class_uri": cls_out}

        return meta
