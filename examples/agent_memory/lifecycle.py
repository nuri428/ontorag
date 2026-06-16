"""메모리 생명주기 관리 — prune / cleanup / dump.

기능:
  prune(older_than_months)   N개월 이상 된 트리플 삭제
  cleanup_workspace()        현재 워크스페이스 전체 삭제
  cleanup_project(uri)       특정 프로젝트 관련 트리플만 삭제
  dump(format, output)       TTL / JSON-LD / N-Triples 내보내기

전제: assert_memory()로 저장된 트리플에는 ag:assertedAt 타임스탬프가 있음.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from identity import AgentIdentity
from normalizer import P


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutoff_iso(months: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=months * 30)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class MemoryLifecycle:
    """메모리 생명주기 관리자."""

    def __init__(self, store: object, identity: AgentIdentity) -> None:
        self._store = store
        self._id = identity

    # ── assert_memory: 타임스탬프 자동 부착 ───────────────────────────────────

    async def assert_memory(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        object_is_uri: bool = False,
        ttl_months: int | None = None,
    ) -> None:
        """타임스탬프 + 세션 태그를 자동 부착하는 assert_triple 래퍼."""
        now = _now_iso()
        triples: list[tuple[str, str, str, bool]] = [
            (subject, predicate, obj, object_is_uri),
            (subject, P.ASSERTED_AT, now, False),
            (subject, P.IN_SESSION, self._id.session_uri, True),
            (subject, P.WORKSPACE, self._id.workspace, False),
        ]
        if ttl_months is not None:
            expires = _cutoff_iso(-ttl_months)  # 미래 날짜
            triples.append((subject, P.EXPIRES_AT, expires, False))

        await self._store.assert_triples(triples, ontology=self._id.ontology_id)

    async def assert_memories(
        self,
        triples: list[tuple[str, str, str, bool]],
        *,
        ttl_months: int | None = None,
    ) -> int:
        """여러 트리플을 한 번에 저장 (각 subject에 메타 자동 부착)."""
        now = _now_iso()
        enriched: list[tuple[str, str, str, bool]] = []
        seen_subjects: set[str] = set()

        for s, p, o, is_uri in triples:
            enriched.append((s, p, o, is_uri))
            if s not in seen_subjects:
                enriched.append((s, P.ASSERTED_AT, now, False))
                enriched.append((s, P.IN_SESSION, self._id.session_uri, True))
                enriched.append((s, P.WORKSPACE, self._id.workspace, False))
                if ttl_months is not None:
                    expires = _cutoff_iso(-ttl_months)
                    enriched.append((s, P.EXPIRES_AT, expires, False))
                seen_subjects.add(s)

        await self._store.assert_triples(enriched, ontology=self._id.ontology_id)
        return len(triples)

    # ── Prune: N개월 이상 된 노드 삭제 ───────────────────────────────────────

    async def prune(
        self,
        older_than_months: int = 6,
        *,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """N개월 이상 된 트리플 삭제.

        Args:
            older_than_months: 이 기간보다 오래된 노드를 삭제.
            dry_run: True이면 삭제 대신 대상 목록만 반환.

        Returns:
            {"subjects": N, "triples": M} — 삭제된 수량.
        """
        cutoff = _cutoff_iso(older_than_months)
        graph = self._id.graph_uri

        # 만료된 subject 목록 조회
        count_q = f"""
SELECT (COUNT(DISTINCT ?s) AS ?subjects) (COUNT(*) AS ?triples)
WHERE {{
  GRAPH <{graph}> {{
    ?s <{P.ASSERTED_AT}> ?t .
    FILTER(?t < "{cutoff}"^^<http://www.w3.org/2001/XMLSchema#dateTime>)
    ?s ?p ?o .
  }}
}}"""
        rows = await self._sparql_select(count_q)
        subjects_n = int(rows[0].get("subjects", {}).get("value", 0)) if rows else 0
        triples_n  = int(rows[0].get("triples", {}).get("value", 0)) if rows else 0

        if dry_run:
            return {"subjects": subjects_n, "triples": triples_n, "dry_run": True}

        if subjects_n == 0:
            return {"subjects": 0, "triples": 0}

        delete_q = f"""
DELETE {{
  GRAPH <{graph}> {{ ?s ?p ?o . }}
}}
WHERE {{
  GRAPH <{graph}> {{
    ?s <{P.ASSERTED_AT}> ?t .
    FILTER(?t < "{cutoff}"^^<http://www.w3.org/2001/XMLSchema#dateTime>)
    ?s ?p ?o .
  }}
}}"""
        await self._store._sparql_update(delete_q)
        return {"subjects": subjects_n, "triples": triples_n}

    # ── Cleanup: 워크스페이스 / 프로젝트 삭제 ────────────────────────────────

    async def cleanup_workspace(self, *, confirm: bool = False) -> dict[str, object]:
        """현재 워크스페이스 named graph 전체 삭제.

        Args:
            confirm: 안전장치 — True로 명시해야 실제 삭제.

        Returns:
            {"graph": uri, "deleted": bool}
        """
        if not confirm:
            return {
                "graph": self._id.graph_uri,
                "deleted": False,
                "message": "confirm=True 로 재호출해야 실제 삭제됩니다.",
            }

        removed = await self._store.clear_graph("data", ontology=self._id.ontology_id)
        return {
            "graph": self._id.graph_uri,
            "deleted": True,
            "triples_removed": removed.get("data", 0),
        }

    async def cleanup_project(
        self,
        project_uri: str,
        *,
        confirm: bool = False,
    ) -> dict[str, object]:
        """특정 프로젝트 URI가 subject 또는 object인 트리플을 삭제.

        직접 연결 트리플만 삭제 — 전이적 삭제는 하지 않음 (안전).
        """
        graph = self._id.graph_uri

        # 삭제 대상 수 확인
        count_q = f"""
SELECT (COUNT(*) AS ?n) WHERE {{
  GRAPH <{graph}> {{
    {{ <{project_uri}> ?p ?o . }}
    UNION
    {{ ?s ?p <{project_uri}> . }}
  }}
}}"""
        rows = await self._sparql_select(count_q)
        count = int(rows[0].get("n", {}).get("value", 0)) if rows else 0

        if not confirm:
            return {
                "project_uri": project_uri,
                "triples_to_delete": count,
                "deleted": False,
                "message": "confirm=True 로 재호출해야 실제 삭제됩니다.",
            }

        if count == 0:
            return {"project_uri": project_uri, "triples_to_delete": 0, "deleted": True}

        delete_q = f"""
DELETE {{
  GRAPH <{graph}> {{
    ?s ?p ?o .
  }}
}}
WHERE {{
  GRAPH <{graph}> {{
    {{
      <{project_uri}> ?p ?o .
      BIND(<{project_uri}> AS ?s)
    }}
    UNION
    {{
      ?s ?p <{project_uri}> .
      BIND(?o AS ?o)
    }}
  }}
}}"""
        await self._store._sparql_update(delete_q)
        return {
            "project_uri": project_uri,
            "triples_deleted": count,
            "deleted": True,
        }

    # ── Dump: 내보내기 ────────────────────────────────────────────────────────

    async def dump(
        self,
        fmt: Literal["turtle", "jsonld", "ntriples"] = "turtle",
        output_path: str | None = None,
        *,
        session_only: bool = False,
    ) -> str:
        """메모리를 파일로 내보내기.

        Args:
            fmt: 출력 포맷 ("turtle" | "jsonld" | "ntriples").
            output_path: 저장할 파일 경로. None이면 자동 생성.
            session_only: True이면 현재 세션 트리플만 내보내기.

        Returns:
            저장된 파일 경로.
        """
        # 자동 파일명: memory_greennuri_ws-ontorag_20260615.ttl
        if output_path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = {"turtle": "ttl", "jsonld": "jsonld", "ntriples": "nt"}[fmt]
            suffix = f"_session_{self._id.session_id}" if session_only else ""
            output_path = f"memory_{self._id.user}_{self._id.workspace}{suffix}_{ts}.{ext}"

        graph = self._id.graph_uri

        if session_only:
            # 현재 세션 트리플만 CONSTRUCT로 추출
            session_uri = self._id.session_uri
            construct_q = f"""
CONSTRUCT {{ ?s ?p ?o }}
WHERE {{
  GRAPH <{graph}> {{
    ?s <{P.IN_SESSION}> <{session_uri}> .
    ?s ?p ?o .
  }}
}}"""
            content = await self._sparql_construct(construct_q, fmt)
        else:
            # named graph 전체 덤프 (ontorag dump_graph 활용)
            fmt_map = {"turtle": "turtle", "jsonld": "json-ld", "ntriples": "nt"}
            raw = await self._store.dump_graph(
                fmt_map[fmt], ontology=self._id.ontology_id
            )
            content = raw.decode() if isinstance(raw, bytes) else raw

        Path(output_path).write_text(content, encoding="utf-8")
        return output_path

    # ── 메모리 현황 요약 ──────────────────────────────────────────────────────

    async def stats(self) -> dict[str, object]:
        """현재 메모리 현황 통계."""
        graph = self._id.graph_uri
        q = f"""
SELECT
  (COUNT(DISTINCT ?s) AS ?subjects)
  (COUNT(*) AS ?triples)
  (MIN(?t) AS ?oldest)
  (MAX(?t) AS ?newest)
WHERE {{
  GRAPH <{graph}> {{
    OPTIONAL {{ ?s <{P.ASSERTED_AT}> ?t . }}
    ?s ?p ?o .
  }}
}}"""
        rows = await self._sparql_select(q)
        if not rows:
            return {"subjects": 0, "triples": 0}

        row = rows[0]
        return {
            "graph": graph,
            "identity": str(self._id),
            "subjects": int(row.get("subjects", {}).get("value", 0)),
            "triples":  int(row.get("triples", {}).get("value", 0)),
            "oldest":   row.get("oldest", {}).get("value", "—"),
            "newest":   row.get("newest", {}).get("value", "—"),
        }

    # ── 내부 SPARQL 유틸 ──────────────────────────────────────────────────────

    async def _sparql_select(self, query: str) -> list[dict]:
        import httpx
        auth = httpx.BasicAuth(
            os.environ.get("FUSEKI_USER", "admin"),
            os.environ.get("FUSEKI_PASSWORD", "admin"),
        )
        async with httpx.AsyncClient(auth=auth, timeout=15.0) as client:
            resp = await client.post(
                f"{os.environ.get('FUSEKI_URL', 'http://localhost:3030')}"
                f"/{os.environ.get('FUSEKI_DATASET', 'ontorag')}/sparql",
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )
            resp.raise_for_status()
            return resp.json()["results"]["bindings"]

    async def _sparql_construct(self, query: str, fmt: str) -> str:
        import httpx
        fmt_map = {
            "turtle":   "text/turtle",
            "jsonld":   "application/ld+json",
            "ntriples": "application/n-triples",
        }
        auth = httpx.BasicAuth(
            os.environ.get("FUSEKI_USER", "admin"),
            os.environ.get("FUSEKI_PASSWORD", "admin"),
        )
        async with httpx.AsyncClient(auth=auth, timeout=15.0) as client:
            resp = await client.post(
                f"{os.environ.get('FUSEKI_URL', 'http://localhost:3030')}"
                f"/{os.environ.get('FUSEKI_DATASET', 'ontorag')}/sparql",
                data={"query": query},
                headers={"Accept": fmt_map[fmt]},
            )
            resp.raise_for_status()
            return resp.text
