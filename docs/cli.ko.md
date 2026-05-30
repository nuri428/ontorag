# CLI 레퍼런스

`ontorag`는 14개 커맨드 그룹을 노출합니다. 모든 커맨드는 Typer + Rich
(프로그레스 바 + 컬러) 기반이며 활성 `GRAPH_STORE` 백엔드를 따릅니다.

## 빠른 맵

| 그룹 | 역할 |
|---|---|
| `ontorag load` | RDF (TTL / JSON-LD / RDF-XML / 디렉토리)를 활성 백엔드로 로드. |
| `ontorag clear` | 명명된 그래프 또는 전체 스토어 비움. |
| `ontorag config` | LLM 프로바이더 / 모델 / API 키 / 백엔드 URL 설정·조회. |
| `ontorag status` | 백엔드 헬스 + 로드된 트리플 수 + LLM 설정 표시. |
| `ontorag serve` | FastAPI 앱 기동 (REST + `/ui` + `/mcp`). |
| `ontorag chat` | 터미널 REPL 채팅. |
| `ontorag learn` | LLMs4OL 온톨로지 학습 (type-term / taxonomy / extract / populate). |
| `ontorag map` | 표 데이터 → RDF 매핑 (CSV / JSON / JSONL → 트리플). |
| `ontorag embed` | 구조 + 텍스트 그래프 임베딩 생성 (`find_similar`). |
| `ontorag eval` | Goldset 평가 (`run` / `bench` / **`reasoning`** v1.1). |
| `ontorag bayes` | 베이지안 레이어 — `load` / `show` / `posterior` / `mpe` / `learn-cpt` / `clear`. |
| `ontorag causal` | 인과 레이어 — `load` / `show` / `do` / `identify` / `counterfactual` / `learn-dag` / `clear`. |
| `ontorag shacl` | 로드된 데이터에 대한 SHACL 검증. |
| `ontorag-mcp` | 독립 **stdio** MCP 서버 (Claude Desktop / Cursor 진입점). |

## 데이터 로드

```bash
# 스키마(TBox) vs 데이터(ABox)
ontorag load schema ./ontology.ttl
ontorag load data   ./instances.ttl

# 자동 감지 (단일 파일)
ontorag load ./combined.ttl

# 디렉토리 로더
#   서브디렉토리명 = ontology id, 같은 스코프 안에서는 schema 먼저
ontorag load ./ontologies/
ontorag load ./ontologies/ --ontology my-onto --replace --no-recursive
```

`ontorag.yaml` 매니페스트(선택)로 기본 서브디렉토리 → 온톨로지 매핑을
오버라이드할 수 있습니다. `core/manifest.py` 참조.

## 설정

```bash
# 프로바이더 + 모델 + 키
ontorag config set --provider anthropic --api-key sk-ant-...
ontorag config set --provider openai    --model gpt-4o

# 백엔드 (env로도 설정 가능)
ontorag config set --graph-store neo4j \
    --neo4j-url bolt://localhost:7687 \
    --neo4j-user neo4j --neo4j-password ***

# 조회 (비밀번호는 마스킹)
ontorag config show
```

## 추론 CLI

### 베이지안 (`uv sync --extra bayes`)

```bash
ontorag bayes load        ./bayes.ttl
ontorag bayes show
ontorag bayes posterior   --evidence "OpponentType=Water" --query "BattleOutcome"
ontorag bayes mpe         --evidence "OpponentType=Water"
ontorag bayes learn-cpt   --data-class Pokemon
ontorag bayes clear
```

### 인과

```bash
ontorag causal load              ./causal.ttl
ontorag causal show
ontorag causal do                --do "Smoking=yes" --query "Cancer"
ontorag causal identify          --treatment "Smoking" --outcome "Cancer"
ontorag causal counterfactual    --observed "Smoking=yes,Cancer=yes" \
                                 --do       "Smoking=no" \
                                 --query    "Cancer"
ontorag causal learn-dag         --save         # 제안만 — 자동 커밋되지 않음
ontorag causal clear
```

!!! warning "Over-claim guard (과대주장 방지)"
    인과 DAG는 **사용자가 제공**합니다. ontorag는 DAG가 올바르게
    명시되었다는 가정 하에 개입 / 반사실 질의를 계산하며 — 인과 의미를
    검증하거나 인과를 *발견*하지는 않습니다. 구조 학습(`learn-dag`)은
    제안만 출력합니다.

## 평가

```bash
ontorag eval run examples/pokemon/goldset.jsonl \
    --schema examples/pokemon/schema.ttl \
    --data   examples/pokemon/data.ttl

ontorag eval bench  examples/pokemon/goldset.jsonl

# v1.1 — 추론 레이어 goldset (posterior / do / counterfactual / identify)
ontorag eval reasoning examples/smoking/reasoning_goldset.jsonl
```

## stdio MCP 서버

```bash
uv sync --extra mcp
ontorag-mcp                  # MCP 클라이언트가 stdio로 스폰
```

클라이언트 설정 스니펫은 [MCP & 툴](mcp.md) 참조.

## 환경 변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `GRAPH_STORE` | `fuseki` | `fuseki` / `neo4j` / `falkordb` |
| `FUSEKI_URL` | `http://localhost:3030/ontorag` | SPARQL 엔드포인트 |
| `FUSEKI_TIMEOUT` | `60` (초) | HTTP 타임아웃 — `0` = 무제한 |
| `NEO4J_URL` | `bolt://localhost:7687` | Neo4j bolt |
| `NEO4J_QUERY_TIMEOUT` | `30` (초) | 쿼리 단위 — `0` = 무제한 |
| `FALKORDB_URL` | `redis://localhost:6379` | FalkorDB redis |
| `FALKORDB_QUERY_TIMEOUT` | `30` (초) | 쿼리 단위 — `0` = 무제한 |
| `LLM_PROVIDER` / `LLM_MODEL` | — | 설정 오버라이드 |
| `LLM_TIMEOUT` | `60` (초) | LLM HTTP — `0` = 무제한 |
| `EMBEDDING_PROVIDER` | — | 텍스트 임베딩용 `openai` / `ollama` |
| `ONTOLOGY_ACCESS` | unset (개방) | `poke:rw,shop:r,secret:none` 온톨로지별 스코프 잠금 |

`env_timeout()` (`core/config.py`)이 위 값을 파싱합니다 — 숫자/미지정 →
기본값, `0` → 무제한, 잘못된 값 → 기본값 + 경고.
