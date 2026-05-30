# 빠른 시작

설치하고, 예제 온톨로지를 로드하고, LLM 에이전트에 자연어 질문을 던지기까지
**5분 안에**.

## 1. 설치

ontorag는 소스 저장소에서 배포됩니다 (아직 PyPI 배포는 없습니다).

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
uv sync                      # 코어 (Fuseki 백엔드)
```

선택적 extra — 필요한 것만 설치하세요:

```bash
uv sync --extra bayes        # 베이지안 + 인과 추론 (pgmpy)
uv sync --extra neo4j        # Neo4j + n10s 백엔드 드라이버
uv sync --extra falkordb     # FalkorDB 백엔드 클라이언트
uv sync --extra mcp          # 독립 stdio MCP 서버
uv sync --extra vector       # Qdrant 벡터 스토어 (Fuseki find_similar)
uv sync --extra docs         # 본 문서 사이트
```

## 2. 그래프 백엔드 띄우기

=== "Fuseki (기본)"

    ```bash
    docker compose up -d fuseki
    ```

=== "Neo4j + n10s"

    ```bash
    docker compose --profile neo4j up -d neo4j
    export GRAPH_STORE=neo4j
    ```

=== "FalkorDB"

    ```bash
    docker compose --profile falkordb up -d falkordb
    export GRAPH_STORE=falkordb
    ```

## 3. 예제 온톨로지 로드

```bash
ontorag load schema examples/pokemon/schema.ttl    # TBox
ontorag load data   examples/pokemon/data.ttl      # ABox
ontorag status                                     # 로드된 트리플 수 확인
```

## 4. LLM 프로바이더 설정

```bash
# Anthropic (기본)
ontorag config set --provider anthropic --api-key sk-ant-...

# OpenAI
ontorag config set --provider openai --api-key sk-... --model gpt-4o

# Ollama (로컬, 키 불필요)
ontorag config set --provider ollama --model llama3.1
```

## 5. 에이전트에 질문

```bash
ontorag serve                # FastAPI :8000 — http://localhost:8000/ui 접속
# 또는 REPL
ontorag chat
```

시도해보세요:

- *"모든 포켓몬 목록을 알려줘."*
- *"피카츄가 강한 타입은?"*
- *"뮤츠와 관련된 모든 것을 보여줘."*

에이전트가 MCP 툴(`get_schema`, `find_entities`, `traverse_graph` …)을
선택하고, SSE 스트림으로 모든 툴 호출이 보입니다.

## 6. (선택) 확률 + 인과 추론

`--extra bayes`를 설치했다면:

```bash
ontorag bayes load   examples/pokemon/bayes.ttl
ontorag bayes posterior \
    --evidence "OpponentType=Water" \
    --query    "BattleOutcome"

ontorag causal load        examples/smoking/causal.ttl
ontorag causal do          --do "Smoking=yes" --query "Cancer"
ontorag causal counterfactual \
    --observed "Smoking=yes,Cancer=yes" \
    --do       "Smoking=no" \
    --query    "Cancer"
```

이 동작 예제는 교과서적 *see ≠ do* 격차를 재현합니다:
**P(Cancer | see Smoking) = 0.72** vs **P(Cancer | do Smoking) = 0.60**.

## 다음 단계

- [CLI 레퍼런스](cli.md) — 전체 서브커맨드.
- [MCP & 툴](mcp.md) — ontorag를 Claude Desktop / Cursor에 연결.
- [추론 레이어](reasoning.md) — 베이지안 + 인과 레이어 상세.
