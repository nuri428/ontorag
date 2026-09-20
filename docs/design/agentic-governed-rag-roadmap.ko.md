# OntoRAG 고도화 로드맵: Agentic·Evidence·Governed Ontology RAG

> **목표:** OntoRAG의 ontology-first 원칙을 유지하면서, 최신 Agentic RAG·Evidence Layer·Governed Knowledge 요구를 반영한다. 핵심은 더 많은 agent를 넣는 것이 아니라, **권한 있는 사용자가 최신의 근거 있는 지식을 안전하게 조회하고, 각 답변을 재현·감사할 수 있게 하는 것**이다.

> **검토 기준:** 2026-09-19, `main` 브랜치의 현재 구현과 문서. 이 문서는 기능 제안과 우선순위이며, 발견된 보안·운영 공백은 실제 수정 전 테스트와 전체 adapter 검토로 다시 확인한다.

---

## 1. 현재 강점과 고도화의 방향

OntoRAG에는 이미 다음 기반이 있다.

- RDF/OWL TBox+ABox를 source of truth로 쓰는 ontology-first 모델
- Fuseki, Neo4j, FalkorDB의 GraphStore protocol parity
- L1 intent tool, L2 JSON DSL, typed MCP tool interface
- OWL logical, Bayesian, causal reasoning의 분리된 named graph layer
- BM25, structural/textual vector, vector RRF hybrid retrieval
- complexity router와 evaluator를 가진 multi-agent loop
- SHACL validator, ontology learning pipeline, manifest, evaluation suite
- tool call/result SSE와 citation·hallucination metric의 기반

따라서 다음 단계는 Microsoft GraphRAG의 community report를 그대로 복제하거나 full agent harness를 OntoRAG 내부에 넣는 것이 아니다. OntoRAG의 차별점인 **명시적 ontology, typed tool, 결정론적 graph reasoning, backend parity** 위에 다음 네 가지를 완성하는 것이다.

```text
1. 정책이 실제 모든 조회·변경 경로에서 집행됨
2. 답변 claim이 시간·출처·권위가 있는 evidence로 연결됨
3. RDF 변경, vector index, quality gate가 일관된 lifecycle로 동작함
4. graph / lexical / vector / causal 도구가 질문과 정책에 따라 명시적으로 선택됨
```

---

## 2. 목표 아키텍처

```text
Raw Source / RDF / Documents
  ↓
Ingestion Gate
  ├─ source version / hash
  ├─ mandatory SHACL validation
  ├─ quarantine + approval
  └─ immutable ingestion activity
  ↓
Governed Ontology Store
  ├─ schema / asserted data
  ├─ inferred / probabilistic / causal named graphs
  ├─ provenance / policy named graphs
  └─ valid time / observed time / authority
  ↓
Policy-aware Retrieval
  ├─ exact graph / OWL reasoning
  ├─ lexical BM25
  ├─ textual + structural vector
  ├─ causal / Bayesian
  └─ bounded evidence subgraph
  ↓
Evidence Layer
  ├─ claim → evidence ID → triple / source span
  ├─ source / version / time / authority / license
  ├─ policy decision and redaction
  └─ contradiction / sufficiency status
  ↓
Agent / MCP Client
  ├─ typed tool selection
  ├─ trace and evaluation
  └─ final response with citations and uncertainty
```

### 비목표

- OntoRAG 자체를 범용 autonomous agent harness로 바꾸지 않는다.
- LLM의 임시 추론이나 유사도 후보를 자동으로 authoritative ontology triple로 승격하지 않는다.
- 모든 질문에 vector, graph, causal, multi-agent loop를 동시에 호출하지 않는다.
- 첫 단계에서 전사 IdP/SSO, 범용 event sourcing, RDF-star 전환, GNN 학습을 모두 구현하지 않는다.

---

## 3. Phase 0: 실제 보호 경계 복구

### 문제

현재 ontology별 access control은 좋은 출발점이지만, 요청 주체가 없는 scope lock이다. 또한 union read와 capability method가 정책을 우회할 수 있는 경로가 있다.

확인 근거:

- `core/access.py`는 user identity 없는 ontology scope policy임을 명시하고, 미등록 scope를 write 허용으로 처리한다.
- `stores/access_wrapper.py`는 `ontology=None` union view를 통과시키며, `search_text`, `find_similar`, `build_embeddings` 등의 capability를 `__getattr__`로 전달한다.
- `query_pattern` 및 GraphStore mutation helper도 wrapper의 명시 guard 범위를 다시 검토해야 한다.
- chat tool의 주요 retrieval path가 scope를 항상 전달하지 않는지 확인이 필요하다.

### 구현

#### 3.1 RequestContext 도입

```python
class RequestContext(BaseModel):
    request_id: str
    subject_id: str
    tenant_id: str
    roles: set[str]
    purpose: str | None
    allowed_ontologies: set[str]
    policy_version: str
```

`RequestContext`를 API → AgentLoop → MCP tool handler → GraphStore adapter에 명시적으로 전달한다. ambient global이나 session ID만으로 권한을 판단하지 않는다.

#### 3.2 deny-by-default policy

- production mode에서는 미등록 ontology를 write 허용하지 않는다.
- `ontology=None` union은 모든 graph의 union이 아니라 **허용된 ontology만의 union**으로 재작성한다.
- BM25, vector, DSL, Bayes, Causal, export, write를 포함한 모든 method를 명시적으로 guard한다.
- `assert_triple`, `assert_triples`, `retract_triple`, `load_rdf`, `clear_graph`은 동일 write policy와 approval policy를 통과한다.
- tool response에 `policy_decision`, `permitted_scope`, `redacted_fields`를 포함한다.

현재 상태 (2026-09): Fuseki의 순수 SPARQL read는 허용 ontology의 named
graph만 `default-graph-uri`로 구성하는 경로가 검증되어 있다. 반면 Neo4j와
FalkorDB의 `_ontology`는 node membership만 보존하며, shared URI의 개별
relationship/property가 어느 ontology assertion에서 왔는지는 보존하지 않는다.
따라서 이 두 backend에 node-level `WHERE`를 더해 filtered union을 흉내 내는
것은 안전하지 않다. 제한 ontology가 하나라도 있으면 union read는 backend
접근 전에 **fail-closed**로 거부하며, assertion-level provenance를 보존하는
저장 모델과 backend별 누출 회귀 검증이 있기 전에는 이 상태를 유지한다.

#### 3.3 감사 event

정책 deny도 포함해 모든 tool call에 다음을 기록한다.

```text
request_id · subject/tenant · tool · ontology scope · policy version
allow/deny · redaction · result hash · latency · timestamp
```

### 완료 기준

- 제한 ontology의 entity가 union, BM25, vector, JSON DSL, graph traversal, export 어느 경로에서도 누출되지 않는다.
- mutation 경로가 role·purpose·approval 없이 실행되지 않는다.
- 모든 access test가 세 backend에서 동일하게 통과한다.
- security regression suite에 cross-tenant session, guessed session ID, vector zombie, union bypass case가 포함된다.

---

## 4. Phase 1: Claim-level Evidence Layer

### 문제

현재 tool call/result trace와 citation metric은 존재하지만, 사용자 답변의 각 claim을 stable evidence object로 강제 연결하는 runtime contract가 완성되지 않았다. triple 존재 확인은 provenance, time, authority, version을 대신하지 못한다.

### 구현

#### 4.1 최소 EvidenceRecord

`provenance` named graph를 실제 read/write path로 활성화하고, PROV-O/DCAT와 호환되는 최소 필드부터 도입한다.

```text
EvidenceRecord
- evidence_id
- claim_id
- triple_id / triple snapshot
- assertion_status: asserted | inferred | proposed | approved | revoked
- source_distribution_uri
- source_locator / source_span
- source_hash / source_version
- ingestion_activity_id / agent / generated_at
- observed_at
- valid_from / valid_to
- authority_tier
- license
- ontology_scope
- policy_decision_id
```

#### 4.2 Citation API와 SSE

- retrieval tool은 `evidence_ids`와 source locator를 반환한다.
- answer synthesizer는 문장 또는 claim별 `citation_ids`를 반환한다.
- SSE에 `citation` 또는 `claim_evidence` event를 추가한다.
- claim이 support되지 않으면 합성하지 않고 `insufficient_evidence`, `conflicting_evidence`, `stale_evidence`로 분기한다.

```text
Claim
  ↓
Evidence IDs
  ↓
Triple / source span / snapshot
  ↓
source version · valid time · authority · policy decision
```

#### 4.3 시간과 권위

- `observed_at`: 시스템이 source를 본 시각
- `valid_from/to`: 사실이 현실에서 유효한 시각
- `generated_at`: RDF/evidence artifact 생성 시각
- `authority_tier`: source 우선순위

동일 subject-predicate의 충돌은 삭제하거나 평균내지 않는다. source와 time을 보존하고 policy에 따라 current authoritative claim, qualified answer, abstain을 선택한다.

### 완료 기준

- goldset answer의 claim별 citation precision/coverage를 계산할 수 있다.
- API 응답만으로 특정 claim을 source span, ontology snapshot, ingestion activity까지 추적할 수 있다.
- stale, expired, conflicting evidence case에서 시스템이 확정 답변 대신 상태를 표시한다.

---

## 5. Phase 2: 안전한 Ontology Learning과 Adaptive Lifecycle

### 문제

SHACL validator는 구현돼 있지만, auto-load에서 shapes가 선택 사항이면 LLM 추출 triple이 production ABox로 직접 들어갈 수 있다. RDF 삭제·정정과 vector index의 일관성도 batch rebuild에 의존한다.

### 구현

#### 5.1 Production ingestion gate

```text
Extract candidate triples
  ↓
Schema / term typing validation
  ↓
Mandatory SHACL validation
  ├─ fail → quarantine named graph
  ├─ low confidence → review queue
  └─ pass → staged graph
  ↓
policy / owner approval
  ↓
published asserted graph
```

- production auto-load에서는 `shapes_path`를 필수로 하고 fail-closed를 기본값으로 한다.
- quarantine triple은 asserted fact가 아니라 `proposed` 상태로만 조회 가능하게 한다.
- approval/revoke event와 reviewer·reason을 audit으로 남긴다.

#### 5.2 RDF-to-index outbox

RDF write/delete transaction은 index event를 만든다.

```text
RDF change transaction
  ↓
outbox event
  ├─ upsert embedding
  ├─ delete embedding
  ├─ rebuild structural embedding when required
  └─ update index freshness watermark
```

- vector delete/upsert 실패 시 해당 scope의 semantic retrieval을 차단하거나 `index_stale` 상태로 낮춘다.
- search 결과에는 `graph_snapshot`, `vector_index_version`, `freshness_watermark`를 포함한다.
- zombie vector, tombstone, retraction, policy 변경을 회귀 테스트한다.

#### 5.3 Trace-to-Graph은 승인 후보 생성에만 사용

agent의 반복 tool trace는 ontology 개선 후보를 찾는 데 유용하지만, runtime trace가 곧바로 fact가 되면 안 된다.

```text
Repeated trace
→ candidate alias / relation / missing ontology concept
→ source and SHACL validation
→ owner review
→ approved ontology update
```

### 완료 기준

- SHACL 실패 triple은 published ABox에 쓰이지 않는다.
- deletion/retraction 후 vector search에서 제거된 것을 자동 검증한다.
- source version과 index freshness를 모르면 semantic result를 authoritative evidence로 사용하지 않는다.

---

## 6. Phase 3: 정책·품질 인지형 Hybrid Retrieval

### 문제

현재 `search_text`의 BM25와 `find_similar`의 structural/textual vector RRF는 좋은 기반이다. 그러나 lexical BM25와 dense/graph vector를 하나의 후보 집합으로 fusion하고, 권한·freshness·authority·evidence completeness를 적용하는 단일 retrieval contract는 없다.

### 구현

#### 6.1 retrieve_hybrid tool

```python
retrieve_hybrid(
    query: str,
    scope: list[str],
    filters: RetrievalFilters,
    modes: set[Literal["graph", "bm25", "text_vector", "structural_vector"]],
    as_of: datetime | None,
    max_results: int,
) -> EvidenceBundle
```

처리 순서:

```text
Policy filter
  ↓
Graph / BM25 / text vector / structural vector candidate retrieval
  ↓
Deduplication by canonical entity and source lineage
  ↓
RRF or calibrated fusion
  ↓
Freshness · authority · evidence-completeness rerank
  ↓
EvidenceBundle
```

`hybrid`이라는 이름은 structural/textual vector RRF와 혼동되지 않도록 새 tool의 score component를 명시한다.

#### 6.2 작고 결정론적인 retrieval planner

복잡한 LLM planner보다 다음 규칙 기반 selector부터 시작한다.

| 질문 신호 | 우선 retrieval |
|---|---|
| exact URI, entity, relation | graph / OWL reasoning |
| identifier, 희귀 용어, 정확 문구 | BM25 |
| 유사 사례, 자연어 설명 | text vector |
| 구조적 유사성, role/dependency pattern | structural vector |
| 여러 entity와 evidence를 연결 | bounded graph traversal + hybrid evidence bundle |
| 개입·확률·반사실 | causal / Bayesian tool |

router log가 충분히 쌓인 뒤에만 typed decision model 또는 학습형 selector를 검토한다. Jev 같은 System One 판단 모델은 risk, route, sufficiency를 빠르게 점수화하는 보조 수단이 될 수 있지만, access policy나 authoritative fact 판정을 대신하지 않는다.

#### 6.3 DRIFT/Global Search의 도입 조건

Microsoft DRIFT는 community summary primer → follow-up local search라는 query-time retrieval mode다. OntoRAG에 이를 바로 복제하기보다, 다음 선행 조건이 충족될 때 선택적으로 도입한다.

- ontology graph에 community-level summary가 실제로 필요한 대규모 다문서 corpus인가?
- whole-corpus theme와 entity-level evidence를 함께 묻는 질문이 반복되는가?
- summary의 provenance와 source coverage를 유지할 수 있는가?

그 전까지는 OntoRAG의 explicit ontology traversal + hybrid retrieval + evidence bundle이 더 단순하고 감사 가능하다.

### 완료 기준

- 동일 질문에서 BM25-only, graph-only, vector-only, fusion 결과를 비교할 수 있다.
- 모든 candidate는 policy filter 후에만 rank되며 source/evidence를 반환한다.
- routing choice, score component, exclusion 이유가 DecisionTrace에 남는다.

---

## 7. Phase 4: 검증 가능한 Adaptive Agent Loop

### 문제

현재 complexity router, typed tool loop, evaluator는 이미 있다. 다음 개선은 더 많은 sub-agent가 아니라, route·tool·claim 판단을 evidence로 재현할 수 있게 만드는 것이다.

### 구현

#### 7.1 DecisionTrace

```text
DecisionTrace
- run_id / request_id / session_id
- request context and policy decision
- query classification and route reason
- selected retrieval mode and parameters
- tool arguments hash / result hash / latency / cost
- graph snapshot / vector index version
- candidate and evidence IDs
- claim verification result
- evaluator outcome: sufficient | ambiguous | insufficient
- retry / abstain / approval / stop reason
```

trace는 immutable append-only event 또는 dedicated trace graph에 저장한다. trace가 ontology assertion을 직접 만들지는 않는다.

#### 7.2 Evidence sufficiency gate

최종 답변 전 각 claim을 다음 중 하나로 분류한다.

```text
SUPPORTED
PARTIALLY_SUPPORTED
CONFLICTING
STALE
INSUFFICIENT
```

`SUPPORTED`만 단정 문장으로 합성한다. 나머지는 재검색, 조건부 답변, 인간 검토, abstain 중 하나로 분기한다.

#### 7.3 Feedback

사용자와 reviewer의 correction은 다음처럼 분리 저장한다.

```text
feedback event
→ ranking / router / ontology candidate 개선 신호
→ validation and review
→ approved change only enters governed ontology
```

feedback은 model prompt에 무비판적으로 누적하지 않는다.

### 완료 기준

- 하나의 answer가 어떤 route, tool, evidence, policy decision을 거쳐 나왔는지 재현 가능하다.
- evaluator의 충분성 판정과 실제 citation coverage·retrieval success를 별도로 측정한다.
- user feedback이 authoritative triple을 자동 수정하지 않는다.

---

## 8. Phase 5: Enterprise Evaluation Suite

기존 goldset과 backend parity benchmark는 유지한다. 별도로 governed retrieval suite를 추가한다.

### 평가 축

| 축 | 결정론적 평가 예시 |
|---|---|
| Access | tenant/role, union, DSL, BM25, vector, mutation 우회가 모두 차단되는가? |
| Evidence | claim-support precision/recall, source/version/hash 연결률, citation coverage |
| Time | expired/stale/current conflict에서 올바른 상태를 선택하는가? |
| Authority | 상충 source에서 authority policy를 적용하거나 qualified answer를 내는가? |
| Lifecycle | RDF delete/retract 뒤 vector zombie가 없는가? |
| Routing | query 유형별 mode 선택 정확도와 fallback 품질 |
| Adaptive | trace completeness, abstention accuracy, retry effectiveness |
| Efficiency | quality 대비 latency, tool count, token, cost |
| Reproducibility | 최소 3개 domain, 다국어, 3 backend, model/version/seed 신뢰구간 |

RAGAS 같은 LLM-based metric은 보조 지표로 사용한다. access, policy, time, provenance, graph existence는 deterministic expected result와 trace assertion으로 평가한다.

---

## 9. 권장 이행 순서

```text
Milestone 0: 보호 경계
- RequestContext
- deny-aware union
- capability/write guard
- security regression suite

Milestone 1: Evidence contract
- EvidenceRecord
- claim citation SSE/API
- as_of / authority / source version

Milestone 2: Lifecycle
- mandatory SHACL + quarantine
- approval/revoke
- RDF-to-vector outbox + freshness watermark

Milestone 3: Retrieval
- policy-aware retrieve_hybrid
- fusion/rerank/evidence bundle
- deterministic retrieval selector

Milestone 4: Adaptive quality
- DecisionTrace
- sufficiency gate
- governed retrieval evaluation suite
```

### 의존성

```text
Policy boundary
  → evidence visibility and audit
  → safe hybrid retrieval
  → adaptive routing and feedback

Evidence identity
  → citation
  → provenance/time/authority
  → quality evaluation

RDF-index consistency
  → trustworthy semantic retrieval
  → hybrid fusion
  → lifecycle SLA
```

---

## 10. 구현 참고

### 현재 코드와 문서에서 재사용할 기반

- `src/ontorag/core/access.py`: ontology scope policy의 출발점
- `src/ontorag/stores/access_wrapper.py`: 모든 GraphStore method를 명시 guard로 전환할 위치
- `src/ontorag/core/ontology.py`: named graph layer 확장
- `docs/design/named-graph-layers.md`: deferred policy/provenance layer 설계
- `docs/design/layered-ontology-plan.md`: PROV-O/DCAT, valid time, authority 설계 초안
- `src/ontorag/learn/shacl.py`: production ingestion gate에 재사용할 validator
- `src/ontorag/stores/_qdrant.py`: outbox/freshness 처리 대상
- `src/ontorag/chat/multi_agent/router.py`, `evaluator.py`, `loop.py`: DecisionTrace와 evidence sufficiency의 기반
- `src/ontorag/eval/metrics/citation.py`, `hallucination.py`: claim-level evidence evaluation 확장 지점
- `src/ontorag/eval/orchestrator.py`: governed retrieval suite roll-up 지점

### 외부 개념 참고

- [Microsoft GraphRAG / DRIFT Search](https://www.microsoft.com/en-us/research/blog/introducing-drift-search-combining-global-and-local-search-methods-to-improve-quality-and-efficiency/): broad primer와 local evidence refinement를 결합하는 query-time retrieval mode
- [W3C PROV-O](https://www.w3.org/TR/prov-o/): provenance vocabulary
- [W3C SHACL](https://www.w3.org/TR/shacl/): RDF validation
- [Open Policy Agent](https://www.openpolicyagent.org/docs/latest/): policy-as-code decision engine

---

## 결론

OntoRAG에 가장 먼저 필요한 것은 더 많은 LLM agent나 더 큰 graph가 아니다.

> **먼저 모든 retrieval과 write를 요청 주체·scope·policy 아래에 두고, 모든 answer claim을 시간·출처·권위가 있는 evidence로 연결하며, RDF와 semantic index의 lifecycle을 일관되게 만들어야 한다.**

이 기반 위에서 hybrid retrieval, DRIFT-style investigation, typed decision routing, adaptive graph improvement를 도입하면 OntoRAG는 ontology-aware RAG를 넘어 **감사 가능하고 재현 가능한 governed knowledge runtime**으로 확장될 수 있다.
