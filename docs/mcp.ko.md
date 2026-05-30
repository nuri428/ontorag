# MCP & 툴

ontorag는 온톨로지 기반 기능을 **Model Context Protocol**을 통해 노출합니다.
두 가지 전송 방식이 있으며, 같은 핸들러 코드(`create_store()` +
`GraphStore` 프로토콜 + 추론 엔진)를 공유합니다:

| 전송 | 엔드포인트 | 사용 시점 |
|---|---|---|
| **HTTP / SSE** (내장) | `http://localhost:8000/mcp` | ontorag가 이미 FastAPI 서버로 실행 중일 때 |
| **stdio** (v1.1, `[mcp]` extra) | `ontorag-mcp` 콘솔 스크립트 | 서버 실행 없이 Claude Desktop / Cursor / Claude Code에 바로 연결 |

## 18개 툴

3개 레이어로 구성됩니다 — LLM은 **L1 + L2**만 봅니다. Raw SPARQL(L3)은
개발자 전용입니다.

### L1 — 의도 기반 (90% 케이스)

| 툴 | 역할 |
|---|---|
| `get_schema` | 압축된 클래스 + 속성 계층 (~30 tokens/class). |
| `get_class_detail` | 특정 클래스 상세 — 속성·부모·자식·인스턴스 샘플. |
| `find_entities` | 클래스 + 필터 → 인스턴스 (subClassOf 추론 포함). |
| `describe_entity` | 한 엔티티의 모든 속성·관계 (inverseOf 포함). |
| `count_entities` | 인스턴스 개수, 필터 옵션. |
| `aggregate` | `group_by` + 집계 함수 (`count`/`sum`/`avg`/`min`/`max`). |
| `traverse_graph` | 그래프 순회 (TransitiveProperty 존중). |
| `find_path` | 두 엔티티 간 최단 경로. |
| `find_related` | 두 클래스 간 멀티홉 조인. |
| `search_text` | BM25 풀텍스트 (jena-text / Neo4j fulltext / FalkorDB fulltext). |
| `find_similar` | 벡터 kNN (구조 FastRP + 텍스트 + RRF 하이브리드). |
| `find_aligned` | `owl:sameAs` 전이+대칭 폐포 (교차 온톨로지). |
| `type_term` (v0.3) | 텍스트 멘션 → TBox 클래스 매핑. |
| `extract_triples` (v0.3) | 텍스트에서 RDF 트리플 제안, 스키마로 검증. |
| `compute_posterior` (v0.7) | pgmpy 기반 베이지안 P(Q \| E). |
| `mpe` (v0.7) | 최대 사후 설명(most-probable explanation). |
| `do_query` (v0.8) | Pearl Rung 2 개입 — 그래프 수술 + 백도어 보정. **v1.1부터 보정 집합 + "why" 트레이스 동반**. |
| `identify_effect` (v0.8) | 최소 백도어 + 모든 프론트도어 보정 집합. |
| `counterfactual` (v0.8) | Pearl Rung 3 — abduction · action · prediction. |

### L2 — JSON DSL 탈출구 (10% 케이스)

`query_pattern` — JSON 트리플 패턴을 내부에서 안전한 SPARQL/Cypher로 번역.
구조상 injection 불가.

### L3 — raw SPARQL (개발자 전용, 미노출)

`query_sparql_raw`는 curl 디버깅용으로 API에는 존재하지만
`exclude_operations`로 LLM에는 도달하지 않습니다.

## stdio MCP — Claude Desktop / Cursor 설정

`uv sync --extra mcp` 후:

=== "Claude Desktop"

    `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

    ```json
    {
      "mcpServers": {
        "ontorag": {
          "command": "ontorag-mcp",
          "env": {
            "GRAPH_STORE": "fuseki",
            "FUSEKI_URL": "http://localhost:3030/ontorag"
          }
        }
      }
    }
    ```

=== "Cursor"

    `~/.cursor/mcp.json`:

    ```json
    {
      "mcpServers": {
        "ontorag": {
          "command": "ontorag-mcp",
          "env": { "GRAPH_STORE": "neo4j",
                   "NEO4J_URL": "bolt://localhost:7687",
                   "NEO4J_USER": "neo4j",
                   "NEO4J_PASSWORD": "***" }
        }
      }
    }
    ```

=== "Claude Code"

    `~/.claude.json`의 `mcpServers`에 추가 (위와 동일한 형태).

클라이언트를 재시작하면 ontorag 툴이 툴 팔레트에 나타납니다. stdio 서버는
핵심 read 툴 + `compute_posterior` + `do_query`를 노출하고, raw SPARQL은
제외됩니다 (HTTP `/mcp`와 동일 정책).

## 검증

```bash
# 엔드투엔드 빠른 체크
ontorag-mcp <<<'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Claude Desktop / Cursor에서:

> *모든 포켓몬을 나열한 뒤, 상대 타입이 Water일 때 BattleOutcome의 사후확률을 계산해줘.*

클라이언트가 `find_entities` → `compute_posterior`를 호출하고 결과를
스트리밍합니다.

## 백엔드 스왑

위 모든 툴은 **3개 백엔드에서 동일하게 동작합니다** — `GRAPH_STORE`만
바꾸고 재시작하면 됩니다. v1.0 벤치마크는 Fuseki / Neo4j / FalkorDB 사이에
프로토콜 메트릭 7/7이 일치함을 증명합니다.
[`docs/BENCHMARK_v1.md`](https://github.com/nuri428/ontorag/blob/main/docs/BENCHMARK_v1.md)
참조.
