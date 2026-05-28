# 레이어드 온톨로지 설계 문서 (v2)

> **보존 노트 (2026-05-28):** 이 문서는 폐기된 `modular-ontology` 브랜치에서
> 보존됐습니다. 원래 v0.4 대상으로 작성됐고, 그 일부는 다른 형태로 이미 구현됐습니다 —
> SHACL 검증(v0.4.1), multi-ontology named-graph 스코핑(v0.5), per-ontology
> 접근 제어(v0.6.1). **그러나 핵심인 4-레이어 아키텍처(Schema / Policy / Dynamic /
> Provenance), State Object 시계열 패턴, PROV-O provenance 레이어는 아직 미구현**이며
> 유효한 향후 설계로 남아 있습니다. 본문의 "v0.4" 버전 표기는 역사적 맥락입니다.

**원 브랜치**: `modular-ontology` (머지·삭제됨)  
**작성일**: 2026-05-18 (v1) → **개정**: 2026-05-18 (v2)  
**상태**: 설계 v2 — 전문가 검토 반영. 부분 구현(SHACL/multi-ontology/access), 레이어드 아키텍처 본체는 미착수.

---

## 변경 이력 (v1 → v2)

3개 검토(온톨로지 표준 전문가 / LLM-RAG 아키텍트 / pre-mortem)에서 발견된 critical/high 이슈를 반영했습니다.

| 변경 | 사유 | 검토자 |
|---|---|---|
| **"Kinematic" → "Policy"** | Palantir의 Kinematic은 시뮬레이션 가능한 인과 모델(상태 전이)이지 SHACL 제약과 무관. 채택 funnel 차단 요인. | 표준 + LLM + pre-mortem (전원 합의) |
| **W3C Time 사용 제거 → `:validFrom`/`:validTo`** | `time:hasBeginning/hasEnd`의 range는 `time:Instant`이지 `xsd:dateTime` 아님. 원안은 W3C 표준 위반. | 표준 전문가 |
| **다중 그래프 추론 어셈블러 설정 명세 추가** | Named Graph 분리 시 OWL 추론이 자동 동작하지 않음. `find_entities(:Animal)` 회귀 위험. | 표준 전문가 |
| **PROV-O 패턴 완성** | Entity/Activity/Agent 타입, `prov:used`, `prov:endedAtTime`, `prov:wasAttributedTo`, `prov:generatedAtTime` 누락. | 표준 전문가 |
| **RDF-star 평가 근거 교체** | "검증 미성숙"은 부정확. Jena 4/5는 안정 지원, W3C RDF 1.2 표준화 진입. 진짜 근거는 MCP 인터페이스 자연성. | 표준 전문가 |
| **State Object 스케일 가드레일 추가** | O(N·k·T) 팽창 — 5천만~1억 트리플 시 Fuseki TDB2 쿼리 지연 급증. | 표준 전문가 |
| **Phase 3 분할 → 3a / 3b** | State Object 본질 작업과 라우터/스키마 로더 직교 작업이 한 묶음. | LLM 아키텍트 |
| **툴 수 결정 spike 추가** | 9→14 툴은 Anthropic 권장(5~10) 초과. 레이어별 분리 vs `layer` 파라미터 결정 보류. | LLM 아키텍트 |
| **시계열 본질 reference 도메인 추가 (IoT)** | Pokemon은 정적 도메인. "Pikachu 6개월 전 레벨"이 시계열 use case로 어색. | LLM + pre-mortem |
| **v0.3.2 사용자 시그널 조사 게이트 추가** | 채택 시그널 없이 가설 위에 가설. 착수 전 GitHub traffic/issues 확인. | pre-mortem |
| **pyshacl 벤치마크 게이트 추가** | 1만+ 트리플에서 검증당 1~10초. 실시간 MCP 툴로 부적합 가능. | LLM 아키텍트 |
| **SKOS/DCAT 고려 추가** | 분류 체계는 SHACL이 아닌 SKOS, 데이터셋 메타데이터는 DCAT. | 표준 전문가 |

---

## 0. 착수 전 결정 게이트 (필수)

**다음 3개 질문에 한 줄로 답할 수 없다면 v0.4 착수를 보류합니다** (pre-mortem 권고).

| 게이트 | 질문 | 답 |
|---|---|---|
| **G1: 사용자 시그널** | v0.3.2 사용자 중 "TBox 재로드 시 ABox 손실" 또는 "시계열 추적 부재"를 issue/discussion으로 제기한 사람이 있는가? | _(미확인)_ |
| **G2: 시계열 도메인** | Pokemon/techstack 외에 "시계열·정책이 본질인 reference 도메인" 1개를 v0.4 릴리즈에 함께 포함할 수 있는가? | _(미확인)_ |
| **G3: 정량적 차별** | GraphRAG/LangChain로 동일 use case를 구현했을 때, v0.4가 정량적으로 우월한 지표(쿼리 정확도, 토큰 비용, 추론 정확도) 1개를 제시할 수 있는가? | _(미확인)_ |

**조사 액션 (1주)**: GitHub traffic + issues + 외부 언급 확인 → 사용자 인터뷰 1~2건 → 답을 채운 뒤 Phase 1 착수.

---

## 1. 왜 레이어드 온톨로지인가

현실 세계 데이터는 변화 속도가 다양합니다:

| 레이어 | 변화 속도 | 예시 | 표준 |
|---|---|---|---|
| **Semantic** (정적 스키마) | 수개월~수년 | 클래스 정의, 속성 스키마 | OWL 2 |
| **Policy** (제약·정책) | 수일~수주 | 데이터 품질 규칙, 분류 정책 | SHACL, SKOS |
| **Dynamic** (시계열·이벤트) | 실시간~수분 | 센서 읽기, 이벤트, 상태 변화 | PROV-O, 자체 valid_from/to |

현재 ontorag은 `schema`(TBox) / `data`(ABox)로만 구분합니다. 이 이분법은 다음을 강제합니다:
- 제약 규칙(SHACL)을 TBox에 섞거나 ABox에 부어야 함 → 운영·검증 분리 불가
- 시계열 데이터를 ABox에 누적하면 "현재 상태"와 "과거 상태"가 혼재

v0.4는 4개 Named Graph로 이를 분리합니다.

**중요한 명확화 (v1 피드백 반영)**: "Semantic = TBox = 변화 빈도"라는 매핑은 부정확합니다. TBox는 변화 빈도가 아니라 메타레벨(스키마 vs 인스턴스) 개념입니다. v0.4의 분리 기준은 **변화 빈도 + 책임(스키마/정책/관측)**이지, 변화 빈도 단독이 아닙니다.

---

## 2. 4개 Named Graph 구조

```
urn:ontorag:semantic     ← TBox (클래스·속성 정의) — 현 schema 그래프
urn:ontorag:policy       ← SHACL shapes + SKOS 분류 정책 (v1의 "kinematic" 명명 변경)
urn:ontorag:dynamic      ← 시계열·이벤트 ABox (State Object 패턴)
urn:ontorag:provenance   ← PROV-O 활동 기록 + DCAT 데이터셋 메타
```

### 하위 호환성

| 현재 문자열 | v0.4 매핑 |
|---|---|
| `"schema"` | `urn:ontorag:semantic` |
| `"data"` | `urn:ontorag:dynamic` |

CLI/API의 `target=schema|data|all` 파라미터는 v0.4에서도 그대로 유지됩니다.

### Fuseki 다중 그래프 추론 설정 (필수)

Named Graph를 분리하면 OWL 추론이 자동 동작하지 않습니다. **Phase 1에서 다음 어셈블러 설정을 반드시 추가**해야 합니다:

```turtle
# fuseki/config-inference.ttl
:dataset rdf:type tdb2:DatasetTDB2 ;
    tdb2:unionDefaultGraph true ;
    tdb2:location "/fuseki/databases/ontorag" .

:inferenceModel rdf:type ja:OntModel ;
    ja:ontModelSpec ja:OWL_MEM_RDFS_INF ;
    ja:baseModel [
        ja:graphName <urn:ontorag:dynamic> ;
        ja:dataset :dataset
    ] ;
    ja:imports <urn:ontorag:semantic> .
```

이 설정이 없으면 `find_entities(:Animal)`이 Dog/Cat을 못 찾는 회귀가 발생합니다.

---

## 3. 핵심 설계 결정

### 3.1 레이어 파라미터 vs 레이어별 툴 — **재검토 필요 (spike 후 결정)**

v1에서는 "레이어별 별도 MCP 툴"로 결정했으나, LLM 아키텍트의 반론을 받았습니다:

| 옵션 | 장점 | 단점 |
|---|---|---|
| **A: 레이어별 별도 툴** (v1 안) | 툴 이름만 보고 레이어 식별 | 9→14 툴, Anthropic 권장(5~10) 초과, `find_entities` vs `get_state` 의미 중복 |
| **B: `layer` 파라미터** | 툴 수 유지(9개), 동사 일관성 | LLM이 `layer` 값을 추측·오입력 가능 |

**결정 보류** → Phase 1 시작 전 spike로 검증:
- A/B 안 각각 mock 구현 → 10개 대표 쿼리에 대해 LLM 호출 정확도 측정
- 정확도 차이가 5%p 이상이면 우월한 안 채택, 그 이하면 단순한 안(B) 채택

### 3.2 Policy 레이어 — SHACL + SKOS

OWL은 개방세계 가정(OWA)이므로 정책 강제에 부적합. 다음 두 표준을 조합:

**SHACL**: 데이터 품질 규칙·제약
```turtle
:PokemonShape a sh:NodeShape ;
    sh:targetClass :Pokemon ;
    sh:property [
        sh:path :hasName ;
        sh:minCount 1 ;
        sh:datatype xsd:string ;
    ] .
```

**SKOS**: 분류 체계 (v1 피드백 반영 추가)
```turtle
:PokemonTypeScheme a skos:ConceptScheme ;
    skos:hasTopConcept :Fire, :Water, :Electric .

:Fire a skos:Concept ;
    skos:broader :Element ;
    skos:prefLabel "Fire"@en, "불"@ko .
```

새 MCP 툴 (옵션 A 시):
- `get_policies(class_uri?)` → SHACL shapes 반환
- `get_classification(scheme_uri)` → SKOS 개념 계층 반환
- `validate_entity(entity_uri)` → SHACL 검증 결과 (실시간 호출 가능 여부는 Phase 2 벤치마크 후 결정)

### 3.3 Dynamic 레이어 — State Object 패턴 (W3C Time 미사용)

**v1에서 제거**: `time:hasBeginning`/`time:hasEnd`는 range가 `time:Instant`이지 `xsd:dateTime`이 아님. 표준 위반.

**채택 (단순)**: 자체 어휘 `:validFrom`/`:validTo` (xsd:dateTime)

```turtle
# Entity는 변하지 않는 식별자
:Pikachu a :Pokemon .

# State는 시점별 속성 스냅샷
:PikachuState_20260115 a :PokemonState ;
    :refersTo :Pikachu ;
    :validFrom "2026-01-15T00:00:00Z"^^xsd:dateTime ;
    :validTo "2026-06-01T00:00:00Z"^^xsd:dateTime ;
    :level 42 ;
    :hp 320 .
```

**State Object 채택 근거 (수정됨)**:
- v1 근거 ("RDF-star 검증 미성숙")는 부정확 — Jena 4/5는 안정 지원
- **진짜 근거**: MCP 툴 결과 JSON에서 State가 URI로 식별 가능해야 자연스럽다. RDF-star는 메타-트리플을 URI 없이 다뤄야 해서 MCP 인터페이스 표현이 어색해진다.

**스케일 가드레일 (v2 신규)**:
- 권장 한도: **5천만 트리플 / 단일 Fuseki 인스턴스**
- 엔티티당 평균 State 수 100 초과 시:
  - 압축 정책 (동일 속성 연속 State 병합)
  - 또는 외부 시계열 DB(InfluxDB/TimescaleDB) 위임
- `record_state`에 **멱등성 체크** 필수: 동일 valid_from + 동일 properties는 중복 생성 금지

새 MCP 툴 (옵션 A 시):
- `get_state(entity_uri, as_of?)` → 특정 시점 상태
- `find_state_history(entity_uri, from_dt, to_dt?)` → 기간 내 상태 이력
- `record_state` → **MCP 미노출** (admin/ETL 작업, `exclude_operations` 처리)

### 3.4 Provenance 레이어 — PROV-O 완성 + DCAT

v1의 PROV-O 사용은 불완전했습니다. v2에서 완성:

```turtle
# Activity (트리플 생성 활동)
:LoadEvent_001 a prov:Activity ;
    prov:startedAtTime "2026-01-15T10:00:00Z"^^xsd:dateTime ;
    prov:endedAtTime "2026-01-15T10:00:03Z"^^xsd:dateTime ;
    prov:wasAssociatedWith :AdminUser ;
    prov:used :source_csv_file .

# Agent
:AdminUser a prov:Agent ;
    prov:type prov:Person ;
    foaf:name "Admin" .

# Entity (생성된 State)
:PikachuState_20260115 prov:wasGeneratedBy :LoadEvent_001 ;
    prov:generatedAtTime "2026-01-15T10:00:03Z"^^xsd:dateTime ;
    prov:wasAttributedTo :AdminUser .

# DCAT (데이터셋 메타데이터)
:source_csv_file a dcat:Distribution ;
    dcat:downloadURL <http://example.org/pokemon.csv> ;
    dcat:mediaType "text/csv" ;
    dcterms:license <http://opensource.org/licenses/MIT> .
```

---

## 4. 프롬프트 인젝션 전략

### 3단계 프롬프트 구조

```
시스템 프롬프트:
  [1] Semantic 스키마 스켈레톤 (~500 tokens, 항상)
      — 클래스명 + 속성 수만, 세부 내용 없음

툴 응답 컨텍스트:
  [2] Policy 요약 (요청 시만, ~300 tokens)
      — 관련 SHACL shapes만

동적 컨텍스트 (이전 툴 결과):
  [3] Dynamic 데이터 (일시적, 툴 결과 한정)
```

**현재 동작과의 차이 명확화 (v1 피드백 반영)**:
현재 `agent.py`는 이미 system prompt에 compact schema를 주입하고 있습니다. v0.4가 더 하는 것:
- **Level 자동 전환 트리거**: 쿼리에 클래스명이 명시되면 Level 3(`get_class_detail`) 호출
- **Policy 요약 인젝션**: validation 키워드 감지 시 Level 2 인젝션

**Dynamic 데이터 시스템 프롬프트 주입에 대한 단정 수정**:
v1의 "Dynamic을 시스템 프롬프트에 절대 넣지 말라"는 단정. Anthropic prompt cache(5분 TTL, 1024+ 토큰) 고려 시 자주 조회되는 최근 상태(예: 최근 1시간 센서값)는 캐시 단위로 시스템 프롬프트에 넣는 게 툴 호출 왕복(200~500ms)보다 빠를 수 있음. **TTL/조회빈도 대비 손익 분석 필요** — Phase 3b 결정 사항.

---

## 5. 2단계 쿼리 라우터 — **재검토 필요**

v1의 정규식 라우터는 다음 약점을 가집니다:
- 한국어/영어 혼합 쿼리에서 다중 매칭 (예: "Pikachu의 정책 위반은 언제?" → 정책+언제 둘 다 매칭)
- 도메인 특화 어휘 미커버 (의료 "환자 상태", 금융 "tick")
- 첫 매치 우선 — 비결정적

**대안 (LLM 아키텍트 권고)**: 라우터를 없애고 시스템 프롬프트에 "질문 유형 → 추천 툴" 매핑 표를 명시. LLM이 직접 툴을 선택하도록.

**결정 보류** → Phase 3b 시작 시 spike로 검증:
- 라우터 안 vs LLM 직접 안 — 동일 쿼리 셋으로 정확도·지연·비용 비교
- 라우팅 패턴은 설정 파일로 외부화 (도메인별 확장 가능)

---

## 6. GraphStore Protocol 변경

### 6.1 새 타입 정의 (base.py)

```python
class OntologyLayer(str, Enum):
    semantic = "semantic"     # urn:ontorag:semantic
    policy = "policy"         # urn:ontorag:policy  (v1: kinematic)
    dynamic = "dynamic"       # urn:ontorag:dynamic
    provenance = "provenance" # urn:ontorag:provenance

LAYER_GRAPH_URI: dict[OntologyLayer, str] = {
    OntologyLayer.semantic: "urn:ontorag:semantic",
    OntologyLayer.policy: "urn:ontorag:policy",
    OntologyLayer.dynamic: "urn:ontorag:dynamic",
    OntologyLayer.provenance: "urn:ontorag:provenance",
}

@dataclass
class StateResult:
    entity_uri: str
    valid_from: datetime
    valid_to: datetime | None
    properties: dict[str, Any]
    state_uri: str  # MCP에서 reference로 사용
    provenance_uri: str | None = None

@dataclass
class PolicyResult:
    shape_uri: str
    target_class_uri: str
    constraints: list[dict[str, Any]]

@dataclass
class ValidationResult:
    entity_uri: str
    valid: bool
    violations: list[dict[str, Any]]
```

### 6.2 GraphStore Protocol 확장

기존 메서드는 변경 없음. 신규 메서드 (옵션 A 시):

```python
# Policy 레이어
async def load_shapes(self, path: str) -> LoadResult: ...
async def get_policies(self, class_uri: str | None = None) -> list[PolicyResult]: ...
async def validate_entity(self, entity_uri: str) -> ValidationResult: ...

# Dynamic 레이어
async def record_state(  # MCP 미노출
    self,
    entity_uri: str,
    properties: dict[str, Any],
    valid_from: datetime,
    valid_to: datetime | None = None,
    provenance: dict[str, Any] | None = None,
) -> str: ...  # returns state_uri (멱등성: 동일 valid_from+properties는 기존 URI 반환)

async def get_state(
    self,
    entity_uri: str,
    as_of: datetime | None = None,
) -> StateResult | None: ...

async def find_state_history(
    self,
    entity_uri: str,
    from_dt: datetime,
    to_dt: datetime | None = None,
    limit: int = 100,
) -> list[StateResult]: ...

# dump_graph 확장
async def dump_graph(
    self,
    target: Literal["schema", "data", "all", "policy", "dynamic", "provenance"],
    fmt: Literal["ttl", "json", "jsonl", "xlsx"] = "ttl",
) -> bytes: ...
```

---

## 7. 새 파일 레이아웃

```
src/ontorag/
├── api/routes/
│   ├── tools/
│   │   ├── policy.py       ← 신규: get_policies, validate_entity
│   │   └── dynamic.py      ← 신규: get_state, find_state_history (record_state 제외)
│   └── chat.py             ← 기존: Phase 3b에서 라우터 통합
├── chat/
│   ├── router.py           ← 신규 (Phase 3b): 쿼리 라우터
│   └── schema_loader.py    ← 신규 (Phase 3b): 프로그레시브 스키마
├── stores/
│   ├── base.py             ← 확장: OntologyLayer enum + 신규 메서드
│   └── fuseki.py           ← 확장: 신규 메서드 구현
├── learn/
│   └── base.py             ← 확장: layer, valid_from, valid_to 필드
└── fuseki/
    └── config-inference.ttl  ← 신규: 다중 그래프 추론 어셈블러
```

---

## 8. 구현 단계 (Phase 3 분할 반영)

### Phase 0 — 사전 조사 (1주, 코드 변경 없음)

| 작업 | 산출물 | 검증 |
|---|---|---|
| G1 사용자 시그널 조사 | GitHub traffic/issues 보고서 | 1명 이상 v0.3.2 채택 확인 |
| G2 시계열 reference 도메인 선정 | IoT 센서 또는 주가 데이터 ttl + 코퍼스 | examples 디렉토리에 추가 가능한 사이즈 |
| G3 GraphRAG/LangChain 동일 케이스 비교 spike | 정량 비교표 | 1개 이상 지표에서 우월 |
| **툴 옵션 A vs B spike** | 10개 쿼리 정확도 비교 | 5%p 이상 차이 시 우월안 선택 |

**게이트**: G1/G2/G3 중 1개라도 실패 시 v0.4 보류.

### Phase 1 — Named Graph 기반 구조 (낮은 위험)

| 작업 | 파일 | 검증 |
|---|---|---|
| `OntologyLayer` enum + `LAYER_GRAPH_URI` | `stores/base.py` | unit test |
| `FusekiStore`: `"schema"/"data"` → URI 매핑 | `stores/fuseki.py` | 기존 285 테스트 통과 |
| **다중 그래프 추론 어셈블러 설정** | `fuseki/config-inference.ttl` | `find_entities(:Animal)` 회귀 테스트 |
| `dump_graph` target 확장 (policy/dynamic/provenance) | `stores/base.py`, `fuseki.py` | unit test |
| CLI `ontorag load` `--layer` 옵션 | `cli.py` | CLI test |

**완료 기준**: 기존 285개 테스트 전부 통과 + Named Graph 분리·추론 회귀 테스트 추가.

### Phase 2 — Policy 레이어 (중간 위험)

| 작업 | 파일 | 검증 |
|---|---|---|
| **pyshacl 1만 트리플 벤치마크 게이트** | 별도 스크립트 | 검증당 <500ms 미달 시 Fuseki SHACL API로 전환 |
| `load_shapes(path)` | `stores/fuseki.py` | unit test |
| `get_policies(class_uri?)` | `stores/fuseki.py` | unit test |
| `validate_entity(uri)` | `stores/fuseki.py` | unit test + 성능 테스트 |
| Policy MCP 라우트 | `api/routes/tools/policy.py` | endpoint test |
| CLI `ontorag load shapes <FILE>` | `cli.py` | CLI test |
| SKOS 분류 체계 예제 추가 | `examples/<domain>/skos.ttl` | 통합 테스트 |

**완료 기준**: 선정된 reference 도메인에 SHACL+SKOS 추가, validation 정상 동작.

### Phase 3a — Dynamic 레이어 본질 (중간 위험)

| 작업 | 파일 | 검증 |
|---|---|---|
| State Object 직렬화/역직렬화 | `stores/fuseki.py` | unit test |
| `record_state()` + 멱등성 체크 | `stores/fuseki.py` | integration test |
| `get_state(uri, as_of?)` | `stores/fuseki.py` | unit test |
| `find_state_history(uri, from, to)` | `stores/fuseki.py` | unit test |
| Dynamic MCP 라우트 (record_state 제외) | `api/routes/tools/dynamic.py` | endpoint test |
| PROV-O 활동 기록 통합 | `stores/fuseki.py` | unit test |

**완료 기준**: IoT 센서 reference 도메인에서 "센서 X의 어제 평균 온도" 쿼리 정상 응답.

### Phase 3b — 라우터·스키마 로더 (선택적, v0.4.1로 미룰 수 있음)

| 작업 | 파일 | 검증 |
|---|---|---|
| 라우터 vs LLM 직접 spike | 별도 비교 노트 | 정확도·지연·비용 표 |
| 채택안 구현 (라우터 or 매핑 표 인젝션) | `chat/router.py` 또는 `chat/agent.py` | unit test |
| 프로그레시브 스키마 로더 | `chat/schema_loader.py` | unit test |
| `chat.py` 통합 | `api/routes/chat.py` | E2E test |
| Dynamic 데이터 캐시 인젝션 손익 분석 | 별도 노트 | TTL/조회빈도 표 |

**완료 기준**: v0.3.2 대비 동일 쿼리에서 토큰 사용량 측정 + 응답 정확도 유지.

---

## 9. 오픈 이슈 (v2 갱신)

| 이슈 | 옵션 | 결정 시점 |
|---|---|---|
| 툴 옵션 A vs B (레이어별 vs layer 파라미터) | 옵션 A: 14 툴 / 옵션 B: 9 툴 + layer | Phase 0 spike 후 |
| SHACL 검증 방식 | pyshacl (로컬) vs Fuseki SHACL API | Phase 2 벤치마크 게이트 |
| Dynamic 데이터 캐싱 | 시스템 프롬프트 vs 툴 호출 | Phase 3b 손익 분석 |
| Web UI 레이어 탭 | 기존 Schema/Data 탭 확장 vs 새 탭 추가 | Phase 2 완료 후 |
| State 압축 정책 | 동일 속성 연속 State 자동 병합 vs 사용자 명시 트리거 | Phase 3a 시작 시 |

---

## 10. 참고 온톨로지 표준

- **OWL 2 DL**: Semantic 레이어 (TBox)
- **SHACL**: Policy 레이어 데이터 제약
- **SKOS**: Policy 레이어 분류 체계
- **PROV-O** (W3C): Provenance 레이어 활동/출처
- **DCAT** (W3C): Provenance 레이어 데이터셋 메타데이터
- **RDF 1.1**: Named Graph (Quad Store)
- **xsd:dateTime**: 시간 표현 (W3C Time 미사용 — Instant/Interval 도메인 복잡도 회피)

---

## 11. Karpathy 가이드라인 적용 점검

| 원칙 | v1 평가 | v2 대응 |
|---|---|---|
| **Think Before Coding** | ✗ 사용자 시그널 없이 진행 | Phase 0 게이트로 명시화 |
| **Simplicity First** | △ 14 툴, 4 그래프, 2단계 라우터 | 툴 수는 spike로 결정, 라우터는 spike로 결정, Phase 3b 분리 |
| **Surgical Changes** | ✗ v0.3.2 pain point 미인용 | G1에서 명시적으로 사용자 issue 인용 강제 |
| **Goal-Driven Execution** | △ 성공 기준이 시연용 | 각 Phase에 정량적 완료 기준 (테스트 통과, 벤치마크 임계) |

---

## 부록 A — 받아들이지 않은 검토 권고

검토자가 제기했으나 채택하지 않은 권고와 근거:

| 권고 | 출처 | 채택하지 않은 이유 |
|---|---|---|
| "4번째 provenance 그래프 제거" | 표준 전문가 (반대 의견) | dump/restore/감사 시 운영 데이터와 메타데이터 분리가 깔끔. GDPR 삭제 요청 대응. PROV-O와 DCAT을 같은 그래프에 두되 역할 분리 명시. |
| "RDF-star로 전환" | 표준 전문가 (보류 의견) | MCP 툴 결과 JSON에서 State가 URI로 식별 가능해야 자연스럽다. RDF-star는 인터페이스 표현이 어색. State Object 유지. |
| "Pokemon 예제 완전 폐기" | LLM + pre-mortem | Pokemon은 OWL 추론·SPARQL 데모용으로 유지. 시계열 use case 입증은 IoT reference 도메인이 담당. 두 예제 병행. |
