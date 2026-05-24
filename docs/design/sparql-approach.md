# SPARQL 접근법 — LLM에게 raw SPARQL을 노출하지 않는 설계

## 핵심 원칙

**SPARQL을 안 쓰는 게 아니라, LLM에게서 격리한다.**

SPARQL은 여전히 내부 엔진이고 ontology의 추론 능력을 활용하는 핵심 인터페이스다. LLM에게는 (1) 도메인 의미가 명확한 고수준 툴과 (2) 구조적으로 안전한 패턴 DSL만 노출한다. 이것이 "ontology-aware"의 진짜 의미에 더 가깝다 — LLM이 SPARQL 문법을 다루는 게 아니라, ontology의 개념(class, property, relation)을 다루게 만드는 것.

이유:
- raw SPARQL을 LLM이 생성하면 인젝션 검증이 어렵다 (allowlist는 너무 제한적, 구조 validator는 corner case가 끝없음)
- LLM tool-calling 시대에 "SQL/SPARQL을 LLM이 직접 쓰게 하기"는 점점 안 좋은 패턴으로 평가받는 중
- 함수 호출은 LLM이 SPARQL 문법보다 훨씬 안정적으로 생성한다

## 3-레이어 아키텍처

### 레이어 1 — 의도(intent) 기반 고수준 툴 (90% 사용 케이스)

실제 SPARQL이 하는 일을 분해하면 다음 패턴으로 정리된다.

```python
# 클래스 + 필터 조회
find_entities(class_uri, filters=[{"property": "foaf:age", "op": ">", "value": 30}])

# 엔티티의 속성/관계
describe_entity(uri, predicates=None)  # predicates 지정 시 부분 조회

# 그래프 순회 (property path 대체)
traverse(start_uri, predicate="foaf:knows", max_depth=3, direction="outgoing")

# 두 엔티티 간 경로
find_path(uri_a, uri_b, max_depth=4)

# 집계
count_entities(class_uri, filters=[...])
aggregate(class_uri, group_by="foaf:nationality", agg="count")

# 조인 (멀티홉)
find_related(class_a, predicate, class_b, filters_a=[...], filters_b=[...])
```

**Multi-hop 질의는 tool chaining에 맡긴다.** LLM은 plan만 짜고, 각 step은 결정론적 툴 호출이다.

예시 — "삼성전자가 출원한 특허 중 IPC G06F이면서 동일 분류에서 인용 횟수가 많은 것":

```
1. find_entities(Patent, [{applicant: "Samsung"}, {ipc: "G06F"}])
   → [patent_1, patent_2, ...]
2. describe_entity(patent_1, predicates=["citations", "ipc"])
3. aggregate(...) 또는 LLM이 결과를 직접 정렬
```

이것이 Anthropic이 권장하는 "thin tools, smart agent" 패턴이다.

### 레이어 2 — 구조화된 JSON 쿼리 DSL (10% — 복잡한 경우)

고수준 툴로 표현이 안 되는 경우를 위한 안전망. raw SPARQL 대신 **JSON으로 triple pattern을 받아서 내부에서 SPARQL로 번역**한다.

```json
{
  "select": ["?person", "?paper", "?year"],
  "where": [
    {"s": "?person", "p": "rdf:type", "o": "ex:Researcher"},
    {"s": "?person", "p": "ex:authored", "o": "?paper"},
    {"s": "?paper", "p": "ex:year", "o": "?year"}
  ],
  "filters": [{"var": "?year", "op": ">=", "value": 2020}],
  "limit": 50
}
```

장점:
- **인젝션 불가능** — 구조가 고정이라 검증이 쉽다. predicate/class가 ontology에 존재하는지 화이트리스트 체크 가능
- **LLM 생성 신뢰도 ↑** — SPARQL 문법 외우게 하는 것보다 JSON schema 따르게 하는 게 훨씬 안정적 (function calling이 잘 하는 일)
- **변환은 결정론적** — `rdflib` 또는 직접 BGP builder로 1:1 매핑

`fastapi-mcp`로 노출하면 LLM은 그냥 또 하나의 tool로 인식한다. 툴 이름은 `query_pattern` 정도가 적당하다.

### 레이어 3 — raw SPARQL (LLM에 비노출, 내부 디버그)

`/tools/query`는 내부 디버그 / 어드민 / 개발자 escape hatch로만 둔다. MCP `operation_id`에서 제외해서 LLM이 호출할 수 없게 만든다 (`fastapi-mcp`의 include/exclude 옵션 활용). 개발자가 직접 curl로 두드리는 용도다.

## Ontology가 진짜 일하는 곳 — Inference Layer

여기서 ontorag만의 가치가 나온다. 위 모든 툴은 **inference가 적용된 view 위에서 동작**해야 한다.

- `find_entities(Animal)` → Dog/Cat 인스턴스도 자동 포함 (`rdfs:subClassOf` 추론)
- `find_entities(Person, filters=[{ex:livesIn: "Seoul"}])` → `ex:livesIn`이 `ex:residesIn`의 sub-property면 둘 다 매칭
- `describe_entity(person_uri)` → `owl:inverseOf` 관계도 함께 반환 (예: `ex:authoredBy` 역방향)
- `traverse(uri, predicate="ex:partOf")` → `owl:TransitiveProperty`면 전이 closure 자동 포함

Fuseki에서 활성화하는 방법: `ja:OntModelSpec` 설정으로 RDFS/OWL inference model을 켠다. 그러면 SPARQL 엔진이 알아서 inferred triple까지 매칭해주고, **툴 구현은 변하지 않는다.**

이게 단순 GraphRAG와의 차별점이다 — GraphRAG는 graph 구조만 보지만, ontorag는 OWL 시맨틱을 통과한 추론 결과 위에서 동작한다.

## ontorag에 권장하는 구체적 디렉터리 구조

현재 `src/ontorag/api/routes/tools/` 구조에 다음과 같이 적용한다.

```
tools/
├── schema.py        # GET  /tools/schema          (기존)
├── entities.py      # POST /tools/entities/find
│                    # GET  /tools/entities/{uri}
│                    # POST /tools/entities/count
├── traversal.py     # POST /tools/traverse
│                    # POST /tools/path
├── pattern.py       # POST /tools/query/pattern  ← 레이어 2 추가
└── _query.py        # POST /tools/query/sparql   ← MCP exclude
```

핵심 변경 — `query_ontology(sparql)`를 `query_pattern(json_dsl)`로 대체. `_query.py`는 언더스코어로 시작해서 `fastapi-mcp`가 무시하게 하거나, mount 시 명시적 exclude 한다.

## LLM에 노출할 최종 MCP 툴 셋 (제안)

| 툴 이름 | 레이어 | 용도 |
|--------|-------|-----|
| `get_schema` | 1 | Ontology 클래스/속성 구조 (compact form) |
| `find_entities` | 1 | 클래스 + 필터로 인스턴스 찾기 |
| `describe_entity` | 1 | 특정 엔티티의 속성/관계 (inference 포함) |
| `traverse_graph` | 1 | 그래프 순회 (property path) |
| `find_path` | 1 | 두 엔티티 간 경로 |
| `count_entities` / `aggregate` | 1 | 집계 |
| `query_pattern` | 2 | JSON DSL escape hatch |
| ~~`query_ontology`~~ | 3 | **LLM 비노출**, 디버그용 |

## 검증 체크리스트

- [ ] 모든 입력 URI가 ontology에 존재하는지 검증 (TBox 기준)
- [ ] 필터의 predicate가 해당 class의 valid property인지 체크 (`rdfs:domain` 활용)
- [ ] `limit` 기본값 강제 (무한 결과 방지)
- [ ] `describe_entity` 응답 크기 제한 (predicate 화이트리스트 또는 페이지네이션)
- [ ] `traverse`/`find_path`의 `max_depth` 상한 설정 (예: 6)
- [ ] inference 결과인 triple을 응답에 명시적으로 표시 (디버깅 용이성)
