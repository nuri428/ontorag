# 설치

ontorag는 소스 저장소에서 배포됩니다. Python **3.12+**와
[uv](https://docs.astral.sh/uv/)가 필요합니다 — uv가 프로젝트의 표준 패키지
매니저이지만 `pip install -e .`도 동작합니다.

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
uv sync                      # 코어 (Fuseki 백엔드, 추론 미포함)
```

이 한 줄로 FastAPI 서버, 에이전트 루프, 3개 LLM 프로바이더, Fuseki 어댑터,
CLI가 모두 설치됩니다. 그 외 모든 기능은 **opt-in** — 실제로 필요한 extra만
선택하세요:

## Extras 매트릭스

| Extra | 포함 패키지 | 언제 설치 |
|---|---|---|
| `bayes` | `pgmpy`, `pandas` (+ numpy/scipy) | 베이지안 + 인과 레이어 (`compute_posterior`, `do_query`, `counterfactual`). `ontorag bayes` / `causal` 전부 사용 시. |
| `neo4j` | `neo4j>=5` async 드라이버 | `GRAPH_STORE=neo4j`일 때. Neo4j 컨테이너에 neosemantics (`n10s`) 설치 필수. |
| `falkordb` | `falkordb>=1.0` | `GRAPH_STORE=falkordb`일 때. v0.9 백엔드, RSAL 라이선스. |
| `vector` | `qdrant-client` | Fuseki + `find_similar` / `ontorag embed`. Neo4j는 native vector index, FalkorDB는 native `vecf32()` — 둘 다 extra 불필요. |
| `mcp` | `mcp>=1.0` (공식 SDK) | 독립 stdio MCP 서버 (`ontorag-mcp`). Claude Desktop / Cursor / Claude Code용. |
| `bench` | `langchain`, `ragas`, `chromadb`, `datasets` | LangChain 베이스라인 + `ontorag eval bench --with-ragas`. 선택, 키 필요. |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff` | 기여자 전용. |
| `docs` | `mkdocs-material`, `mkdocs-static-i18n`, `pymdown-extensions` | 본 문서 사이트 빌드 (`mkdocs serve`). |

```bash
# 자유롭게 조합
uv sync --extra bayes --extra neo4j --extra vector
```

## 백엔드

각 백엔드는 docker-compose 서비스로 제공되며 헬스체크가 포함됩니다.

=== "Fuseki (기본)"

    Apache 2.0 · SPARQL 1.1 · ~200 MB 이미지.

    ```bash
    docker compose up -d fuseki
    # 코어에 번들 — extra 불필요
    ```

=== "Neo4j + n10s"

    GPL / AGPL · Cypher · v0.5+.

    ```bash
    docker compose --profile neo4j up -d neo4j
    uv sync --extra neo4j
    export GRAPH_STORE=neo4j
    ```

    참고: `n10s` (neosemantics)는 compose 이미지에 사전 설치되어 있습니다.
    이게 없으면 RDF round-trip이 깨집니다.

=== "FalkorDB"

    **RSAL** (Redis Source Available License — OSI 오픈소스 *아님*) ·
    Cypher · GraphBLAS 가속 · v0.9+.

    ```bash
    docker compose --profile falkordb up -d falkordb
    uv sync --extra falkordb
    export GRAPH_STORE=falkordb
    ```

## LLM 프로바이더

```bash
# Anthropic (기본)
ontorag config set --provider anthropic --api-key sk-ant-...

# OpenAI
ontorag config set --provider openai --api-key sk-... --model gpt-4o

# Ollama (로컬, 키 불필요)
ontorag config set --provider ollama --model llama3.1
```

자격 증명은 프로젝트 루트의 `.env`에 기록됩니다. `ontorag config show`로
비밀번호가 마스킹된 상태로 조회할 수 있습니다.

## 검증

```bash
ontorag status
```

백엔드 헬스, 로드된 트리플 수, 활성 LLM, 결정된 `GRAPH_STORE`를 보고합니다.
누락이 있으면 해당 줄에 `unavailable`과 힌트가 표시됩니다.

## 다음으로

- [빠른 시작](quickstart.md) — 5분 안에 첫 질의.
- [CLI 레퍼런스](cli.md) — 모든 서브커맨드.
- [아키텍처](architecture.md) — 구성 요소들이 어떻게 맞물리는지.
