"""Full-text search mixin for FalkorDBStore (v0.9.1).

FalkorDB has a *native* full-text index (RediSearch under the hood):
  - create: ``CALL db.idx.fulltext.createNodeIndex('Resource', 'k1', 'k2', …)``
  - query:  ``CALL db.idx.fulltext.queryNodes('Resource', $q) YIELD node, score``

Differs from Neo4j (``db.index.fulltext.*``): the procedure takes the node
LABEL (not a named index), indexes are created synchronously (no POPULATING
state machine / SHOW INDEXES), and the query first arg is the label.

Like the Neo4j mixin this is an optional capability (getattr-guarded → 501).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontorag.stores._neo4j_scope import ontology_scope_filter
from ontorag.stores._neo4j_values import first_scalar
from ontorag.stores.base import SearchHit

if TYPE_CHECKING:
    from ontorag.stores.falkordb import FalkorDBStore

logger = logging.getLogger(__name__)

_MAX_DEPTH_HARD = 6

# Cap on the full-graph scan that rebuilds the _fulltext shadow. A load with
# more :Resource nodes than this leaves the overflow un-indexed; we warn so the
# truncation is never silent. (Acceptable at ontology-ABox scale — see
# docs/design/falkordb.md; page the rebuild if this becomes a real ceiling.)
_FT_SCAN_LIMIT = 50000


class _FalkorDBSearchMixin:
    """Native full-text search capability for FalkorDBStore."""

    _run: Any
    _run_write: Any
    _ensure_prefix_map: Any
    _tbox_type_list: Any

    async def _ensure_fulltext_index(self: "FalkorDBStore") -> None:
        """Build the scalar ``_fulltext`` shadow property + its full-text index.

        FalkorDB's full-text index only indexes **scalar** string properties,
        not the LIST-valued RDF properties this adapter stores (n10s ARRAY
        parity). So we concatenate every string value on each :Resource node
        into a single scalar ``_fulltext`` property and index that. The original
        array properties are untouched (the entity/schema mixins read them).

        Synchronous + best effort: a failure here never breaks load_rdf.
        """
        try:
            rows = await self._run(
                f"MATCH (n:Resource) RETURN n AS n LIMIT {_FT_SCAN_LIMIT}"
            )
            if len(rows) >= _FT_SCAN_LIMIT:
                logger.warning(
                    "FalkorDB full-text rebuild hit the %d-node scan cap; nodes "
                    "beyond it are not searchable. Page the rebuild if needed.",
                    _FT_SCAN_LIMIT,
                )
            updates: list[dict[str, str]] = []
            for r in rows:
                node = r.get("n")
                if not isinstance(node, dict):
                    continue
                uri = node.get("uri")
                if not uri:
                    continue
                parts: list[str] = []
                for k, v in node.items():
                    if k == "uri" or k.startswith("_"):
                        continue
                    for x in v if isinstance(v, list) else [v]:
                        if isinstance(x, str) and x:
                            parts.append(x.split("@")[0])
                text = " ".join(p for p in parts if p)
                if text:
                    updates.append({"uri": uri, "ft": text})
            if not updates:
                return
            await self._run_write(
                "UNWIND $rows AS r MATCH (n:Resource {uri: r.uri}) "
                "SET n._fulltext = r.ft",
                rows=updates,
            )
            # Drop + (re)create the scalar full-text index.
            try:
                await self._run_write("CALL db.idx.fulltext.drop('Resource')")
            except Exception:  # noqa: BLE001 — no index yet / unsupported drop
                pass
            await self._run_write(
                "CALL db.idx.fulltext.createNodeIndex('Resource', '_fulltext')"
            )
            logger.info("FalkorDB full-text index built on _fulltext (%d nodes).", len(updates))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to ensure FalkorDB full-text index: %s", exc)

    async def search_text(  # type: ignore[override]
        self: "FalkorDBStore",
        query: str,
        class_uri: str | None = None,
        limit: int = 20,
        ontology: str | None = None,
    ) -> list[SearchHit]:
        """Full-text search → ranked SearchHit list (subClassOf-aware on class_uri)."""
        await self._ensure_prefix_map()
        internal_limit = max(limit * 5, limit + 50)
        scope_frag, scope_params = ontology_scope_filter(ontology, node_alias="node")
        scope_and = f" AND {scope_frag}" if scope_frag else ""

        if class_uri is not None:
            cypher = f"""
            CALL db.idx.fulltext.queryNodes('Resource', $q) YIELD node, score
            WHERE (node)-[:rdf__type]->()-[:rdfs__subClassOf*0..{_MAX_DEPTH_HARD}]->(:Resource {{uri: $class_uri}}){scope_and}
            OPTIONAL MATCH (node)-[:rdf__type]->(cls:Resource)
            RETURN node.uri AS uri, node.rdfs__label AS raw_label, cls.uri AS cls_uri, score
            ORDER BY score DESC LIMIT $limit
            """
            params: dict[str, Any] = {
                "q": query, "class_uri": class_uri, "limit": internal_limit, **scope_params,
            }
        else:
            cypher = f"""
            CALL db.idx.fulltext.queryNodes('Resource', $q) YIELD node, score
            {("WHERE " + scope_frag) if scope_frag else ""}
            OPTIONAL MATCH (node)-[:rdf__type]->(cls:Resource)
            RETURN node.uri AS uri, node.rdfs__label AS raw_label, cls.uri AS cls_uri, score
            ORDER BY score DESC LIMIT $limit
            """
            params = {"q": query, "limit": internal_limit, **scope_params}

        try:
            rows = await self._run(cypher, **params)
        except Exception as exc:  # noqa: BLE001 — no index / repopulating
            logger.warning("FalkorDB full-text query failed (%s); returning []", exc)
            return []

        tbox = set(self._tbox_type_list)
        seen: dict[str, SearchHit] = {}
        for row in rows:
            uri = row.get("uri")
            if not uri:
                continue
            score = float(row.get("score") or 0.0)
            cls_raw = row.get("cls_uri")
            cls_hit = cls_raw if cls_raw and cls_raw not in tbox else None
            prev = seen.get(uri)
            if prev is not None:
                if cls_hit and prev.class_uri is None:
                    prev = prev.model_copy(update={"class_uri": cls_hit})
                    seen[uri] = prev
                if prev.score >= score:
                    continue
            raw_label = row.get("raw_label")
            label: str | None = None
            if raw_label is not None:
                lv = first_scalar(raw_label)
                if lv is not None:
                    label = str(lv).split("@")[0]
            seen[uri] = SearchHit(
                uri=uri,
                label=label,
                class_uri=cls_hit or (prev.class_uri if prev else None),
                score=score,
            )
        return sorted(seen.values(), key=lambda h: h.score, reverse=True)[:limit]
