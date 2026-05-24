# ontorag

Ontology-aware RAG framework. RDF/OWL ontology as first-class citizen for LLM-based retrieval and reasoning.

## What this is

A framework that lets developers build LLM applications grounded in a domain ontology. Unlike typical RAG (chunks + embeddings), ontorag treats the ontology schema and instance data as the source of truth, and provides ontology-aware tools that an LLM can call via MCP tool use.

Reference implementation: a Korean patent search system (`patent_board`, private) using IPC/CPC/KSIC classifications + claim semantic elements. This framework extracts the generalizable parts.

## Target user

Developers who are researching and evaluating ontology-based LLM application frameworks for real-world use. They understand RDF/OWL/SPARQL and want a production-ready framework to test and compare against alternatives. Not aimed at end-users; DX (developer experience) is the primary quality bar.

## Positioning

- LangChain/LlamaIndex: code-first RAG libraries, ontology not central
- Dify: visual LLM app builder, ontology not supported
- GraphRAG (Microsoft): KG from unstructured text as a property graph — no OWL semantics, no SPARQL reasoning, no transitive inference, no user-defined schema enforced at query time
- **ontorag**: OWL-native — TBox defines the schema, Fuseki enforces OWL reasoning (`rdfs:subClassOf`, `owl:TransitiveProperty`, `owl:inverseOf`), all tools speak SPARQL 1.1; **v0.3 adds LLMs4OL** (text → ontology extension) so the graph grows without manual authoring

One-line: "RAG framework where OWL ontology is the source of truth — and LLMs extend it."

## Version scope

### v0.1 / v0.2 (shipped)
- RDF ontology loader (TTL, JSON-LD, RDF/XML) — TBox + ABox loaded separately or combined
- Graph store: Apache Jena Fuseki with OWL reasoning
- GraphStore Protocol abstraction (Fuseki adapter; Neo4j → v0.5)
- Agentic MCP server: 9 ontology-aware tools (8 L1 intent + 1 L2 JSON DSL)
- FastAPI + SSE streaming — tool calls visible in stream; `rate_limit` event
- LLM providers: Anthropic, OpenAI, Ollama
- Web UI: Schema graph, Data browser, Playground chat (`/ui`)
- CLI: load, config, serve, chat, status
- Docker compose one-command deployment
- Pokémon example ontology

### v0.3 scope — LLMs4OL (Ontology Learning)

**Goal**: LLMs extend an existing OWL ontology from unstructured text — no manual authoring.

Implements the three canonical LLMs4OL tasks (EKAW 2023):

| Task | Input | Output | New triple type |
|------|-------|--------|-----------------|
| **A — Term Typing** | text mention + TBox classes | ranked `(class_uri, confidence)` | `rdf:type` |
| **B — Taxonomy Discovery** | term pair + existing hierarchy | `is_subclass: bool + confidence` | `rdfs:subClassOf` |
| **C — Relation Extraction** | text + entity pair | predicted `predicate_uri + confidence` | `owl:ObjectProperty` assertion |

Pipeline: `text → [A] term typing → [B] taxonomy → [C] relations → proposed RDF triples → auto-load to Fuseki`

New CLI:
```bash
ontorag learn type-term "Pikachu"              # Task A → pk:Pokemon (0.97)
ontorag learn taxonomy --text corpus.txt       # Task B → propose subClassOf
ontorag learn extract --text corpus.txt        # Task C → propose property triples
ontorag learn populate --text corpus.txt       # A+B+C pipeline + auto-load to ABox
```

New MCP tools (L1, exposed):
- `type_term(term, context?)` — map text mention to TBox class
- `extract_triples(text, entities?)` — propose RDF triples from text, validated against schema

Out of scope for v0.3:
- DL-based (transformer embedding) ontology learning — LLM-prompting only
- Fully automated schema evolution (new class proposals without human review)
- BPM, notifications, multi-tenant, vector similarity (v0.5+)

### v0.5 (planned)
- Neo4j + n10s adapter; `GRAPH_STORE=fuseki|neo4j` env var
- Vector similarity tool `find_similar` (Neo4j vector index or Qdrant)
- Multi-ontology per instance

## Architecture

```
사용자 (브라우저 / CLI)
         │
         ▼ POST /chat  (SSE 스트림 응답)
┌────────────────────────────────────────┐
│            FastAPI Server              │
│                                        │
│  /chat ──▶ Agent Loop                  │
│                 │                      │
│                 ▼ tool_use (MCP)       │
│          LLM (Claude/GPT/Ollama)       │
│                 │                      │
│   ┌─────────────────────────────────┐  │
│   L1 (intent tools, 8개):           │  │
│    get_schema     find_entities     │  │
│    describe_entity  count_entities  │  │
│    aggregate      traverse_graph    │  │
│    find_path      find_related      │  │
│   L2 (JSON DSL):  query_pattern     │  │
│   L3 (dev only):  query_sparql_raw  │  │
│   └─────────────┬───────────────────┘  │
└─────────────────┼──────────────────────┘
                  │ SPARQL (HTTP)
                  ▼
       Apache Jena Fuseki  ← Phase 1
       Neo4j + n10s        ← Phase 1.5
```

SSE stream events visible to client:
```
data: {"type": "thinking",     "content": "스키마를 확인합니다..."}
data: {"type": "tool_call",    "tool": "get_schema"}
data: {"type": "tool_result",  "content": {"classes": [...]}}
data: {"type": "text",         "content": "Person 클래스는..."}
data: {"type": "done"}
```

## Tools the LLM can call (MCP)

Ontology-aware tools exposed via MCP, embedded in FastAPI process. Each tool returns structured JSON — no unstructured text blobs.

툴은 `src/ontorag/api/routes/tools/` 아래 FastAPI 라우트로 구현되며, `fastapi-mcp`가 자동으로 MCP 툴로 변환합니다. 라우트 `operation_id`가 MCP 툴 이름이 됩니다.

### 3-레이어 설계 (docs/design/sparql-approach.md 기반)

**Layer 1 — 의도 기반 고수준 툴 (MCP 노출, 90% 사용 케이스)**

| operation_id | 엔드포인트 | 설명 |
|---|---|---|
| `get_schema` | GET /tools/schema | 클래스·속성·계층 구조 (compact, ~30 tokens/class) |
| `get_class_detail` | GET /tools/schema/class?class_uri=... | 특정 클래스 상세 (속성·부모·자식·인스턴스 샘플) |
| `find_entities` | POST /tools/entities/find | 클래스 + 필터로 인스턴스 탐색 (inference 포함) |
| `describe_entity` | GET /tools/entities/{uri} | 엔티티 속성·관계 전체 (inverseOf 포함) |
| `count_entities` | POST /tools/entities/count | 인스턴스 수 집계 |
| `aggregate` | POST /tools/entities/aggregate | group_by + agg 함수 |
| `traverse_graph` | POST /tools/traverse | 그래프 순회 (TransitiveProperty 포함) |
| `find_path` | POST /tools/path | 두 엔티티 간 최단 경로 |
| `find_related` | POST /tools/related | 두 클래스 간 멀티홉 조인 |

**Layer 2 — JSON DSL escape hatch (MCP 노출, 10% 복잡한 케이스)**

| operation_id | 엔드포인트 | 설명 |
|---|---|---|
| `query_pattern` | POST /tools/query/pattern | JSON triple patterns → 내부에서 SPARQL 번역, injection 불가 |

**Layer 3 — raw SPARQL (MCP 비노출, 개발자 전용)**

| operation_id | 엔드포인트 | 설명 |
|---|---|---|
| `query_sparql_raw` | POST /tools/query/sparql | `exclude_operations`으로 MCP에서 제외, curl 디버그용 |

Inference 레이어: Fuseki 데이터셋을 `ja:OntModelSpec` 추론 모델로 구성하면 `find_entities(Animal)`이 rdfs:subClassOf를 통해 Dog/Cat 인스턴스를 자동 포함 — 툴 코드 변경 없음.

## CLI design

```bash
# RDF 로드 (진행률 표시 — rich 라이브러리)
ontorag load schema ./ontology.ttl     # TBox (클래스/속성 정의)
ontorag load data   ./instances.ttl   # ABox (인스턴스 데이터)
ontorag load        ./combined.ttl    # 자동 감지

# LLM 설정 (.env 또는 커맨드)
ontorag config set --provider anthropic --api-key sk-ant-...
ontorag config set --provider openai --model gpt-4o
ontorag config show

# 서버 실행
ontorag serve [--host 0.0.0.0] [--port 8000]

# 채팅 REPL
ontorag chat

# 상태 확인
ontorag status   # 그래프 스토어 연결 + 로드된 트리플 수 + LLM 설정
```

Load progress example:
```
⠴ Loading triples... [████████████░░░░░] 62% | 3,891 / 6,284 triples
```

## GraphStore abstraction

All MCP tools depend on this Protocol, not a concrete store. Swapping Fuseki → Neo4j requires only a new adapter.

```python
class GraphStore(Protocol):
    # Loading
    async def load_rdf(self, path: str, mode: Literal["schema", "data", "auto"]) -> LoadResult: ...

    # Layer 1 — intent-based tools (MCP exposed)
    async def get_schema(self) -> SchemaResult: ...                                    # compact, ~30 tokens/class
    async def get_class_detail(self, class_uri: str) -> ClassDetail: ...               # drill-down per class
    async def find_entities(self, class_uri: str, filters: list[EntityFilter] | None, limit: int) -> list[EntityResult]: ...
    async def describe_entity(self, uri: str, predicates: list[str] | None = None) -> EntityResult: ...
    async def count_entities(self, class_uri: str, filters: list[EntityFilter] | None) -> int: ...
    async def aggregate(self, class_uri: str, group_by: str, agg: AggFunc) -> list[AggregateResult]: ...
    async def traverse(self, start_uri: str, predicate: str | None, max_depth: int, direction: TraversalDirection) -> TraversalResult: ...
    async def find_path(self, uri_a: str, uri_b: str, max_depth: int) -> TraversalResult: ...
    async def find_related(self, class_uri_a: str, predicate: str, class_uri_b: str, filters_a: list[EntityFilter] | None, filters_b: list[EntityFilter] | None, limit: int) -> list[dict]: ...

    # Layer 2 — JSON DSL (MCP exposed)
    async def query_pattern(self, query: PatternQuery) -> QueryResult: ...

    # Layer 3 — raw SPARQL (internal only, NOT exposed via MCP)
    async def _sparql_select(self, sparql: str) -> dict: ...

    # Status
    async def status(self) -> StoreStatus: ...
```

## v0.3 LearnerProtocol

```python
from dataclasses import dataclass, field
from typing import Protocol

@dataclass
class TermTypingResult:
    term: str
    class_uri: str          # best matching TBox class
    label: str
    confidence: float       # 0.0–1.0
    reasoning: str | None = None

@dataclass
class TaxonomyRelation:
    child_term: str
    parent_uri: str         # existing TBox class URI
    confidence: float

@dataclass
class ExtractedTriple:
    subject_label: str
    subject_uri: str | None   # None → new entity to be minted
    predicate_uri: str        # must exist in TBox
    object_uri: str | None    # for object properties
    object_value: str | None  # for data properties
    confidence: float

@dataclass
class PopulationResult:
    term_typings: list[TermTypingResult] = field(default_factory=list)
    taxonomy_proposals: list[TaxonomyRelation] = field(default_factory=list)
    triples: list[ExtractedTriple] = field(default_factory=list)
    triples_loaded: int | None = None   # set after auto-load

class OntologyLearner(Protocol):
    """LLMs4OL pipeline — all tasks backed by LLM prompting against current TBox."""

    async def type_term(
        self,
        term: str,
        context: str | None = None,
        top_k: int = 3,
    ) -> list[TermTypingResult]:
        """Task A: rank TBox classes for a text mention."""
        ...

    async def discover_taxonomy(
        self,
        text: str,
        candidate_classes: list[str] | None = None,
    ) -> list[TaxonomyRelation]:
        """Task B: propose rdfs:subClassOf from text evidence."""
        ...

    async def extract_relations(
        self,
        text: str,
        entities: list[str] | None = None,
        min_confidence: float = 0.7,
    ) -> list[ExtractedTriple]:
        """Task C: propose object/data property triples from text."""
        ...

    async def populate_from_text(
        self,
        text: str,
        auto_load: bool = False,
        min_confidence: float = 0.7,
    ) -> PopulationResult:
        """Run A+B+C in sequence; optionally load accepted triples to Fuseki."""
        ...
```

Design constraints:
- All methods receive the current TBox (SchemaResult) at call time — no stale schema cache
- `predicate_uri` and `class_uri` in outputs must exist in the current TBox (validated before return)
- Confidence threshold `min_confidence` filters low-quality proposals; default 0.7
- `auto_load=True` calls `store.load_rdf(...)` with mode="data" after validation

## Tech stack

- Language: Python 3.12
- Package manager: uv (preferred)
- Web framework: FastAPI
- MCP: `fastapi-mcp>=0.4.0` — FastAPI 라우트를 MCP 툴로 자동 변환, ASGI transport (HTTP 오버헤드 없음), `/mcp` 엔드포인트 자동 생성
- Graph store (Phase 1): Apache Jena Fuseki (SPARQL 1.1 compliant, Docker image ~200MB)
- Graph store (Phase 1.5): Neo4j + n10s (Cypher natively; SPARQL via n10s endpoint)
- LLM SDKs: anthropic (Phase 1); openai, ollama (Phase 1.5)
- CLI: Typer + Rich (progress bars, status display)
- Deployment: Docker + docker-compose
- Tests: pytest

## Repo layout

```
ontorag/
├── .claude/CLAUDE.md          # this file
├── README.md
├── pyproject.toml
├── docker-compose.yml         # dev
├── docker-compose.prod.yml    # production overlay
├── .env.example
├── .dockerignore
├── docker/
│   └── api/Dockerfile
├── src/ontorag/
│   ├── __init__.py
│   ├── cli.py                 # `ontorag` command entry (Typer)
│   ├── api/                   # FastAPI app
│   │   ├── main.py            # FastAPI app + fastapi-mcp mount
│   │   └── routes/
│   │       ├── health.py      # GET  /health
│   │       ├── status.py      # GET  /status
│   │       ├── load.py        # POST /load
│   │       ├── chat.py        # POST /chat — SSE streaming
│   │       └── tools/         # MCP 툴 라우트 (fastapi-mcp → /mcp 자동 노출)
│   │           ├── schema.py      # L1: GET /tools/schema + GET /tools/schema/class
│   │           ├── entities.py    # L1: find/count/aggregate + GET /tools/entities/{uri}
│   │           ├── traversal.py   # L1: traverse + path + related
│   │           ├── pattern.py     # L2: POST /tools/query/pattern
│   │           ├── _query.py      # L3: POST /tools/query/sparql (MCP exclude)
│   │           └── learning.py    # v0.3 L1: type_term, extract_triples (MCP exposed)
│   ├── core/
│   │   ├── loader.py          # RDF parsing & loading (with progress callback)
│   │   └── sparql.py          # PatternQuery DSL → SPARQL translator
│   ├── stores/
│   │   ├── base.py            # GraphStore Protocol + result types
│   │   ├── fuseki.py          # v0.1 default (SPARQL over HTTP)
│   │   └── neo4j.py           # v0.5 (Cypher + n10s SPARQL endpoint)
│   ├── llm/
│   │   ├── base.py            # LLMProvider abstract base
│   │   ├── anthropic.py
│   │   ├── openai.py
│   │   └── ollama.py
│   ├── chat/
│   │   └── agent.py           # Agentic MCP loop (LLM + tool calls + SSE emit)
│   └── learn/                 # v0.3 — LLMs4OL ontology learning
│       ├── __init__.py
│       ├── base.py            # OntologyLearner Protocol + result types
│       ├── term_typing.py     # Task A: term → TBox class
│       ├── taxonomy.py        # Task B: rdfs:subClassOf discovery
│       ├── relation.py        # Task C: object/data property extraction
│       └── pipeline.py        # A+B+C orchestration + auto-load
├── examples/
│   └── foaf/                  # FOAF ontology schema + sample instance data
└── tests/
```

## Coding conventions

- Style: defensive programming, DRY, guard clauses, explicit type hints
- Type checking: full type hints, `from __future__ import annotations` on all modules
- Null safety: explicit None checks, no silent fallbacks
- Error handling: raise specific exceptions, don't swallow
- Docstrings: Google style, on all public functions
- Tests: pytest, one test file per module, fixture-driven
- Imports: stdlib → third-party → local, sorted within group
- Async: prefer async for all I/O (LLM calls, graph store HTTP, FastAPI routes)
- No print statements; use logging
- Keep modules under 300 lines; split when growing

## Design principles

- **Ontology is the source of truth.** Tools surface ontology structure, not bury it.
- **Agentic MCP.** LLM is an agent that calls MCP tools; tools are the interface to the graph store.
- **SSE transparency.** Tool calls and results are visible in the SSE stream — no black box.
- **GraphStore Protocol.** All tools target the abstract interface; stores are swappable adapters.
- **Minimal dependencies.** No LangChain, no LlamaIndex, no LangServe. Direct SDK calls.
- **Structured tool outputs.** JSON in, JSON out. LLM gets structured data, not chunks.
- **Fast cold start.** `docker compose up` to ready API in under 60 seconds.
- **Explicit over implicit.** Configuration via .env and CLI flags, not magic.

## Docker compose design

```bash
docker compose up                   # Fuseki + API
docker compose --profile ui up      # + Web UI (Phase 2)
```

Production overlay:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Healthchecks mandatory on all services. `depends_on` uses `condition: service_healthy`.

Fuseki healthcheck: `GET /$/ping` → 200 OK.

## Milestone plan

### v0.1 / v0.2 ✅ shipped
- Fuseki + 9 MCP tools + SSE agent loop
- Web UI (Schema/Data/Playground)
- Anthropic, OpenAI, Ollama providers
- Rate-limit UX + forced tool-use when ontology has data

### v0.3 — LLMs4OL (current focus)

**Goal**: `ontorag learn populate --text corpus.txt` extracts RDF triples and loads them to Fuseki.

| Step | Deliverable |
|------|-------------|
| 1 | `learn/base.py` — OntologyLearner Protocol + all result dataclasses |
| 2 | `learn/term_typing.py` — Task A, LLM prompt chain: schema → candidate ranking |
| 3 | `learn/taxonomy.py` — Task B, pairwise subclass scoring |
| 4 | `learn/relation.py` — Task C, relation extraction with schema-validated predicates |
| 5 | `learn/pipeline.py` — A+B+C orchestration, confidence filtering, RDF serialisation |
| 6 | `api/routes/tools/learning.py` — `type_term` + `extract_triples` MCP tools |
| 7 | CLI `ontorag learn` command group |
| 8 | Example: extend Pokémon ontology from English Wikipedia text |

**Quality bar**: Task A top-1 accuracy ≥ 70% on Pokémon example; output RDF parses cleanly.

### v0.5 (planned)
- Neo4j + n10s adapter; `GRAPH_STORE` env var
- Vector similarity tool `find_similar`
- Multi-ontology per instance

## What NOT to do (anti-patterns)

- Don't pull in LangChain, LlamaIndex, or LangServe. Each tool is small; write it directly.
- Don't skip the GraphStore abstraction. Even with only Fuseki, define the Protocol first — Neo4j comes in v0.5.
- Don't expose raw SPARQL to the LLM. MCP에는 L1 툴 + L2 `query_pattern`만 노출하고, raw SPARQL(L3 `query_sparql_raw`)은 개발자 디버그 전용으로 격리.
- Don't add features from `patent_board` directly. Domain-specific code stays out.
- Don't add BPM, notifications, or multi-tenant — separate repo.
- Don't include KIPRIS/IPC/CPC code or data. License risk and scope creep.
- Don't optimize prematurely. Get it working first; profile second.
- v0.3 LLMs4OL: Don't propose new TBox classes automatically — only ABox triples using existing schema. TBox evolution requires human review.
- v0.3 LLMs4OL: Don't output `predicate_uri` or `class_uri` that don't exist in the current TBox — validate against SchemaResult before returning.

## Open questions (decide when reached)

- L2 `query_pattern` DSL 검증 전략: predicate/class URI를 TBox 화이트리스트로 체크 + `limit`/`max_depth` 상한. Day 4에 결정. (Raw SPARQL은 L3로 LLM 비노출이므로 인젝션 우려 없음.)
- Neo4j SPARQL via n10s endpoint vs. native Cypher translation: evaluate at Phase 1.5.
- Vector similarity tool (Phase 2): Neo4j vector index vs. Qdrant? 별도 L1 툴 `find_similar`로 추가할지, `query_pattern`에 vector filter로 통합할지 결정 필요.
- Multi-ontology per instance: not in v0.1. Single ontology assumption.
- Auth/multi-tenant: not in v0.1. Single-user assumption.

## How to work with Claude Code on this repo

When starting a session, Claude Code should:
1. Read this CLAUDE.md
2. Check current state with `git status` and `git log --oneline -10`
3. Confirm which milestone item is the current focus
4. Propose specific files to touch before writing code

When proposing changes:
- Match the repo layout above
- Honor GraphStore Protocol — tools never import a concrete store directly
- Add or update tests in the same change
- Keep changes scoped to one concern per commit

When unsure about scope:
- Default to smaller. Phase 1 is small on purpose.
- If something feels like Phase 2, flag it and skip.

## License

MIT (planned). No proprietary or domain-specific code from patent_board.
