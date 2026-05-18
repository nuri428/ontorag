# ontorag

**온톨로지 기반 RAG 프레임워크 — RDF/OWL 온톨로지를 진실의 원천(source of truth)으로.**

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](README.md)

---

일반적인 RAG 시스템은 지식을 텍스트 청크로 저장하고 임베딩 유사도로 검색합니다.
**ontorag**는 온톨로지 자체를 진실의 원천으로 취급합니다. LLM 에이전트가 근사적인 벡터 검색 대신, 구조화된 MCP 툴로 RDF/OWL 그래프를 직접 탐색합니다.

```
사용자 질문 → LLM 에이전트 → 온톨로지 툴 (get_schema / find_entities / traverse_graph …)
                                           ↓
                                Apache Jena Fuseki  (SPARQL 1.1)
                                           ↓
                                  구조화된 JSON 답변
```

---

## ontorag을 쓰는 이유 (vector RAG 대비)

동일한 TBox + ABox + goldset으로 측정한 결과입니다 — 전체 실행 내역은 [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) (LangChain + Chroma + OpenAI, 2개 도메인 합산 70 문항).

| 능력 | Vector RAG (LangChain) | ontorag |
|---|---|---|
| 단일 엔티티 조회 | ✓ Commerce easy 5/5 | ✓ |
| 멀티 홉 / OWL 전이 추론 (`pl:locatedIn+`) | ✗ Q008/Q039/Q040은 첫 홉에서 멈춤 | ✓ |
| 트리플 단위 인용 (감사 가능한 출처) | ✗ 0 / 70 (구조적 한계 — 청크만 반환) | ✓ 30 / 70 인용 |
| 정답 대비 환각 측정 가능성 | N/A (트리플 단위 grounding 없음) | ✓ 환각률 0.000 |
| KG에 없는 사실에 대한 거절 (trap 문항) | ✓ Commerce 3/3 거절 | ✓ |

Vector RAG는 단순 조회에 강합니다. ontorag의 구조적 우위는 **전이 추론·출처 인용·정량 grounding**에서 나타납니다.

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| **온톨로지 중심** | RDF/OWL 스키마(TBox) + 인스턴스 데이터(ABox)를 1등 시민으로 처리 |
| **Agentic MCP 루프** | LLM이 9개의 타입 툴을 호출; 툴 호출 내역이 SSE 스트림에서 실시간 노출 |
| **Web UI** | 브라우저 내장 인터페이스 — 스키마 그래프, 데이터 탐색, Playground 채팅 (`/ui`) |
| **멀티 LLM** | Anthropic Claude · OpenAI · Ollama(로컬) 지원 |
| **GraphStore Protocol** | 추상 인터페이스 — 툴 코드 변경 없이 Fuseki → Neo4j 교체 가능 |
| **SSE 스트리밍** | `thinking / tool_call / tool_result / text / done / rate_limit` 이벤트 |
| **점진적 공개** | `get_schema` (간략) + `get_class_detail` (드릴다운) |
| **인젝션 안전 L2 DSL** | `query_pattern`은 JSON 트리플 패턴을 내부적으로 SPARQL로 변환 |
| **스키마 캐싱** | 세션 시작 시 스키마를 system prompt에 주입 — 매 턴 `get_schema` 호출 불필요 |
| **Docker 우선** | `docker compose up` → 60초 이내 준비 완료 |

---

## 빠른 시작

**사전 요구 사항:** Docker · Docker Compose · Anthropic _또는_ OpenAI API 키

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
cp .env.example .env           # ANTHROPIC_API_KEY (또는 OPENAI_API_KEY) 설정

docker compose up -d           # Fuseki + API 시작

uv run ontorag load schema examples/pokemon/schema.ttl
uv run ontorag load data   examples/pokemon/data.ttl

uv run ontorag chat
```

실행 예시:

![포켓몬 채팅 데모](assets/pokemon_chat.png)

---

## Web UI

서버 실행 후 브라우저에서 **http://localhost:8000/ui** 를 열면 됩니다.

### Schema 탭 (TBox)

온톨로지 클래스 계층 구조를 Cytoscape.js 인터랙티브 그래프로 탐색합니다. 노드를 클릭하면 이웃 노드가 하이라이트되고, 더블클릭하면 초기화됩니다. TBox 파일 업로드(항상 교체 모드)와 구문/SHACL 검증을 브라우저에서 바로 실행할 수 있습니다.

![Schema 탭](assets/TBox.png)

### Data 탭 (ABox)

클래스를 선택하면 해당 인스턴스 목록이 표시됩니다. 행을 클릭하면 엔티티의 모든 속성과 depth-2 이웃 그래프가 사이드 패널에 나타납니다. ABox 파일을 **추가** 또는 **교체** 모드로 업로드할 수 있습니다.

![Data 탭](assets/ABox.png)

### Playground 탭

LLM 에이전트와 채팅합니다. `find_entities`, `traverse_graph` 등 툴 호출이 실행되는 즉시 화면에 표시됩니다. 그래프 데이터가 포함된 응답은 인터랙티브 결과 그래프로 시각화됩니다. 대화 세션 관리와 LLM 제공자 설정을 서버 재시작 없이 UI에서 변경할 수 있습니다.

![Playground 탭](assets/playground.png)

---

## 아키텍처

```
사용자 (CLI / 브라우저)
  │
  ▼  POST /chat   (SSE 스트림)
┌────────────────────────────────────────┐
│             FastAPI 서버               │
│                                        │
│   /chat ──▶  AgentLoop                 │
│                  │                     │
│        LLM  (Claude / GPT / Ollama)    │
│                  │  tool_use           │
│  ┌───────────────────────────────────┐ │
│  │  L1 의도 툴  (MCP 노출):          │ │
│  │  get_schema        find_entities  │ │
│  │  get_class_detail  describe_entity│ │
│  │  count_entities    traverse_graph │ │
│  │  find_path         find_related   │ │
│  │  L2 DSL:  query_pattern           │ │
│  │  L3 개발:  query_sparql_raw (숨김)│ │
│  └───────────────┬───────────────────┘ │
└──────────────────┼─────────────────────┘
                   │ SPARQL (HTTP)
                   ▼
        Apache Jena Fuseki   ← v0.1–v0.3.2
        Neo4j + n10s         ← v0.5
```

### SSE 이벤트 타입

| 이벤트 | 페이로드 | 발생 시점 |
|---|---|---|
| `thinking` | `content: str` | LLM 턴 시작 전 |
| `tool_call` | `tool: str, content: dict` | LLM이 툴 호출 |
| `tool_result` | `tool: str, content: any` | 툴 실행 결과 |
| `text` | `content: str` | LLM 최종 답변 청크 |
| `done` | — | 턴 완료 |
| `error` | `content: str` | 복구 불가 오류 |
| `rate_limit` | `retry_after: int` | API 레이트 리밋 도달 — N초 후 재시도 |

---

## 설치

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
uv sync          # 의존성 설치
```

[uv](https://docs.astral.sh/uv/)와 Docker가 필요합니다.

---

## 설정

```bash
# Anthropic (기본값)
ontorag config set --provider anthropic --api-key sk-ant-...

# OpenAI
ontorag config set --provider openai --api-key sk-...

# Ollama (로컬, 키 불필요)
ontorag config set --provider ollama --ollama-url http://localhost:11434

# 모델 변경
ontorag config set --model claude-opus-4-7
ontorag config set --model gpt-4o-mini

# Fuseki 엔드포인트
ontorag config set --fuseki-url http://localhost:3030

# 설정 확인
ontorag config show
```

설정은 현재 디렉터리의 `.env` 파일에 저장됩니다.

### 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` · `openai` · `ollama` |
| `LLM_MODEL` | 제공자 기본값 | 모델 이름 |
| `ANTHROPIC_API_KEY` | — | Anthropic 사용 시 필수 |
| `OPENAI_API_KEY` | — | OpenAI 사용 시 필수 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 서버 주소 |
| `FUSEKI_URL` | `http://localhost:3030` | SPARQL 엔드포인트 |
| `FUSEKI_DATASET` | `ontorag` | 데이터셋 이름 |

---

## CLI 레퍼런스

```bash
ontorag init [DIR]              # 프로젝트 파일 생성 (docker-compose, .env.example, examples)

ontorag load schema <FILE>               # TBox 로드 (클래스/속성 정의)
ontorag load data   <FILE>               # ABox 로드 — 기존 데이터에 추가
ontorag load data   <FILE> --replace     # ABox 로드 — 기존 데이터를 교체
ontorag load        <FILE>               # TBox/ABox 자동 감지

ontorag clear schema                     # TBox(스키마) 그래프 삭제
ontorag clear data                       # ABox(인스턴스) 그래프 삭제
ontorag clear all                        # TBox + ABox 전체 삭제

ontorag serve [--host HOST] [--port PORT] [--reload]

ontorag chat                    # 대화형 REPL

ontorag status                  # 그래프 스토어 연결 + 트리플 수 확인

ontorag config set [OPTIONS]
ontorag config show

# v0.3 — 텍스트에서 온톨로지 학습
ontorag learn type-term "React"                        # Task A — 용어를 TBox 클래스에 매핑
ontorag learn taxonomy corpus.txt                      # Task B — rdfs:subClassOf 제안
ontorag learn extract corpus.txt                       # Task C — RDF 트리플 추출
ontorag learn populate corpus.txt [--yes]              # A+B+C 파이프라인 → Fuseki

# v0.3.1 — 구조화 파일에서 ABox 확장 (CSV / JSON / JSONL)
ontorag learn populate-structured data.csv \
    --class-uri pk:Pokemon --id-column name [--yes]
ontorag learn populate-structured data.jsonl --batch-size 100 --yes
ontorag learn populate-structured nested.json --min-confidence 0.8
```

---

## REST API

### `POST /chat`

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "불꽃 타입 포켓몬 목록을 보여줘"}'
```

```
data: {"type": "thinking",    "content": "분석 중... (턴 1)"}
data: {"type": "tool_call",   "tool": "get_schema",      "content": {}}
data: {"type": "tool_result", "tool": "get_schema",      "content": {...}}
data: {"type": "tool_call",   "tool": "find_entities",   "content": {...}}
data: {"type": "tool_result", "tool": "find_entities",   "content": [...]}
data: {"type": "text",        "content": "불꽃 타입 포켓몬: 파이리, 파이밤, 리자드, ..."}
data: {"type": "done"}
```

### `GET /mcp`

MCP (Model Context Protocol) 엔드포인트. MCP 호환 클라이언트가 연결해 9개의 온톨로지 툴을 직접 호출할 수 있습니다.

---

## MCP 툴 목록

| 툴 | 레이어 | 설명 |
|---|---|---|
| `get_schema` | L1 | 클래스 목록과 속성 수 (~30 tokens/class) |
| `get_class_detail` | L1 | 특정 클래스의 속성·부모·자식·인스턴스 샘플 |
| `find_entities` | L1 | 클래스 + 선택적 조건으로 인스턴스 탐색 |
| `describe_entity` | L1 | 단일 엔티티의 모든 속성과 관계 |
| `count_entities` | L1 | 클래스 인스턴스 수 집계 |
| `traverse_graph` | L1 | 노드에서 BFS 순회 (나가는/들어오는/양방향) |
| `find_path` | L1 | 두 엔티티 간 최단 경로 탐색 |
| `find_related` | L1 | predicate로 연결된 두 클래스 인스턴스 쌍 조회 |
| `query_pattern` | L2 | JSON 트리플 패턴 DSL → 안전한 SPARQL 변환 |

---

## v0.3 — LLMs4OL: 텍스트에서 온톨로지 학습

v0.3은 **LLMs4OL 파이프라인**을 추가합니다. LLM이 일반 텍스트를 읽고 기존 온톨로지를 확장하는 RDF 트리플을 제안합니다. 수동 저작 없이 그래프를 성장시킵니다.

### CLI 명령어

![learn --help](assets/learn_help.png)

### Task A — 용어 타이핑 (`type-term`)

텍스트 언급을 가장 적합한 TBox 클래스에 매핑하고, 신뢰도와 근거를 함께 반환합니다.

```bash
ontorag learn type-term "Pikachu" --context "진화한 포켓몬"
ontorag learn type-term "React"
```

![learn type-term 출력](assets/learn_type_term.png)

### A+B+C 파이프라인 (`populate`)

세 가지 태스크를 순서대로 실행합니다 — 용어 타이핑 → 분류 계층 발견 → 관계 추출 — 그 후 승인된 트리플을 Fuseki에 로드합니다.

```bash
ontorag learn populate examples/techstack/corpus.txt
```

![learn populate 출력](assets/learn_populate.png)

### 구조화 파일 ABox 확장 (`populate-structured`) — v0.3.1

**CSV / JSON / JSONL** 파일을 읽어 LLM으로 컬럼을 TBox 속성 URI에 매핑하고, 각 행을 RDF 트리플로 변환합니다. 컬럼 매핑 결과는 사이드카 `.mapping.json` 파일에 저장되어 — 이후 실행에서는 LLM 호출 없이 재사용됩니다.

```bash
# 첫 번째 실행: LLM이 컬럼을 매핑 → pokemon.csv.mapping.json 저장
ontorag learn populate-structured pokemon.csv \
    --class-uri pk:Pokemon --id-column name

# 두 번째 실행: 캐시 재사용, LLM 호출 없음
ontorag learn populate-structured pokemon.csv --yes

# JSON / JSONL (중첩 키는 자동 평탄화: {"stats":{"hp":35}} → "stats.hp")
ontorag learn populate-structured pokedex.jsonl --batch-size 100 --yes
```

![learn populate-structured 출력](assets/learn_populate_structured.png)

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--class-uri` | — | 각 행의 TBox 클래스 URI (예: `pk:Pokemon`) |
| `--id-column` | — | 주어 URI 슬러그로 사용할 컬럼; 생략 시 uuid5 자동 발급 |
| `--batch-size` | 50 | LLM 매핑 호출당 처리 행 수 |
| `--min-confidence` | 0.7 | 컬럼 매핑 최소 신뢰도 임계값 |
| `--yes` | false | Fuseki 로드 확인 프롬프트 생략 |

### 테스트 스위트 — v0.3.1 (264개 테스트)

![v0.3.1 테스트 결과](assets/learn_tests.png)

---

## 예제: 기술 스택 온톨로지 (v0.3 — LLMs4OL)

일반 벡터 검색 RAG로는 불가능한 기능을 직접 보여주는 예제입니다.

**1단계 — 시드 온톨로지 로드** (React, Next.js, Node.js, TypeScript 등 15종)

```bash
uv run ontorag load schema examples/techstack/schema.ttl
uv run ontorag load data   examples/techstack/data.ttl
```

**2단계 — 텍스트로 온톨로지 확장** (v0.3 LLMs4OL 파이프라인)

```bash
# 텍스트 코퍼스 입력 → LLM이 타입·관계 추출 → RDF 트리플 제안
uv run ontorag learn populate examples/techstack/corpus.txt
```

**3단계 — OWL 추론 포함 자연어 질의**

```
> Next.js는 무엇에 의존하나요?
```
답변: Next.js → React → Node.js
*(Next.js dependsOn Node.js는 명시되지 않았지만 Fuseki가 `owl:TransitiveProperty`로 추론합니다.)*

자세한 사용 방법은 [`examples/techstack/README.md`](examples/techstack/README.md)를 참고하세요.

---

## 예제: 포켓몬 온톨로지

번들 예제는 프레임워크의 모든 기능을 실증합니다.

```
examples/pokemon/
├── schema.ttl   # TBox: Pokemon, LegendaryPokemon, Type, Move, Trainer, Region
└── data.ttl     # ABox: 관동 지방 · 포켓몬 12종 · 트레이너 3명 · 타입 18종
```

**온톨로지 설계 포인트:**

- `pk:evolvesFrom` — `owl:TransitiveProperty`로 선언; Fuseki 추론으로 전체 진화 체인 자동 추적
- `pk:LegendaryPokemon rdfs:subClassOf pk:Pokemon` — `find_entities(Pokemon)` 호출 시 뮤츠 자동 포함
- `strongAgainst` / `weakAgainst` — 타입 상성을 오브젝트 프로퍼티로 모델링

**예제 질문:**

```
> 리자몽의 전체 진화 체인을 알려줘
> 지우의 포켓몬은 어떤 것들이야?
> 물 타입에 약한 포켓몬을 찾아줘
> 뮤츠의 전체 스탯을 보여줘
```

![포켓몬 진화 체인 질의 예시](assets/pokemon_chat.png)

---

## LLM 제공자

| 제공자 | 키 변수 | 기본 모델 | 특징 |
|---|---|---|---|
| **Anthropic** (기본값) | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | 툴 사용 정확도 최고 |
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o` | |
| **Ollama** | `OLLAMA_BASE_URL` | `llama3.1` | 로컬 실행, 키 불필요 |

---

## Docker

```bash
# 개발 — 코드 변경 시 자동 재시작
docker compose up

# 프로덕션
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

| 서비스 | 포트 | 설명 |
|---|---|---|
| `fuseki` | 3030 | Apache Jena Fuseki; 관리 UI: `/dataset.html` |
| `api` | 8000 | ontorag FastAPI; OpenAPI: `/docs`, MCP: `/mcp` |

---

## 타 프레임워크 비교

| 프레임워크 | 온톨로지 | 에이전트 | 비고 |
|---|---|---|---|
| LangChain / LlamaIndex | 최소 지원 | Yes | 코드 중심 RAG, 온톨로지 플러그인 수준 |
| Dify | 미지원 | Yes | 비주얼 빌더, OWL 미지원 |
| GraphRAG (Microsoft) | 텍스트→프로퍼티 그래프 | Yes | OWL 시맨틱 없음 — `rdfs:subClassOf` 추론·`owl:TransitiveProperty`·SPARQL 미지원; 스키마가 쿼리 시 강제되지 않음 |
| **ontorag** | **OWL-native** | **Yes** | TBox가 스키마를 정의; Fuseki가 OWL 추론 강제; v0.3에서 LLMs4OL로 텍스트→온톨로지 확장 |

---

## 평가 하네스 — `ontorag eval`

`eval-harness` 브랜치에서 사용 가능한 내장 평가 도구. ontorag을
벡터 RAG baseline과 정량 비교하기 위한 goldset+metric 파이프라인.

### 제공 기능

- **두 벤치마크 도메인** — `examples/pure_land/` (50문항, fictional+religious — 다국어 라벨 서방정토 우주관) + `examples/commerce/` (20문항, schema.org 표준 어휘 + 가상 회사 인스턴스)
- **Goldset 형식** — JSONL with `gold_sparql`, `gold_answer`, `gold_triples`, `uses_inference`. Pydantic 검증.
- **5개 메트릭** — `sparql_result_equivalent`, `inference_utilization`, `hallucination_rate`, `citation_coverage`, RAGAS (`faithfulness`, `answer_correctness`, `answer_relevancy`)
- **Baseline** — `ontorag_mock` (perfect retrieval 상한), `vector_rag_mock` (70/20/10 bucket 시뮬), `langchain` (실제 RetrievalQA + Chroma + OpenAI — `--extra bench` + API key 필요)
- **Markdown 리포트** — PR 코멘트/블로그 포스트에 그대로 부착 가능
- **CI 통합** — GitHub Actions matrix가 두 도메인 모두 PR마다 실행 + artifact upload + sticky comment

### 명령어

```bash
# Goldset 검증
uv run ontorag eval validate examples/commerce/goldset.jsonl

# gold_sparql을 graph에 실행 (데이터 위생 체크)
uv run ontorag eval run examples/commerce/goldset.jsonl \
    --schema examples/commerce/schema.ttl \
    --data examples/commerce/data.ttl \
    --output report.json

# Baseline + 메트릭 통합 실행
uv run ontorag eval bench examples/commerce/goldset.jsonl \
    --baseline ontorag_mock \
    --schema examples/commerce/schema.ttl \
    --data examples/commerce/data.ttl \
    --output ontorag.json

# 두 결과 비교 Markdown
uv run ontorag eval compare ontorag.json langchain.json \
    --name-a ontorag --name-b langchain \
    --output comparison.md

# JSON → Markdown 리포트
uv run ontorag eval report ontorag.json --output report.md
```

### 실제 LangChain baseline + RAGAS (~$1/실행)

```bash
uv sync --extra bench
export OPENAI_API_KEY=sk-...

uv run ontorag eval bench examples/commerce/goldset.jsonl \
    --baseline langchain \
    --schema examples/commerce/schema.ttl \
    --data examples/commerce/data.ttl \
    --with-ragas \
    --output langchain_real.json
```

전체 측정 이력(v2~v9)과 정직한 결과 정리는
[`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md)를 참조하세요.

---

## 벤치마크 결과 — 4-도메인 RAGAS final (2026-05)

**네 개의 온톨로지 도메인**에 대해 **agent = `gpt-4o`**, **judge = `gpt-4o`** (RAGAS LLM-as-judge)로 head-to-head 벤치마크를 돌렸습니다. 비교한 두 baseline은:

| Baseline | 설명 |
|---|---|
| `langchain` | 고전적 vector RAG — TTL chunk를 Chroma에 인덱싱 + OpenAI embedding + `gpt-4o` RetrievalQA. 그래프 추론 없음. |
| `ontorag_native` | ontorag 자체 agent loop — `gpt-4o`가 Apache Jena Fuseki(OWL 추론 활성)를 9개 MCP 툴로 호출. |

### 네 도메인 — OWL 기능 조합을 다양하게 커버

| 도메인 | 문항 수 | 언어 | OWL 기능 조합 | LLM 사전학습 오염도 |
|---|---|---|---|---|
| **Pokemon** | 20 | 한국어 | TransitiveProperty 1개 (`evolvesFrom`), 작은 ABox (~50 인스턴스) | **매우 높음** — 1세대 포켓몬은 모든 frontier LLM이 사전학습 |
| **Techstack** | 20 | 한국어 | TransitiveProperty 1개 (`dependsOn`), 작은 ABox (15 기술) | **매우 높음** — React/Node/TS는 어디든 |
| **ODS** (Open Data Structures) | 20 | 영어 | TransitiveProperty 2개 (`uses`, `specialises`) + inverseOf 쌍 (`implements` ↔ `implementedBy`) | 높음 — Pat Morin 공개 교재 |
| **Pure Land** | 50 | 한국어 | TransitiveProperty (`locatedIn`) + 다국어 라벨 (`@ko/@zh-Hant/@en`) + 큰 ABox (717 트리플) | 낮음 — 서방정토 우주관, fictional+religious |

도메인은 두 축에서 변동하도록 의도적으로 선정했습니다 — **OWL 기능 풍부도**와 **LLM이 답을 이미 외우고 있는 정도**.

### 측정 결과

(domain, baseline) 쌍마다 RAGAS LLM-as-judge 3개 메트릭(Faithfulness, AnswerCorrectness, AnswerRelevancy) + 결정론적 2개 메트릭(SPARQL 근거 기반 Hallucination, Citation 제공률)을 측정. **굵게**는 도메인 내 우위.

| 도메인 | Baseline | Faithfulness | Correctness | Relevancy | Hallucination | Citation% |
|---|---|---|---|---|---|---|
| Pokemon | LangChain | **0.677** | 0.448 | 0.342 | — | 0% |
| Pokemon | ontorag_native | 0.423 | **0.466** | **0.349** | **0.000** | **65%** |
| Techstack | LangChain | **0.808** | **0.523** | **0.420** | — | 0% |
| Techstack | ontorag_native | 0.333 | 0.382 | 0.279 | **0.000** | **45%** |
| ODS | LangChain | 0.521 | 0.493 | 0.641 | — | 0% |
| ODS | ontorag_native | **0.551** | **0.515** | **0.749** | **0.000** | **65%** |
| Pure Land | LangChain | 0.345 | 0.260 | 0.180 | — | 0% |
| Pure Land | ontorag_native | **0.422** | **0.381** | **0.357** | **0.000** | **66%** |

### 표 해석 — 세 가지 발견

#### 발견 1. LLM judge의 Faithfulness 점수는 "원문 인용 스타일"을 좋아함

Pokemon·Techstack에서 LangChain Faithfulness가 0.677/0.808로 크게 우위입니다. 이건 LangChain이 **더 진실한 답을 생성한다는 증거가 아닙니다** — RAGAS judge가 "검색된 chunk와 어휘가 얼마나 겹치는가"를 본질적으로 좋아한다는 증거입니다. LangChain은 TTL 텍스트를 거의 그대로 인용하므로 겹침이 큼.

ontorag_native는 반대로 **SPARQL을 실행하고 결과를 유창한 답변으로 재구성**합니다. 사실(fact)은 동일해도 표현이 원문과 달라지므로 judge가 페널티를 줍니다.

style 차이라는 증거는 옆 칸 두 메트릭에서 확인됩니다 — Pokemon에서 ontorag의 **AnswerCorrectness가 더 높고**(0.466 vs 0.448), **AnswerRelevancy도 더 높습니다**(0.349 vs 0.342). 같은 사실, 다른 스타일.

#### 발견 2. OWL 기능이 풍부할수록 ontorag 우위가 커짐

네 도메인이 동원하는 독립적 OWL 기능 수로 비교:

| 도메인 | TransitiveProperty | inverseOf | 다국어 라벨 | ontorag가 우위인 메트릭 수 (5개 중) |
|---|---|---|---|---|
| Pokemon | 1 | ✗ | ✗ | 3/5 |
| Techstack | 1 | ✗ | ✗ | 2/5 |
| ODS | 2 | ✓ | ✗ | 5/5 |
| Pure Land | 1 | ✓ | ✓ | 5/5 |

OWL 추론 축이 **1축뿐인 도메인**(Pokemon, Techstack)에선 vector RAG의 chunk-quote 이점이 style 메트릭에 그대로 반영됩니다. **2축 이상**(ODS의 두 TransProp + inverseOf; Pure Land의 transitive locatedIn + 다국어 라벨 + 큰 ABox)에선 그래프 추론이 *모든* RAGAS 메트릭(Faithfulness 포함)을 이깁니다.

가장 극단적인 경우는 Pure Land. AnswerRelevancy가 0.180(LangChain)에서 0.357(ontorag)로 **상대 98% 개선**. 왜? 질문과 정답이 서로 다른 언어일 수 있고 URI가 그것들을 잇지만, vector 인덱스는 그것들을 무관한 chunk로 봅니다.

#### 발견 3. Hallucination 0% / Citation 45-66% — ontorag 전용

우측 두 열을 보면 **네 도메인 모두**에서 ontorag는 **Hallucination 정확히 0.000**, Citation 제공률 45-66%입니다. LangChain은 두 칸 모두 "—"인데 — **"—"는 "0"이 아닙니다**. 설명이 필요합니다.

이 두 메트릭은 *결정론적*(deterministic — LLM judge 아님)입니다. 하네스가 각 답변에서 명시적 트리플/URI 인용을 파싱한 뒤, 그 트리플이 실제 ABox에 존재하는지 코드로 검증합니다.

* `citation_provided_rate` = "답변이 무언가를 인용했는가?"
* `citation_coverage` = "인용한 것 중 실제 ABox 트리플과 매칭되는 비율"
* `hallucination_rate` = "인용한 것 중 **ABox에 없는** 트리플의 비율"

세 메트릭 모두 1단계 — **답변에 인용이 있어야** — 가 충족되어야 2단계 검증이 정의됩니다. LangChain의 `RetrievalQA`는 chunk를 prompt에 넣고 자연어 답변만 받아오며 RDF 트리플을 절대 출력하지 않습니다. 결과:

* `citation_provided_rate = 0%`은 **실측치** (110개 질문 전체에서 단 한 번도 인용 안 함)
* `hallucination_rate = "—"`는 **"측정 불가"**이지 "0"이 아님. 검증할 인용이 없으면 falsifiable test 자체가 정의되지 않습니다. 여기서 0이라고 적으면 "안전하다"는 거짓 주장이 됩니다.

ontorag의 agent loop는 MCP 툴을 통해 SPARQL을 실행하고 결과 트리플을 답변 안에 "근거"로 첨부합니다. 하네스가 그것을 추출·검증할 수 있으니 같은 칸이 ontorag엔 채워지고 — 게다가 전부 통과(Hallucination 0, 인용률 45-66%).

**이게 LLM judge에 의존하지 않는 구조적 해자입니다.** 자신만만하게 틀린 답을 만드는 비용이 큰 도메인(법률·의료·학술 KG·다언어 카탈로그)에서 가치가 여기 있습니다. 각 goldset에는 20%의 **trap 질문**(LLM이 사전학습에서 본 적 있지만 온톨로지엔 없는 엔티티 — *이브이* / *Vue.js* / *SplayTree* / *뮤*)이 있는데, ontorag는 SPARQL이 empty rows를 반환하면 답을 만들지 않습니다.

> **LangChain의 환각도 같은 축에서 보고 싶다면?** 3열의 RAGAS Faithfulness가 가장 가까운 LLM-judged 대용입니다 — 다만 *fuzzy 유사도 점수*일 뿐, 실제 그래프에 대한 *falsifiable 예/아니오* 검증은 아닙니다.

### 실무적 결론

| 당신의 도메인이... | 추천 |
|---|---|
| LLM 오염 심함 + ABox 작음 + 인용 스타일이 중요 | **LangChain**이 RAGAS 점수 우위; ~$0.45/실행 |
| OWL 기능 풍부 (TransProp ≥2, inverseOf, 다국어 라벨) | **ontorag**가 모든 RAGAS 메트릭 우위 |
| Hallucination 비용 > retrieval 비용 (법률/의료/학술) | **ontorag** — 답을 만들지 않고 거절함 |
| 답이 어느 트리플에서 나왔는지 짚어야 함 | **ontorag** — 유일하게 citation을 출력 |

문항별 상세와 v2→v9 반복 이력은 [`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md)에 있습니다. 도메인별 심층 분석은 각 `examples/<domain>/README.md` 참조.

### 재현 방법

```bash
docker compose up -d                           # Fuseki
cp .env.example .env && OPENAI_API_KEY 설정    # OpenAI 키 필수
echo "LLM_MODEL=gpt-4o"            >> .env     # agent 모델
echo "RAGAS_JUDGE_MODEL=gpt-4o"    >> .env     # judge 모델 (opt-in)

# 도메인마다 그래프 clear → load → 두 baseline 실행:
uv run ontorag load schema examples/pokemon/schema.ttl
uv run ontorag load data   examples/pokemon/data.ttl

uv run ontorag eval bench examples/pokemon/goldset.jsonl \
    --baseline langchain      --schema examples/pokemon/schema.ttl \
    --data examples/pokemon/data.ttl --lang ko --with-ragas \
    --output examples/pokemon/bench_results/langchain_gpt4o.json

uv run ontorag eval bench examples/pokemon/goldset.jsonl \
    --baseline ontorag_native --schema examples/pokemon/schema.ttl \
    --data examples/pokemon/data.ttl --lang ko --with-ragas \
    --output examples/pokemon/bench_results/ontorag_native_gpt4o.json
```

대략적인 비용: agent와 judge 모두 `gpt-4o`로 4-도메인 × 2-baseline 풀 실행 시 ~$7-9.

---

## 로드맵

- **v0.1** — Fuseki · Anthropic · OpenAI · Ollama · CLI · SSE 스트리밍 ✅
- **v0.2** — Web UI (Schema/Data/Playground) · 브라우저 RDF 업로드 · 레이트 리밋 UX · 온톨로지 데이터 존재 시 툴 호출 강제 ✅
- **v0.3** — LLMs4OL: `ontorag learn` CLI (용어 타이핑 · 분류 발견 · 관계 추출) · `type_term` + `extract_triples` MCP 툴 · 기술 스택 예제 ✅
- **v0.3.1** — 구조화 ABox 확장: `populate-structured`로 CSV/JSON/JSONL 읽기 → LLM으로 컬럼을 TBox에 매핑 → RDF 트리플 → Fuseki; 매핑 캐시, uuid5 멱등 URI, 배치 체크포인팅 ✅
- **v0.3.2** — TBox/ABox 덤프: `ontorag dump schema|data|all` · `GET /dump` REST 엔드포인트 · Web UI 다운로드 버튼 · TTL / JSON / JSONL / XLSX 포맷 ✅
- **v0.4 (eval-harness 브랜치, 현재)** — Phase B 평가 하네스: 2개 벤치마크 도메인 (Pure Land 50q + Commerce 20q) · Goldset JSONL + Pydantic loader · 4개 결정론적 메트릭 + RAGAS wrapper · LangChain 벡터 baseline · `ontorag eval` CLI (validate/run/bench/compare/report) · GitHub Actions matrix CI · BenchRunner orchestrator ✅
- **v0.5** — Neo4j + n10s 어댑터 · `GRAPH_STORE` 환경 변수 · 벡터 유사도 툴 (`find_similar`) · 멀티 온톨로지 지원

---

## 기여하기

```bash
# 개발 환경 설정
uv sync --extra dev

# 테스트 실행
uv run pytest tests/ --cov=src/ontorag

# 개발 서버 실행
uv run ontorag serve --reload
```

---

## 라이선스

[MIT](LICENSE)
