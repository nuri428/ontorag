"""Graph-embedding capability mixin for FalkorDBStore (v0.9.1).

Structural embeddings use the pure-Python FastRP (``core/fastrp.py``) — FalkorDB
has no GDS — and are stored as ``vecf32`` properties indexed by FalkorDB's
*native* vector index. Textual embeddings use the EmbeddingProvider. kNN is
``CALL db.idx.vector.queryNodes('Resource', prop, k, vecf32(v)) YIELD node, score``.

This is the hybrid of the two existing backends: FastRP like Fuseki, native
vector index like Neo4j. Hybrid mode fuses structural + textual via RRF.

FalkorDB's cosine vector index returns a *distance* (0 = identical); we convert
to a similarity score ``1/(1+distance)`` so higher = more similar, matching the
other backends' SimilarHit.score convention.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from ontorag.core.fastrp import fastrp_embeddings
from ontorag.stores.base import SimilarHit

if TYPE_CHECKING:
    from ontorag.llm.embedding import EmbeddingProvider
    from ontorag.stores.falkordb import FalkorDBStore

logger = logging.getLogger(__name__)

_STRUCT_DIM = 256
_STRUCT_PROP = "_struct_embedding"
_TEXT_PROP = "_text_embedding"
_RRF_K0 = 60
_MIN_TEXT_LEN = 3
_MAX_DEPTH_HARD = 6


class _FalkorDBEmbeddingMixin:
    """Structural + textual graph embeddings via FalkorDB native vector index."""

    _run: Any
    _run_write: Any
    _ensure_prefix_map: Any
    _tbox_type_list: Any

    # ── build ──────────────────────────────────────────────────────────────────

    async def build_embeddings(
        self: "FalkorDBStore",
        mode: Literal["structural", "textual", "both"] = "both",
        embedding_provider: "EmbeddingProvider | None" = None,
        ontology: str | None = None,
    ) -> dict[str, int]:
        """Build structural and/or textual embeddings for ABox instances."""
        await self._ensure_prefix_map()
        result: dict[str, int] = {}
        if mode in ("structural", "both"):
            result["structural"] = await self._build_structural(ontology)
        if mode in ("textual", "both"):
            if embedding_provider is None:
                from ontorag.llm.embedding import get_embedding_provider  # noqa: PLC0415

                embedding_provider = get_embedding_provider()
            result["textual"] = await self._build_textual(embedding_provider, ontology)
        return result

    async def _instance_uris(self: "FalkorDBStore", ontology: str | None) -> list[str]:
        """ABox instance URIs (nodes with a non-vocab rdf:type), optionally scoped."""
        scope = " AND $oid IN inst._ontology" if ontology else ""
        params: dict[str, Any] = {"tbox": self._tbox_type_list}
        if ontology:
            params["oid"] = ontology
        rows = await self._run(
            f"MATCH (inst:Resource)-[:rdf__type]->(t:Resource) "
            f"WHERE NOT t.uri IN $tbox{scope} RETURN DISTINCT inst.uri AS uri",
            **params,
        )
        return [r["uri"] for r in rows if r.get("uri")]

    async def _build_structural(self: "FalkorDBStore", ontology: str | None) -> int:
        nodes = await self._instance_uris(ontology)
        if not nodes:
            logger.info("FalkorDB: no ABox instances; structural embeddings skipped.")
            return 0
        edge_rows = await self._run(
            "MATCH (s:Resource)-[r]->(o:Resource) "
            "WHERE NOT type(r) IN ['rdf__type'] RETURN s.uri AS s, o.uri AS o"
        )
        edges = [(e["s"], e["o"]) for e in edge_rows if e.get("s") and e.get("o")]
        vectors = fastrp_embeddings(edges=edges, nodes=nodes, dim=_STRUCT_DIM, seed=42)
        await self._ensure_vector_index(_STRUCT_PROP, _STRUCT_DIM)
        return await self._store_vectors(_STRUCT_PROP, vectors)

    async def _build_textual(
        self: "FalkorDBStore", provider: "EmbeddingProvider", ontology: str | None
    ) -> int:
        scope = " AND $oid IN inst._ontology" if ontology else ""
        params: dict[str, Any] = {"tbox": self._tbox_type_list}
        if ontology:
            params["oid"] = ontology
        rows = await self._run(
            f"MATCH (inst:Resource)-[:rdf__type]->(t:Resource) "
            f"WHERE NOT t.uri IN $tbox{scope} "
            f"RETURN inst.uri AS uri, inst.rdfs__label AS label, "
            f"inst.rdfs__comment AS comment",
            **params,
        )
        pairs: list[tuple[str, str]] = []
        for r in rows:
            uri = r.get("uri")
            if not uri:
                continue
            parts: list[str] = []
            for key in ("label", "comment"):
                v = r.get(key)
                v = v[0] if isinstance(v, list) and v else v
                if isinstance(v, str) and v:
                    parts.append(v.split("@")[0].strip())
            text = " ".join(p for p in parts if p)
            if len(text) >= _MIN_TEXT_LEN:
                pairs.append((uri, text))
        if not pairs:
            logger.info("FalkorDB: no embeddable text; textual embeddings skipped.")
            return 0
        uris = [u for u, _ in pairs]
        vectors_list = await provider.embed([t for _, t in pairs])
        if len(vectors_list) != len(uris):
            logger.error("FalkorDB textual: provider returned mismatched vector count.")
            return 0
        await self._ensure_vector_index(_TEXT_PROP, provider.dimension)
        return await self._store_vectors(_TEXT_PROP, dict(zip(uris, vectors_list)))

    async def _ensure_vector_index(self: "FalkorDBStore", prop: str, dim: int) -> None:
        """Create the native vector index for *prop* (idempotent / best-effort)."""
        try:
            await self._run_write(
                f"CREATE VECTOR INDEX FOR (n:Resource) ON (n.`{prop}`) "
                f"OPTIONS {{dimension: {int(dim)}, similarityFunction: 'cosine'}}"
            )
        except Exception as exc:  # noqa: BLE001 — already exists / unsupported
            logger.debug("FalkorDB vector index on %s: %s", prop, exc)

    async def _store_vectors(
        self: "FalkorDBStore", prop: str, vectors: dict[str, list[float]]
    ) -> int:
        rows = [{"uri": u, "vec": [float(x) for x in v]} for u, v in vectors.items()]
        if not rows:
            return 0
        await self._run_write(
            f"UNWIND $rows AS r MATCH (n:Resource {{uri: r.uri}}) "
            f"SET n.`{prop}` = vecf32(r.vec)",
            rows=rows,
        )
        logger.info("FalkorDB: stored %d %s vectors.", len(rows), prop)
        return len(rows)

    # ── find_similar ────────────────────────────────────────────────────────────

    async def find_similar(
        self: "FalkorDBStore",
        uri: str,
        top_k: int = 10,
        mode: Literal["structural", "textual", "hybrid"] = "structural",
        class_uri: str | None = None,
        ontology: str | None = None,
    ) -> list[SimilarHit]:
        """kNN similar entities via the native vector index (subClassOf-aware)."""
        await self._ensure_prefix_map()
        if mode == "hybrid":
            return await self._similar_hybrid(uri, top_k, class_uri, ontology)
        return await self._similar_single(uri, top_k, mode, class_uri, ontology)

    async def _similar_single(
        self: "FalkorDBStore",
        uri: str,
        top_k: int,
        mode: Literal["structural", "textual"],
        class_uri: str | None,
        ontology: str | None,
    ) -> list[SimilarHit]:
        prop = _STRUCT_PROP if mode == "structural" else _TEXT_PROP
        vrows = await self._run(
            f"MATCH (n:Resource {{uri: $uri}}) RETURN n.`{prop}` AS vec", uri=uri
        )
        vec = vrows[0].get("vec") if vrows else None
        if not vec:
            logger.debug("find_similar: %s has no %s embedding.", uri, mode)
            return []
        over = min(max(top_k * 10, top_k + 1), 200) if class_uri else top_k + 1
        try:
            rows = await self._run(
                f"CALL db.idx.vector.queryNodes('Resource', '{prop}', $k, vecf32($vec)) "
                f"YIELD node, score RETURN node.uri AS uri, score",
                k=over,
                vec=[float(x) for x in vec],
            )
        except Exception as exc:  # noqa: BLE001 — no index yet
            logger.warning("FalkorDB vector query failed (%s); returning []", exc)
            return []
        hit_uris = [r["uri"] for r in rows if r.get("uri") and r["uri"] != uri]
        if not hit_uris:
            return []
        if class_uri is not None:
            allowed = await self._filter_by_class(hit_uris, class_uri, ontology)
            hit_uris = [h for h in hit_uris if h in allowed]
            if not hit_uris:
                return []
        meta = await self._resolve_meta(hit_uris)
        results: list[SimilarHit] = []
        for r in rows:
            hu = r.get("uri")
            if not hu or hu == uri or hu not in hit_uris:
                continue
            dist = float(r.get("score") or 0.0)
            m = meta.get(hu, {})
            results.append(
                SimilarHit(
                    uri=hu,
                    label=m.get("label"),
                    class_uri=m.get("class_uri"),
                    score=1.0 / (1.0 + dist),  # cosine distance → similarity
                    mode=mode,
                )
            )
            if len(results) >= top_k:
                break
        return results

    async def _similar_hybrid(
        self: "FalkorDBStore",
        uri: str,
        top_k: int,
        class_uri: str | None,
        ontology: str | None,
    ) -> list[SimilarHit]:
        struct = await self._similar_single(uri, top_k * 2, "structural", class_uri, ontology)
        text = await self._similar_single(uri, top_k * 2, "textual", class_uri, ontology)
        if not struct and not text:
            return []
        scores: dict[str, float] = {}
        meta: dict[str, tuple[str | None, str | None]] = {}
        for rank, h in enumerate(struct):
            scores[h.uri] = scores.get(h.uri, 0.0) + 1.0 / (_RRF_K0 + rank + 1)
            meta[h.uri] = (h.label, h.class_uri)
        for rank, h in enumerate(text):
            scores[h.uri] = scores.get(h.uri, 0.0) + 1.0 / (_RRF_K0 + rank + 1)
            if h.uri not in meta:
                meta[h.uri] = (h.label, h.class_uri)
        ordered = sorted(scores, key=lambda u: scores[u], reverse=True)[:top_k]
        return [
            SimilarHit(
                uri=u,
                label=meta.get(u, (None, None))[0],
                class_uri=meta.get(u, (None, None))[1],
                score=scores[u],
                mode="hybrid",
            )
            for u in ordered
        ]

    async def _filter_by_class(
        self: "FalkorDBStore", uris: list[str], class_uri: str, ontology: str | None
    ) -> set[str]:
        """Subset of *uris* that are instances of class_uri or a subclass."""
        try:
            rows = await self._run(
                "UNWIND $uris AS u "
                f"MATCH (inst:Resource {{uri: u}})-[:rdf__type]->(:Resource)"
                f"-[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->(:Resource {{uri: $cls}}) "
                "RETURN DISTINCT inst.uri AS uri",
                uris=uris,
                cls=class_uri,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning("FalkorDB class filter failed (%s); keeping all", exc)
            return set(uris)
        return {r["uri"] for r in rows if r.get("uri")}

    async def _resolve_meta(
        self: "FalkorDBStore", uris: list[str]
    ) -> dict[str, dict[str, str | None]]:
        """Resolve label + a non-vocab class_uri per entity URI."""
        rows = await self._run(
            "UNWIND $uris AS u MATCH (n:Resource {uri: u}) "
            "OPTIONAL MATCH (n)-[:rdf__type]->(c:Resource) WHERE NOT c.uri IN $tbox "
            "RETURN n.uri AS uri, n.rdfs__label AS raw_label, min(c.uri) AS class_uri",
            uris=uris,
            tbox=self._tbox_type_list,
        )
        out: dict[str, dict[str, str | None]] = {}
        for r in rows:
            u = r.get("uri")
            if not u:
                continue
            raw = r.get("raw_label")
            raw = raw[0] if isinstance(raw, list) and raw else raw
            label = str(raw).split("@")[0] if raw is not None else None
            out[u] = {"label": label, "class_uri": r.get("class_uri")}
        return out
