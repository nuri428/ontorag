# Web UI

`ontorag serve` 후 브라우저에서 **<http://localhost:8000/ui>**를 엽니다.
UI는 HTMX 기반, 서버 렌더링이며 4개 탭으로 구성됩니다:

| 탭 | 역할 | 백킹 툴 |
|---|---|---|
| 📐 **Schema** | TBox 클래스 계층을 Cytoscape 그래프로 | `get_schema`, SHACL validate |
| 📊 **Data** | 클래스별 ABox 인스턴스 + 엔티티 드릴다운 | `find_entities`, `describe_entity`, `traverse_graph` |
| 🛝 **Playground** | LLM 에이전트 채팅, 툴 호출 실시간 표시 | 모든 MCP 툴 |
| 🧮 **Reasoning** | 베이지안 / 인과 인터랙티브 러너 (v0.8.4) | `compute_posterior`, `mpe`, `do_query`, `counterfactual`, `identify_effect` |

상단 nav에 언어 토글(KO / EN)이 있으며, 각 탭은 HTMX swap을 거쳐도 상태가
유지됩니다.

## Schema 탭

인터랙티브 클래스 계층:

- 노드 클릭 → 이웃(서브클래스·속성) 하이라이트.
- 더블클릭 → 초기화.
- **TBox 업로드** — TTL / JSON-LD / RDF-XML, *항상 replace* 모드 (스키마
  변경은 설계상 파괴적 — 점진적 스키마 수정은 `ontorag learn` 경로).
- **검증** — 문법 체크(rdflib parse) + 인라인 SHAPES TTL에 대한 SHACL
  적합성 체크.

![Schema 탭](https://github.com/nuri428/ontorag/raw/main/assets/TBox.png)

## Data 탭

클래스 선택 → 인스턴스 탐색:

- 행 클릭 → 모든 속성 + depth-2 이웃 그래프가 보이는 엔티티 상세 패널.
- **ABox 업로드** — append *또는* replace 모드 (데이터는 점증적).
- 객체 속성 값은 모든 백엔드(Fuseki / Neo4j / FalkorDB)에서 가독성 있는
  `label (URI)` 칩으로 렌더링됨 — 커밋 `8a4c00f` 참조.

같은 탭의 검색 패널은 `search_text` (BM25), `find_similar` (벡터 kNN),
`aggregate` (group-by + count/sum/avg)를 감쌉니다 — v0.5 capability 툴.

![Data 탭](https://github.com/nuri428/ontorag/raw/main/assets/ABox.png)

## Playground 탭

에이전트와 채팅. 툴 호출이 실행되는 대로 실시간 표시됩니다:

- `find_entities` 호출은 결과 전에 클래스 + 필터를 먼저 보여줌.
- 그래프 데이터가 있는 결과는 인터랙티브 결과 그래프로 렌더링 (Schema
  탭과 같은 Cytoscape 엔진).
- **History** 사이드바 — 세션은 `chat.db`에 영속, 전환 후 재개 가능.
- **LLM 설정**을 탭 내에서 — 서버 재시작 없이 프로바이더/모델/키 변경.
  변경은 `.env`에 기록되어 다음 채팅부터 적용.
- 레이트 리밋 처리 — LLM API가 레이트 리밋을 걸면 `retry_after` 배너가
  카운트다운하고 턴이 자동 재개.

![Playground 탭](https://github.com/nuri428/ontorag/raw/main/assets/playground.png)

## Reasoning 탭 (v0.8.4)

기존 HTMX-partial 패턴을 따르는 두 개의 서브탭. `[bayes]` extra와
`ontorag bayes load`로 로드된 베이지안 네트워크 필요.

### 베이지안

- 증거 빌더 (`variable = state`)와 질의 변수 선택.
- **사후확률 계산** → `P(query | evidence)`이 분포 막대로 렌더링
  (`partials/dist_bars.html`).
- **MPE** → 가장 가능성 높은 설명 (argmax joint).

### 인과

`ontorag causal load`로 로드된 DAG가 있을 때:

- `do(X)` 개입
- `counterfactual` 질의 (관측 + 개입)
- `identify` — 최소 백도어 + 모든 프론트도어 보정 집합

하이라이트:

- **DAG 엣지**가 폼 옆에 나열되어, 어떤 그래프 위에서 추론하는지 항상
  알 수 있음.
- 사후확률 결과의 **"do(X)로 비교 →"** 링크가 같은 증거를 개입으로
  Causal 탭에 seed — 두 번 클릭으로 see ≠ do 대비.
- v1.1 — 결과 막대 아래에 이제 **"why:" 트레이스**가 포함됨: 엔진이
  사용한 백도어 보정 집합 + "왜 do ≠ see인지" 한 줄 요약.

### Capability 가드

백엔드 / 네트워크 / `pgmpy`가 없으면 탭이 에러 대신 실행 가능한 앰버 힌트
(`partials/reasoning_error.html`)를 렌더링 — 어떤 env 변수나 extra가
빠졌는지 정확히 알려줌.

## 코드 위치

- 라우트 — `src/ontorag/web/router.py`
- 템플릿 — `src/ontorag/web/templates/` (Jinja2)
- 공유 partials — `templates/partials/dist_bars.html`,
  `instances_grid.html`, `reasoning_error.html`

## 테스트

Web UI 동작은 라우트 레벨에서 `tests/test_web_reasoning.py`로 회귀
테스트됨 (10개, pgmpy 없이도 capability 가드 실행; happy-path가 see 0.72 ≠
do 0.60 검증).
