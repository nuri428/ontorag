"""관계 발견 데모 — 파일 기반 메모리 vs 온톨로지 기반 메모리 비교.

실행:
    uv run python examples/agent_memory/demo_relationship_discovery.py

Phase 1: 현재 Claude Code 메모리 파일을 온톨로지 트리플로 적재 (노말라이즈 포함)
Phase 2: 파일로는 할 수 없는 관계 발견 쿼리 3개 시연
"""

from __future__ import annotations

import asyncio
import os
import textwrap

import httpx

from normalizer import P, label_of, resolve

# ── 환경 설정 ─────────────────────────────────────────────────────────────────

os.environ.setdefault("FUSEKI_URL", "http://localhost:3030")
os.environ.setdefault("FUSEKI_DATASET", "ontorag")
os.environ.setdefault("FUSEKI_USER", "admin")
os.environ.setdefault("FUSEKI_PASSWORD", "admin")

ONTOLOGY = "agentmem"   # 전용 named graph — 기존 온톨로지 오염 없음

# ── 트리플 정의 (메모리 파일에서 추출 + 오늘 대화 내용 추가) ───────────────────

def _triples() -> list[tuple[str, str, str, bool]]:
    """(subject, predicate, object, object_is_uri) 형식의 트리플 목록."""
    r = resolve  # shorthand

    return [
        # ── 프로젝트 구조 (project_ontorag_workspace.md) ──────────────────
        (r("ontorag"),       P.LABEL,      "ontorag v1.1.1 — Ontology-aware RAG",  False),
        (r("ontorag"),       P.LAYER,      r("Semantic"),                           True),
        (r("ontorag"),       P.LAYER,      r("Dynamic"),                            True),
        (r("ontorag"),       P.USES,       r("RDF"),                                True),
        (r("ontorag"),       P.USES,       r("SPARQL"),                             True),
        (r("ontorag"),       P.USES,       r("MCP"),                                True),

        (r("ontorag-flow"),  P.LABEL,      "ontorag-flow v0.1.0 — Kinetic Layer",  False),
        (r("ontorag-flow"),  P.LAYER,      r("Kinetic"),                            True),
        (r("ontorag-flow"),  P.DEPENDS_ON, r("ontorag"),                            True),
        (r("ontorag-flow"),  P.CONCEPT,    r("ACM"),                                True),
        (r("ontorag-flow"),  P.CONCEPT,    r("PROV-O"),                             True),
        (r("ontorag-flow"),  P.USES,       r("MCP"),                                True),

        (r("ontorag-demo"),  P.DEPENDS_ON, r("ontorag"),                            True),
        (r("ontorag-demo"),  P.DEPENDS_ON, r("ontorag-flow"),                       True),

        # ── 제품 방향 (project_ontorag_direction.md) ─────────────────────
        (r("patent_board"),  P.LABEL,      "patent_board — B2B 특허 제품 (24h 운영)", False),
        (r("patent_board"),  P.TARGET,     r("ontorag"),                            True),
        (r("patent_board"),  P.RELATED_TO, r("SRE"),                               True),
        (r("patent_board"),  P.DESCRIPTION,"매일 배포 + 인시던트 발생 → 포스트모템 타깃", False),

        # SRE 포스트모템이 patent_board와 ontorag를 연결하는 개념
        (r("SRE"),           P.INVOLVES,   r("patent_board"),                       True),
        (r("SRE"),           P.INVOLVES,   r("ontorag"),                            True),
        (r("SRE"),           P.CONCEPT,    r("PROV-O"),                             True),

        # ── 오늘 대화에서 내린 결정들 (2026-06-15) ────────────────────────
        (
            "urn:ag:decision:2026-06-15:hermes-integration",
            P.LABEL, "Hermes Agent + ontorag 연동 결정",                            False,
        ),
        (
            "urn:ag:decision:2026-06-15:hermes-integration",
            P.MADE_AT, "2026-06-15",                                               False,
        ),
        (
            "urn:ag:decision:2026-06-15:hermes-integration",
            P.INVOLVES,  r("Hermes Agent"),                                        True,
        ),
        (
            "urn:ag:decision:2026-06-15:hermes-integration",
            P.INVOLVES,  r("ontorag"),                                             True,
        ),
        (
            "urn:ag:decision:2026-06-15:hermes-integration",
            P.INVOLVES,  r("ontorag-flow"),                                        True,
        ),
        (
            "urn:ag:decision:2026-06-15:hermes-integration",
            P.RATIONALE, "구조화된 메모리 + ACM 오케스트레이션으로 에이전트 장기 기억 구현", False,
        ),
        (
            "urn:ag:decision:2026-06-15:hermes-integration",
            P.ENABLES,  "urn:ag:decision:2026-06-15:write-mcp",                   True,
        ),

        (
            "urn:ag:decision:2026-06-15:write-mcp",
            P.LABEL,    "ontorag MCP write 툴 구현 (assert/retract_triple)",       False,
        ),
        (
            "urn:ag:decision:2026-06-15:write-mcp",
            P.INVOLVES, r("ontorag"),                                              True,
        ),
        (
            "urn:ag:decision:2026-06-15:write-mcp",
            P.INVOLVES, r("MCP"),                                                  True,
        ),
        (
            "urn:ag:decision:2026-06-15:write-mcp",
            P.RATIONALE, "Claude Code가 온톨로지에 직접 메모리를 저장할 수 있게",   False,
        ),

        # Hermes가 patent_board SRE 유즈케이스와도 연결됨
        (r("Hermes Agent"),  P.RELATED_TO, r("SRE"),                               True),
        (r("Hermes Agent"),  P.USES,       r("MCP"),                               True),
    ]


# ── SPARQL 유틸 ────────────────────────────────────────────────────────────────

async def _sparql(query: str) -> list[dict]:
    auth = httpx.BasicAuth(
        os.environ.get("FUSEKI_USER", "admin"),
        os.environ.get("FUSEKI_PASSWORD", "admin"),
    )
    async with httpx.AsyncClient(auth=auth, timeout=15.0) as client:
        resp = await client.post(
            f"{os.environ['FUSEKI_URL']}/{os.environ['FUSEKI_DATASET']}/sparql",
            data={"query": query},
            headers={"Accept": "application/sparql-results+json"},
        )
        resp.raise_for_status()
        return resp.json()["results"]["bindings"]


def _val(row: dict, key: str) -> str:
    return row.get(key, {}).get("value", "")


# ── Phase 1: 적재 ─────────────────────────────────────────────────────────────

async def phase1_load() -> None:
    from ontorag.stores.fuseki import FusekiStore

    store = FusekiStore.from_env()
    triples = _triples()
    count = await store.assert_triples(triples, ontology=ONTOLOGY)
    await store.aclose()
    print(f"[Phase 1] {count}개 트리플 적재 완료 → graph: urn:ontorag:{ONTOLOGY}:data\n")


# ── Phase 2: 관계 발견 데모 ────────────────────────────────────────────────────

async def demo_q1_direct_connection() -> None:
    """Q1: ontorag와 patent_board는 직접 연결되어 있나?"""
    print("=" * 60)
    print("Q1. ontorag ↔ patent_board 직접 연결 탐색")
    print("    (파일 기반: 두 메모리 파일을 읽고 LLM이 추론)")
    print("-" * 60)

    ontorag_uri = resolve("ontorag")
    patent_uri  = resolve("patent_board")
    graph_uri   = f"urn:ontorag:{ONTOLOGY}:data"

    q = f"""
SELECT ?rel ?label WHERE {{
  GRAPH <{graph_uri}> {{
    {{
      <{patent_uri}> ?rel <{ontorag_uri}> .
    }} UNION {{
      <{ontorag_uri}> ?rel <{patent_uri}> .
    }}
    OPTIONAL {{ <{patent_uri}> <{P.LABEL}> ?label . }}
  }}
}}"""
    rows = await _sparql(q)
    if rows:
        for row in rows:
            rel = _val(row, "rel").split(":")[-1]
            print(f"  → {label_of(patent_uri)} --[{rel}]--> {label_of(ontorag_uri)}")
    else:
        print("  직접 연결 없음")
    print()


async def demo_q2_decision_chain() -> None:
    """Q2: 오늘(2026-06-15) 내린 결정들이 어떤 프로젝트와 연결되나?"""
    print("=" * 60)
    print("Q2. 2026-06-15 결정들 → 관련 프로젝트/기술 전부 나열")
    print("    (파일 기반: 불가. 날짜 기반 쿼리는 텍스트 파싱 필요)")
    print("-" * 60)

    graph_uri = f"urn:ontorag:{ONTOLOGY}:data"
    q = f"""
SELECT DISTINCT ?decision_label ?entity_label WHERE {{
  GRAPH <{graph_uri}> {{
    ?decision <{P.MADE_AT}> "2026-06-15" .
    ?decision <{P.LABEL}>   ?decision_label .
    ?decision <{P.INVOLVES}> ?entity .
    ?entity   <{P.LABEL}>   ?entity_label .
  }}
}}
ORDER BY ?decision_label"""
    rows = await _sparql(q)
    current_decision = None
    for row in rows:
        dec = _val(row, "decision_label")
        ent = _val(row, "entity_label")
        if dec != current_decision:
            print(f"\n  결정: {dec}")
            current_decision = dec
        print(f"    └── 관련: {ent}")
    print()


async def demo_q3_multi_hop() -> None:
    """Q3: Hermes → ? → patent_board 경로 (2홉 이내) — 파일로는 불가능."""
    print("=" * 60)
    print("Q3. Hermes Agent → ??? → patent_board  (2홉 경로)")
    print("    (파일 기반: 완전히 불가. 두 파일에 Hermes 언급 없음)")
    print("-" * 60)

    hermes_uri = resolve("Hermes Agent")
    patent_uri = resolve("patent_board")
    graph_uri  = f"urn:ontorag:{ONTOLOGY}:data"

    q = f"""
SELECT ?mid ?rel1 ?rel2 ?mid_label WHERE {{
  GRAPH <{graph_uri}> {{
    <{hermes_uri}> ?rel1 ?mid .
    ?mid           ?rel2 <{patent_uri}> .
    OPTIONAL {{ ?mid <{P.LABEL}> ?mid_label . }}
  }}
}}"""
    rows = await _sparql(q)
    if rows:
        print(f"  {label_of(hermes_uri)}")
        for row in rows:
            mid       = _val(row, "mid")
            mid_label = _val(row, "mid_label") or label_of(mid)
            rel1      = _val(row, "rel1").split(":")[-1]
            rel2      = _val(row, "rel2").split(":")[-1]
            print(f"    --[{rel1}]--> {mid_label} --[{rel2}]--> {label_of(patent_uri)}")
    else:
        print("  경로를 찾을 수 없음")
    print()


async def demo_q4_why_mcp() -> None:
    """Q4: MCP를 사용하는 결정의 rationale — 출처 추적."""
    print("=" * 60)
    print("Q4. MCP를 포함한 결정들의 rationale (출처 추적)")
    print("    (파일 기반: 전체 파일 읽고 키워드 매칭 — 느리고 부정확)")
    print("-" * 60)

    mcp_uri   = resolve("MCP")
    graph_uri = f"urn:ontorag:{ONTOLOGY}:data"

    q = f"""
SELECT ?label ?rationale WHERE {{
  GRAPH <{graph_uri}> {{
    ?decision <{P.INVOLVES}>   <{mcp_uri}> .
    ?decision <{P.LABEL}>      ?label .
    ?decision <{P.RATIONALE}>  ?rationale .
  }}
}}"""
    rows = await _sparql(q)
    for row in rows:
        label     = _val(row, "label")
        rationale = _val(row, "rationale")
        print(f"  결정: {label}")
        print(f"  이유: {textwrap.fill(rationale, width=56, initial_indent='        ', subsequent_indent='        ')}")
        print()


# ── 파일 기반 방식 시뮬레이션 ─────────────────────────────────────────────────

def show_file_based_limitation() -> None:
    print("=" * 60)
    print("※ 파일 기반 방식의 한계 요약")
    print("-" * 60)
    items = [
        ("Q1 직접 연결", "가능 (2개 파일 읽고 LLM 추론)", "~800 토큰 소비"),
        ("Q2 날짜별 쿼리", "불가 (날짜가 구조화 안 됨)", "~2000 토큰 소비"),
        ("Q3 2홉 경로", "불가 (Hermes는 별도 파일 없음)", "N/A — 파일 없음"),
        ("Q4 출처 추적", "불가 (rationale 구조 없음)", "~1200 토큰 소비"),
    ]
    for q, capability, cost in items:
        print(f"  {q:<15} | {capability:<35} | {cost}")
    print()


# ── 메인 ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("\n" + "=" * 60)
    print("  관계 발견 데모: 파일 기반 vs 온톨로지 기반 메모리")
    print("=" * 60 + "\n")

    await phase1_load()

    await demo_q1_direct_connection()
    await demo_q2_decision_chain()
    await demo_q3_multi_hop()
    await demo_q4_why_mcp()

    show_file_based_limitation()

    print("✓ 데모 완료")


if __name__ == "__main__":
    asyncio.run(main())
