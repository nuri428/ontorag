# Neo4j 백엔드 도입 — Ontology-Aware RAG에 두 번째 그래프 스토어 붙이기

> **한 줄 요약** — OWL/SPARQL 기반 RAG 프레임워크 `ontorag`에 Neo4j 백엔드를
> 추가하면서, **기존 Fuseki(SPARQL/TDB2) 백엔드와 capability parity** — 즉
> *추론·풀텍스트 검색·벡터 유사도*가 모두 동일하게 동작하도록 — 를
> 달성한 작업의 변천 과정.
>
> **결과** — 세 백엔드(Fuseki / Neo4j / FalkorDB)에서 동일 프로토콜 툴이
> *비트 단위로 동일한 결과*를 반환하며(`docs/BENCHMARK_v1.md`,
> `full_parity = True`), 이는 `ontorag`의 헤드라인 차별점으로
> 측정·문서화되어 v1.0의 핵심 근거가 되었다.

작성: 2026-06-07 · 대상 릴리스: v0.5.0 → v0.6.1 · 본 흐름이 v0.9 FalkorDB·v1.0 벤치마크의 토대가 됨.

---

## 0. 왜 이 작업이 의미가 있는가

`ontorag`는 "OWL을 1급 시민으로 다루는 RAG 프레임워크"를 표방한다. 기존
경쟁군과의 위치는 다음과 같다.

- **LangChain / LlamaIndex** — 코드-first RAG 라이브러리. 온톨로지가 중심이 아님.
- **GraphRAG (Microsoft)** — 비정형 텍스트에서 property graph를 구성. OWL
  시맨틱·SPARQL 추론·전이 추론·스키마 강제가 없음.
- **ontorag** — OWL native. TBox가 스키마를 정의하고, 모든 툴이 SPARQL 1.1을
  말한다. `rdfs:subClassOf`, `owl:TransitiveProperty`, `owl:inverseOf`가
  쿼리에 자연스럽게 흐른다.

이 정체성을 유지하면서 그래프 스토어를 갈아 끼우려면, **단순한 어댑터 추가가
아니라 "추상화의 진정성"이 시험대에 오른다.** Neo4j는 property graph지
RDF triple store가 아니다. SPARQL이 아닌 Cypher를 쓰고, 노드/관계의 모델이
다르다. "Fuseki에서 잘 되던 게 Neo4j에서는 약간 다르다"를 허용하기 시작하면,
프레임워크의 "ontology가 source of truth"라는 약속이 무너진다.

이 글은 그 약속을 지키기 위해 어떤 결정들을 어떤 순서로 내렸는지에 대한
기록이다.

---

## 1. 출발점 — GraphStore Protocol이라는 추상 인터페이스

Phase 1(v0.1) 시점부터 `ontorag`는 `GraphStore`라는 Python `Protocol`을
정의해두었다. 모든 MCP 툴 — `find_entities`, `describe_entity`,
`count_entities`, `traverse`, `find_path`, `aggregate`, `query_pattern` 등 —
은 구체 스토어가 아니라 이 Protocol에만 의존한다.

```python
# src/ontorag/stores/base.py (요약)
class GraphStore(Protocol):
    async def load_rdf(self, path, mode, ontology=None) -> LoadResult: ...
    async def get_schema(self, ontology=None) -> SchemaResult: ...
    async def find_entities(
        self, class_uri, filters=None, limit=100, ontology=None,
    ) -> list[EntityResult]: ...
    async def describe_entity(self, uri, predicates=None) -> EntityResult: ...
    async def traverse(self, start_uri, predicate, max_depth, direction) -> TraversalResult: ...
    async def query_pattern(self, query: PatternQuery) -> QueryResult: ...
    # ... 추론·검색·임베딩 capability tools (search_text, find_similar, ...)
```

선택지로는 두 가지가 있었다.

| 선택지 | 단점 |
|---|---|
| (A) Fuseki 코드를 직접 호출하는 구조에서 시작해 나중에 추상화 | 어댑터 도입 시점에 거대한 리팩토링이 발생. 모든 라우트·테스트가 깨짐. |
| (B) 처음부터 Protocol을 선언하고 Fuseki 어댑터가 그것을 구현 | 단일 백엔드 시점에는 약간의 over-engineering처럼 보임. |

(B)를 택했다. 결과적으로 **Neo4j 도입 단계에서 라우트·CLI·테스트 코드를
한 줄도 손대지 않아도 됐다.** 어댑터 클래스 하나 + factory의 분기 한 줄로
교체가 가능했다.

```python
# src/ontorag/stores/factory.py (요약)
def create_store(graph_store: str) -> GraphStore:
    if graph_store == "fuseki":   return FusekiStore(...)
    if graph_store == "neo4j":    return Neo4jStore(...)
    if graph_store == "falkordb": return FalkorDBStore(...)  # v0.9에서 같은 길로 추가됨
    raise ValueError(...)
```

이 추상화의 진정한 가치는 Neo4j 작업 이후 FalkorDB 백엔드(v0.9)를 또 추가할
때 증명되었다 — **Neo4j용 L1 + reasoning mixin이 거의 그대로 재사용**됐다
(FalkorDB도 OpenCypher). 한 번 잘 깎은 추상은 두 번 보답한다.

---

## 2. 1단계 — n10s 어댑터 (RDF ↔ Property Graph 매핑)

> **참조**: `docs/design/neo4j-n10s.md` · `src/ontorag/stores/neo4j.py` ·
> 시작 commit: `3e4fcae` (2026-05-25, "fix(neo4j): correct malformed
> traverse edge-detail Cypher")

### 문제

Neo4j는 RDF triple store가 아니다. RDF를 Neo4j에 넣으려면 두 가지 방향이 있다.

1. **n10s (neosemantics) 플러그인** — Neo4j Labs 공식 RDF↔PG 매퍼.
2. **손수 MERGE** — rdflib으로 parse → MATCH/MERGE Cypher 생성.

### 결정

**n10s를 채택**. 그리고 다음 구성으로 잠금.

| 설정 | 선택 | 이유 |
|---|---|---|
| RDF→PG import | `n10s.rdf.import.inline` | URI-faithful, round-trippable. 손수 MERGE가 빠뜨리기 쉬운 datatype·언어 태그를 표준 절차로 처리. |
| `handleVocabUris` | **SHORTEN** | `prefix__local` 형태로 가독성 확보. prefix는 TTL의 `@prefix` 선언에서 가져와 `n10s.nsprefixes.add()`로 import *전*에 고정. |
| `handleRDFTypes` | **LABELS_AND_NODES** | 클래스 자체가 노드로 존재 → `rdfs:subClassOf` 관계를 traversable로 만듦. **이 한 줄이 다음 단계(OWL 추론)를 가능케 한다.** |
| `handleMultival` | **ARRAY** | 프로토콜이 multi-valued 속성을 지원하므로. 단일 원소 배열은 어댑터가 unwrap해서 Fuseki 결과 모양과 일치시킴 (`stores/_neo4j_values.py`). |
| `keepLangTag` | **true** | `rdfs:label`의 `@lang`를 보존해 라벨 lookup이 lang/case-insensitive로 동작. |

### 결과 — 그래프 형태

`pk: <http://example.org/pokemon#>` prefix가 import 전에 고정되었을 때:

```
(:Resource:pk__Pokemon { uri: ".../Pikachu", pk__name: "Pikachu" })
    -[:rdf__type]->   (:Resource:owl__Class { uri: ".../Pokemon" })
    -[:pk__hasType]-> (:Resource:pk__Type  { uri: ".../Electric" })

(:Resource{ uri: ".../LegendaryPokemon" })
    -[:rdfs__subClassOf]-> (:Resource{ uri: ".../Pokemon" })
```

- 모든 노드는 `:Resource` 라벨 + 단축된 클래스 라벨을 함께 가짐.
- Object property → `prefix__local` 타입 관계.
- Datatype property → `prefix__local` 키의 노드 속성.
- TBox의 클래스/속성도 `:Resource` 노드이므로 스키마 자체가 쿼리 가능.

### URI ↔ 단축형 매핑 레이어

프로토콜은 항상 full URI(`class_uri`, `predicate_uri`)를 받는다. n10s는
단축형으로 저장한다. 어댑터는 얇은 양방향 매퍼를 갖는다.

- **expand** `prefix__local` → full URI (read path).
- **shorten** full URI → `prefix__local` (write/query path).

이 매퍼는 import 시점에 pinning된 prefix를 우선 사용하고, 런타임에는 n10s가
저장하는 `_NsPrefDef` 노드에서 재구성할 수 있다. 결과는 안정적이다 —
prefix가 `ns0__`처럼 자동 할당되지 않고 `pk__Pokemon`으로 유지된다.

---

## 3. 2단계 — OWL 추론 parity (subClassOf)

> **참조**: `docs/design/neo4j-n10s.md` "Resolved" 섹션 ·
> `docs/design/fuseki-parity.md`

### 문제

`find_entities(Animal)`은 Dog/Cat 인스턴스도 반환해야 한다 — `Dog
rdfs:subClassOf Animal`이라면 `Dog` 인스턴스는 의미상 `Animal`이다. 이게
GraphRAG와 ontorag를 가르는 결정적 차이다.

당시 두 백엔드의 상태:

- **Neo4j** — 클래스가 노드로 존재(`LABELS_AND_NODES` 덕분)하므로
  `[:rdfs__subClassOf*0..N]` 가변 길이 path로 자연스럽게 추론 가능.
- **Fuseki** — `--mem` 개발용 데이터셋이 reasoner 없이 떠 있어 추론 OFF.

이대로 두면 같은 질문이 백엔드마다 다른 답을 낸다. parity의 1차 위반이다.

### 결정

**둘 다 추론 ON으로 수렴.** 단, 백엔드의 네이티브 기술로 각자 구현.

Neo4j — Cypher 가변 길이 path:
```cypher
MATCH (i)-[:rdf__type]->()-[:rdfs__subClassOf*0..N]->(c {uri: $class_uri})
RETURN i
```

Fuseki — Jena 설정을 바꾸지 않고 **쿼리 레벨**에서 해결:
```sparql
GRAPH <urn:ontorag:data>   { ?inst a ?type . ... }
{ GRAPH <urn:ontorag:schema> { ?type rdfs:subClassOf* <cls> . } }
UNION { FILTER(?type = <cls>) }   # TBox 미적재 시 직접 매치 fallback
```

이 선택의 미덕은 "Fuseki에 `ja:OntModelSpec` reasoner를 켜라"는 인프라 변경
없이 — 그래서 기존 600+ 테스트가 한 줄도 안 깨진 채로 — 추론이 활성화됐다는
점이다. SCHEMA/DATA named graph 조인 + 직접매치 UNION만으로 수렴이 가능했다.

### 결과 — 측정된 parity

`docs/BENCHMARK_v1.md`에서 검증된 한 줄:

> `count_entities(Pokemon) = 13` (Fuseki / Neo4j / FalkorDB **모두**)

여기에는 `LegendaryPokemon`의 단 하나의 인스턴스(Mewtwo)가 subClassOf 추론을
통해 포함되어 있다. 3-backend가 모두 13으로 일치한다는 사실 자체가
"OWL subclass inference가 라이브로 작동하며 모든 백엔드에서 일관적"이라는
증거다.

---

## 4. 3단계 — L2 DSL 번역기 (`pattern_to_cypher`)

> **참조**: `src/ontorag/core/cypher.py` · `src/ontorag/core/sparql.py` ·
> `docs/design/sparql-approach.md`

### 문제

`ontorag`의 MCP 툴은 3-레이어 구조다.

- **L1** — 의도 기반 고수준 툴 (`find_entities`, `describe_entity`, ...).
  90% 케이스가 여기서 끝난다.
- **L2** — JSON DSL escape hatch (`query_pattern`). 복잡한 BGP를 LLM이
  생성하되, 구조적으로 검증 가능한 JSON으로 받는다 — raw SPARQL은 절대 LLM에
  노출하지 않는다.
- **L3** — raw SPARQL. MCP에서 제외, 개발자 디버그용.

Fuseki에는 이미 `pattern_to_sparql`이 있다. Neo4j에도 **대칭적인
`pattern_to_cypher`**가 필요하다 — 그래야 L2 툴의 의미가 양 백엔드에서
동일하다.

### 보안의 어려움 — Cypher는 SPARQL과 다르다

SPARQL은 모든 IRI/리터럴을 `?param`으로 bind할 수 있다. Cypher는 **관계
타입·라벨·속성 키를 파라미터로 바인드할 수 없다.** 문자열 인터폴레이션으로
박아 넣어야 한다. 단순히 backtick으로 감싸는 것은 불충분하다 — 입력에
backtick이 있으면 quoting을 깨버린다.

### 결정

`core/cypher.py`에 `_safe_rel()` allowlist 검증을 도입.

```python
# 안전한 n10s-단축 식별자: ``prefix__Local``
_SAFE_SHORT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*__[A-Za-z0-9_.\-]+$")

def _safe_rel(short: str) -> str:
    """모든 rel-type / label / prop-key 인터폴레이션은 이 함수를 통과해야 한다."""
    if not _SAFE_SHORT_RE.match(short):
        raise InvalidIdentifierError(short)
    return short
```

그리고 `PatternQuery` 자체가 SPARQL 측 `PatternTriple` regex 검증을 통과한
이후 Cypher로 번역되므로, 입력은 이미 정제된 상태로 들어온다 — **이중 방어**.
가변 길이 path는 깊이 상한(`subClassOf*0..N`, pred `*1..6`)으로 캡.

### 결과

L2 `query_pattern`이 양 백엔드에서 동일한 JSON DSL을 받아 동일한 결과를
낸다. LLM은 SPARQL이나 Cypher 어느 쪽도 모르고도 정확한 BGP 질의를 작성할
수 있다.

---

## 5. 4단계 — BM25 풀텍스트 검색 (`search_text`)

> **참조**: `docs/design/neo4j-bm25.md` · `src/ontorag/stores/_neo4j_search_mixin.py` ·
> `src/ontorag/stores/_fuseki_search_mixin.py` · commit `fc3a5b2` (2026-05-26,
> "feat(agent): expose search_text (BM25) + find_similar (vector) as agent tools")

### 문제

"피카츄 같은 캐릭터를 찾아줘" — 키워드 풀텍스트는 RAG의 기본기다. 그런데
SPARQL/Cypher의 기본 동등 비교로는 못 한다. 외부 검색 엔진(Elasticsearch)을
붙이는 길도 있지만 그러면 의존성이 늘어난다.

### 결정 — 둘 다 native

- **Neo4j** — `db.index.fulltext.queryNodes`. Neo4j가 Lucene을 내장하고 BM25
  점수를 그대로 노출한다. 외부 엔진 불필요.
- **Fuseki** — `text:TextDataset` (jena-text + Lucene). Fuseki 설정 파일
  (`docker/fuseki/config.ttl`)에서 TDB2 데이터셋을 `text:TextDataset`로
  감싸 `rdfs:label`, `rdfs:comment`, `skos:prefLabel`, `skos:definition`를
  Lucene에 색인.

API 표면은 양쪽 동일하다.

```python
search_text(query: str, class_uri: str | None = None, limit: int = 10) -> list[SearchHit]
# SearchHit = {uri, label, class_uri, score}
```

`class_uri`가 주어지면 `find_entities`와 동일한 subClassOf-aware 필터를
적용 — 즉, 풀텍스트 결과도 OWL 추론을 통과한다.

### Neo4j 쪽의 까다로움 — 동적 색인 재구성

`handleMultival: ARRAY`로 모든 datatype 속성이 리스트로 저장된다. Neo4j
풀텍스트 인덱스는 색인 대상 속성 키를 *고정 리스트*로 받는다. 즉:

1. 매 `load_rdf` 후 `:Resource` 노드를 스캔해 string 값 속성 키를 발견한다.
2. 각 키를 `_safe_rel`로 검증.
3. 속성 집합이 바뀌었으면 인덱스를 **drop + recreate**.
4. `rdfs__label`은 항상 포함.

> `CALL db.index.fulltext.queryNodes('ontorag_fulltext', $query)`

### Fuseki 쪽의 비대칭 — 의도된 차이

jena-text는 색인 대상 predicate를 *config 시점*에 고정한다. Fuseki는 큐레이션된
text-property 셋(`rdfs:label` / `comment` / `skos:prefLabel` / `definition`)을
색인하는 반면, Neo4j는 *모든* string 속성을 동적으로 발견한다. 이는 두
저장소의 본성 차이를 반영한 의도된 비대칭이다 — *결과 모양*(`SearchHit`)은
동일.

---

## 6. 5단계 — 벡터 유사도 (`find_similar`, FastRP + 임베딩)

> **참조**: `docs/design/neo4j-embedding.md` ·
> `src/ontorag/stores/_neo4j_embedding_mixin.py` (38.8 KB) ·
> `src/ontorag/stores/_fuseki_embedding_mixin.py` (33.6 KB) ·
> commit `35775be` (2026-05-26, "feat(similar): add subClassOf-aware
> class_uri filter to find_similar")

### 문제

"피카츄와 비슷한 캐릭터를 찾아줘"는 BM25로 풀리지 않는다. 그래프 구조와
의미 임베딩이 필요하다. 그리고 그 둘을 어떻게 조합할 것인가?

### 결정 — 구조 + 텍스트 + RRF hybrid

세 모드를 지원.

| Mode | Neo4j | Fuseki |
|---|---|---|
| `structural` | **GDS FastRP** (`gds.fastRP.write`) → `_struct_embedding` 노드 속성 → native vector index | **순수 Python FastRP** (`core/fastrp.py`, 의존성 zero) → Qdrant collection `ontorag_struct` |
| `textual` | **EmbeddingProvider** (OpenAI/Ollama) → `_text_embedding` 노드 속성 → native vector index | EmbeddingProvider → Qdrant collection `ontorag_text` |
| `hybrid` | 위 두 single-mode kNN 각각 top-k×2 → **RRF** (reciprocal rank fusion, `k0 ≈ 60`) | 동일 RRF |

### 왜 Fuseki에 별도 FastRP를 새로 썼는가

Neo4j는 GDS Community(무료)에 FastRP가 들어있어 한 줄로 끝난다. Fuseki에는
없다. 옵션은:

- (A) NetworkX/PyTorch-Geometric 같은 큰 라이브러리를 의존성으로 추가.
- (B) **순수 Python FastRP 구현** (NumPy만 사용, < 200 LOC).

(B)를 택했다. 이유:

1. **의존성 최소화** — `ontorag`의 원칙이다. 라이브러리 늘리지 않고 푼다.
2. **결정론** — `randomSeed` 고정 시 재현 가능한 임베딩. 테스트가 안정적.
3. **라이선스 안전** — Apache 2.0 호환만 의존.

이 작은 결정 하나가 **v0.9 FalkorDB 작업에서 그대로 재활용**됐다. FalkorDB도
GDS가 없기 때문에 `core/fastrp.py`를 동일하게 사용해 벡터 백엔드를 구성했다.
"Fuseki의 부족을 메우려고 짠 코드"가 "다른 백엔드의 표준 경로"가 되는
선순환.

### 두-스토어 일관성 — Fuseki 쪽의 비용

Neo4j는 벡터가 노드 속성이므로 그래프와 벡터가 한 곳에 있다. Fuseki는 분리
저장소(Qdrant)가 필요하다 → **분산 상태 문제**가 생긴다.

- 임베딩은 **명시적**으로만 생성한다 (`ontorag embed --mode both`). 절대
  `load_rdf`에 묶지 않음 — textual은 외부 API 비용이 든다.
- 엔티티가 삭제되면 Qdrant의 해당 point는 **다음 `ontorag embed`까지 stale**.
  이를 README에 명시.

이건 백엔드의 본성에서 오는 비용이다. 숨기지 않고 문서화하는 게 옳다.

### 결과 — subClassOf-aware 필터까지

`find_similar(uri, top_k, mode, class_uri=None)`. `class_uri`가 주어지면
similar 결과 중 해당 클래스(또는 서브클래스)의 인스턴스만 반환. 추론이
검색에까지 흘러든다 — **이게 GraphRAG와의 차이가 가장 분명히 드러나는
지점**이다.

---

## 7. 6단계 — Fuseki Capability Parity 정식 선언

> **참조**: `docs/design/fuseki-parity.md`

이 시점에서 두 백엔드가 모두 (1) subClassOf 추론, (2) 풀텍스트, (3) 벡터
유사도를 지원한다. parity 표를 design doc에 못 박았다.

| Capability | Neo4j | Fuseki |
|---|---|---|
| subClassOf 추론 | Cypher `[:rdfs__subClassOf*0..N]` | 쿼리 레벨 SPARQL `?inst a/rdfs:subClassOf* <cls>` |
| `search_text` | 네이티브 fulltext index | jena-text Lucene (`text:query`) |
| `find_similar`, `ontorag embed` | GDS FastRP + native vector index | `core/fastrp.py` + EmbeddingProvider → Qdrant |

핵심 원칙은: **각 백엔드가 자기 네이티브 기술로 같은 contract를 충족한다.**
"Fuseki 위에 Neo4j 흉내내기"가 아니다.

---

## 8. 7단계 — Multi-Ontology Scoping

> **참조**: `docs/design/multi-ontology.md`

### 문제

하나의 ontorag 인스턴스가 **여러 온톨로지**(예: `pokemon`, `foaf`)를 동시에
호스팅하고, 각각을 격리해서 또는 union으로 질의할 수 있어야 한다. 그런데
600+ 기존 테스트는 *단일 온톨로지*를 가정해 작성됐다 — 하나라도 깨지면 안
된다.

### 결정 — 옵션 파라미터로 스코핑 + 하위호환

GraphStore 프로토콜의 모든 read 메서드 + `load_rdf`에 `ontology: str | None`
인자 추가.

| | `ontology=None` (기본) | `ontology="<id>"` |
|---|---|---|
| Fuseki | `urn:ontorag:schema` / `urn:ontorag:data` (기존 한 쌍) | `urn:ontorag:{id}:schema` / `urn:ontorag:{id}:data` |
| Neo4j | `_ontology` 필터 없음 (모든 노드) | 노드 `_ontology = "{id}"` 태그 |

핵심은 **`ontology=None`이 정확히 기존과 동일하게 동작**해야 한다는 것.
Fuseki는 `tdb2:unionDefaultGraph true`로 default graph를 union으로 보여주고,
Neo4j는 그냥 `_ontology` 필터를 안 건다. 600+ 테스트가 그대로 통과한다.

### 두 백엔드의 본성 차이

- Fuseki는 named graph가 1급 기능 → 자연스럽게 `GRAPH <urn:ontorag:{id}:data>`로
  감싼다.
- Neo4j는 named graph 개념이 없다 → **노드 레벨 태깅**. import 후
  `:Resource` 노드 중 갓 들어온 것에 `_ontology = $id`를 부여, 매 read에
  `WHERE n._ontology = $id`를 추가.

스키마 vocab(rdf/rdfs/owl)은 교차-온톨로지 공용이라 필터 대상 제외 — 이런
세세한 결정이 design doc에 명시되어야 백엔드 간 동작이 어긋나지 않는다.

### 알려진 까다로움 — `query_pattern` × Fuseki named graph

L2 `query_pattern`은 **스코프 없는** escape hatch — `ontology` 파라미터가
없고 default graph를 본다. Neo4j/FalkorDB에서는 scoped data가 하나의 물리
그래프에 살므로 어떻든 보인다. **Fuseki에서는 named graph에 격리되어 있으므로
`tdb2:unionDefaultGraph true`가 켜져 있을 때만 보인다.** 프로덕션 config는
이걸 켜둠. 일반 `--mem` dev 컨테이너는 0건이 나올 수 있음 — cross-backend
health check가 이걸 잡아준다.

이런 식의 "한 백엔드에만 있는 sharp edge"는 README와 design doc에 명시적으로
남긴다 — 숨기지 않는다.

---

## 9. 8단계 — v0.6.1 후속: `owl:sameAs` 정렬 + 접근 제어

> **참조**: `docs/design/multi-ontology.md` "Out of scope" → v0.6.1에서 해소 ·
> commit `996ddbb` (2026-05-28, `feat: cross-ontology owl:sameAs alignment (find_aligned)`) ·
> commit `30930e1` (2026-05-28, `feat(access): config-driven per-ontology read/write access control`)

multi-ontology가 들어오자 자연스럽게 두 가지가 필요해졌다.

### (a) Cross-Ontology Entity Alignment — `find_aligned`

서로 다른 온톨로지에 같은 실세계 엔티티가 있을 수 있다. `owl:sameAs`로 명시
하면 이를 추적 가능. v0.6.1에 두 백엔드에서 `owl:sameAs`의 **전이+대칭 폐포**를
계산하는 `sameas_closure`를 추가.

- Fuseki — property path `(owl:sameAs | ^owl:sameAs)+`
- Neo4j — `[:owl__sameAs*1..]` 무방향

→ `find_aligned(uri)` MCP 툴로 노출.

### (b) Per-Ontology Access Control

여러 온톨로지가 한 인스턴스에 있으면, 사고로 인한 cross-ontology 쓰기/읽기를
방지할 필요가 생긴다. v0.6.1에 `ONTOLOGY_ACCESS` env 기반의 read/write/none
스코프 락을 도입 (`src/ontorag/stores/access_wrapper.py`). **GraphStore 경계에서
래핑**해서 백엔드 종류와 무관하게 일관 적용. 미설정 시 fully open (하위호환).

이건 인증/멀티테넌시가 아니다 — *사고 방지*가 목적임을 README에 명시.

---

## 10. 입증 — 3-Backend Deterministic Parity

> **참조**: `docs/BENCHMARK_v1.md`

v1.0 시점에 다음을 측정해 문서화.

```
Pokémon 스키마+데이터를 normal load 순서 (clear → load schema → load data)
로 세 백엔드에 적재 후, LLM 개입 없이 deterministic L1 툴 실행.
```

| Metric | Fuseki | Neo4j | FalkorDB | Parity |
|---|---|---|---|---|
| `get_schema` classes | 6 | 6 | 6 | ✅ |
| `get_schema` properties | 20 | 20 | 20 | ✅ |
| `count_entities(Pokemon)` (subClassOf-추론) | **13** | **13** | **13** | ✅ |
| `count_entities(LegendaryPokemon)` | 1 | 1 | 1 | ✅ |
| `aggregate(hasType)` groups | 8 | 8 | 8 | ✅ |
| `aggregate(hasType)` total | 18 | 18 | 18 | ✅ |
| `traverse(Pikachu, depth 2)` nodes | 42 | 42 | 42 | ✅ |

`full_parity = True`.

`count_entities(Pokemon) = 13`이 세 백엔드 모두 동일한 것이 **이 작업의 핵심
입증**이다. 그 13에는 한 마리의 `LegendaryPokemon`(Mewtwo)이
subClassOf 추론으로 포함되어 있다 — 즉, OWL subclass inference가 세 곳 모두
라이브로 작동하며 일관적이다.

골드셋 자체의 품질(5개 도메인, 130 질문, `gold_sparql` 실패 0건)도 함께
입증했다.

---

## 11. 회고 — 핵심 결정의 근거

### 11.1 왜 SPARQL via n10s endpoint를 안 쓰고 native Cypher 번역을 했는가

n10s는 SPARQL endpoint도 제공한다 (`/rdf/neo4j/cypher`). 그러면 Fuseki 코드를
거의 그대로 재사용 가능하다. 그런데 거절했다.

이유:

1. **성능** — n10s SPARQL endpoint는 SPARQL → Cypher 매 쿼리마다 번역한다.
   Cypher native 코드는 한 번 번역해 캐싱 가능.
2. **인젝션 안전성** — Cypher injection을 막는 `_safe_rel` allowlist가 우리
   코드 안에 있는 게 *우리* 통제 하에 있다. n10s 내부 번역은 외부 의존이다.
3. **L2 대칭성** — `pattern_to_sparql`과 `pattern_to_cypher`가 짝을 이루는
   대칭 설계가 코드 베이스 일관성을 만든다.
4. **L1 표현력** — `find_entities`의 subClassOf 추론을 Cypher로 직접 쓰는 게
   훨씬 단순하다 (가변 길이 path 한 줄). SPARQL property path를 n10s에
   맡기는 것보다 결정론적.

### 11.2 왜 Fuseki에 GDS 대신 pure-Python FastRP를 새로 구현했는가

§6에서 이미 다뤘다. 짧게: **의존성 최소화 + 결정론 + 라이선스 안전 + 재사용성**.
이 결정 하나가 v0.9 FalkorDB에서 그대로 보답했다.

### 11.3 추론 backend divergence를 어떻게 닫았는가

작업 중반까지 메모리에는 "Neo4j는 추론 ON, Fuseki는 OFF"가 *intentional
divergence*로 적혀 있었다. 이를 그대로 두면 parity 주장이 깨진다. `docs/design/
fuseki-parity.md` 작성 시점에 **Fuseki도 추론을 켜되 Jena reasoner config를
바꾸지 않는 길**로 수렴 — 쿼리 레벨 `?inst a/rdfs:subClassOf*` 패턴 + SCHEMA/
DATA named graph 조인 + 직접매치 UNION fallback. **인프라 변경 0건**으로 600+
테스트가 그대로 통과하며 추론이 활성화됐다.

이게 어쩌면 이 작업에서 기술적으로 가장 만족스러운 순간이었다. "config를 바꿔야
한다"는 길에 빠지지 않고 코드 레벨로 닫았다.

### 11.4 왜 multi-ontology를 named graph + 노드 태깅의 *조합*으로 풀었는가

대안은:

- (A) 모든 백엔드에서 노드 태깅으로 통일.
- (B) Fuseki는 named graph, Neo4j는 별도 데이터베이스로 격리.
- (C) **각 백엔드의 native 추상화 활용** (Fuseki=named graph, Neo4j=노드 태깅).

(C)를 택한 이유: **백엔드의 본성에 맞춰야 후속 비용이 작다.** Fuseki에서
노드 태깅은 어색하고(triple에 quad를 흉내내야 함), Neo4j에서 별도 DB는 비싸다
(연결 풀·트랜잭션 경계 별도). 각자의 native primitive를 쓰면 추후 백엔드별
최적화 여지가 열린다.

### 11.5 왜 sharp edge를 숨기지 않고 노출했는가

`query_pattern × Fuseki named graph × tdb2:unionDefaultGraph` 같은 "한
백엔드에만 있는 까다로움"이 있다. README와 design doc에 명시한다.

이유: **숨기면 운영 단계에서 폭발한다.** "왜 dev에서는 0건이 나오죠?" 같은
질문이 GitHub issue로 누적되는 것보다, 미리 문서에 적어두는 게 신뢰
자산이다. v1.0 vendoring 시점의 audit에서 이 원칙이 코드 신뢰도의 일부로
포함됐다.

---

## 12. 부록 A — 코드 맵 (작업이 닿은 파일들)

```
src/ontorag/
├── core/
│   ├── cypher.py                  # PatternQuery → Cypher 번역 + _safe_rel
│   ├── sparql.py                  # PatternQuery → SPARQL 번역 (Fuseki 쪽, 대칭)
│   └── fastrp.py                  # 순수 Python FastRP (Fuseki·FalkorDB 공용)
├── stores/
│   ├── base.py                    # GraphStore Protocol + result types
│   ├── factory.py                 # create_store(): fuseki / neo4j / falkordb 분기
│   ├── access_wrapper.py          # v0.6.1 ONTOLOGY_ACCESS 스코프 락
│   ├── fuseki.py                  # Fuseki 어댑터 (SPARQL over HTTP)
│   ├── neo4j.py                   # Neo4j 어댑터 (n10s + async driver)
│   ├── _neo4j_schema_mixin.py     # get_schema / get_class_detail
│   ├── _neo4j_entity_mixin.py     # find / describe / count / aggregate
│   ├── _neo4j_traversal_mixin.py  # traverse / path / closure / related
│   ├── _neo4j_search_mixin.py     # BM25 (native fulltext)
│   ├── _neo4j_embedding_mixin.py  # GDS FastRP + textual + RRF
│   ├── _neo4j_values.py           # n10s ARRAY 다중값 unwrap
│   ├── _neo4j_scope.py            # multi-ontology _ontology 태깅
│   ├── _fuseki_search_mixin.py    # jena-text (Lucene)
│   ├── _fuseki_embedding_mixin.py # core/fastrp + EmbeddingProvider → Qdrant
│   └── _qdrant.py                 # async Qdrant 래퍼
└── llm/embedding.py               # EmbeddingProvider (OpenAI/Ollama) 공용
```

---

## 13. 부록 B — 시간선 (관련 commit만 발췌)

날짜는 2026년.

```
05-25  3e4fcae  fix(neo4j): correct malformed traverse edge-detail Cypher
05-26  fc3a5b2  feat(agent): expose search_text (BM25) + find_similar (vector) as agent tools
05-26  35775be  feat(similar): add subClassOf-aware class_uri filter to find_similar
05-26  75b36ef  docs(readme): reflect v0.5.x — 13-tool agent, find_similar class filter
05-26  975812e  feat(cli): expose backend (Neo4j/GRAPH_STORE/Qdrant) in config set
05-28  60c1c56  perf(loader): load_rdf accepts a pre-parsed graph to avoid double-parse
05-28  996ddbb  feat: cross-ontology owl:sameAs alignment (find_aligned)
05-28  30930e1  feat(access): config-driven per-ontology read/write access control
05-28  c869b51  chore(release): v0.6.1 — owl:sameAs alignment + per-ontology access
05-29  9107068  feat: v0.9 FalkorDB backend — 3rd graph store with full parity   ← 이 작업의 자연스러운 후속
05-29  3669f7f  fix: cross-backend parity from graphdb health check (Fuseki/Neo4j/FalkorDB)
05-30  5434b36  docs: v1.0 3-backend deterministic benchmark + parity evidence    ← 입증 시점
05-30  5b8dba9  chore: release v1.0.0 — Production-Ready & Proven
```

> **메모** — main 브랜치는 squash merge로 정리되어 commit 50건만 보인다.
> 실제 개발 과정의 세부 변천은 이 글이 인용한 design doc(`docs/design/`)에
> 더 자세히 남아 있다.

---

## 14. 부록 C — 참고 자료

### 사내 design docs
- `docs/design/neo4j-n10s.md` — n10s 어댑터 결정과 그래프 매핑
- `docs/design/neo4j-bm25.md` — BM25 풀텍스트 설계
- `docs/design/neo4j-embedding.md` — GDS FastRP + textual + RRF
- `docs/design/fuseki-parity.md` — 백엔드 동등성 표
- `docs/design/multi-ontology.md` — 멀티 온톨로지 스코핑
- `docs/design/sparql-approach.md` — 3-레이어 툴 격리 원칙
- `docs/BENCHMARK_v1.md` — 3-backend deterministic parity 입증

### 외부 자료
- [neosemantics (n10s) — Neo4j Labs](https://neo4j.com/labs/neosemantics/)
  — RDF 임포트 / vocab URI / multival 옵션
- [Graph Data Science (GDS) — FastRP](https://neo4j.com/docs/graph-data-science/current/algorithms/fastrp/)
- [Apache Jena — jena-text](https://jena.apache.org/documentation/query/text-query.html)
  — Fuseki Lucene 풀텍스트 색인
- [Qdrant — Python client](https://qdrant.tech/documentation/quick-start/)
- [SPARQL 1.1 Property Paths — W3C](https://www.w3.org/TR/sparql11-query/#propertypaths)

---

## 15. 한 줄 마무리

> **"각 백엔드를 자기 본성대로 두되, 프로토콜이라는 약속만 동등하게 지키게
> 한다."**
>
> Neo4j는 Cypher를, Fuseki는 SPARQL을, FalkorDB는 OpenCypher를 모국어로
> 쓴다. ontorag의 MCP 툴은 셋 중 어느 것도 모른 채 `find_entities(Animal)`을
> 부르고, 세 곳 모두에서 Dog와 Cat을 받는다. 이게 OWL native RAG의 의미다.
