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
- **ontorag**: OWL-native — TBox defines the schema, Fuseki enforces OWL reasoning (`rdfs:subClassOf`, `owl:TransitiveProperty`, `owl:inverseOf`), all tools speak SPARQL 1.1; **v0.3 adds LLMs4OL** (text → ontology extension) so the graph grows without manual authoring; **v0.7+ adds probabilistic + causal inference** (Bayesian / Pearl Rung 2-3) so the ontology becomes a reasoning substrate, not just a graph

One-line: "OWL-native reasoning framework — ontology as source of truth, extended by LLMs and queried under uncertainty by LLM agents via MCP."

## 4-layer reasoning stack (north star)

ontorag's long-term architecture is a 4-layer reasoning stack, accumulated one layer per major release:

```
Layer 4 — Learning              ← v1.0+ (GNN: R-GCN link prediction, neural CPT, structure learning)
Layer 3 — Counterfactual        ← v0.8 (Pearl Rung 2+3: do-calculus, counterfactuals)
Layer 2 — Probabilistic         ← v0.7 (Bayesian network inference: posterior, MPE)
Layer 1 — Logical (RDFS+)       ← shipped (subClassOf*, inverseOf, Transitive)
Layer 0 — Storage               ← shipped (Fuseki / Neo4j; FalkorDB → v0.9)
```

Each layer answers a different *kind* of question:
- Logical: "Is X necessarily true?"
- Probabilistic: "How likely is X?"
- Counterfactual: "What if we intervened on Y? What if Y had been different?"
- Learning: "What patterns does the graph itself reveal?"

This stack is independent of (and complementary to) Palantir's Semantic/Kinetic/Dynamic frame — Layers 2-4 collectively activate Palantir's Dynamic layer. Kinetic (actions/workflows) is intentionally out of scope for ontorag and lives in a separate BPM project; ontorag exposes capability via MCP so external Kinetic engines can compose.

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

### v0.5 — Neo4j backend (shipped)
- **Neo4j + neosemantics (n10s) adapter** behind `GRAPH_STORE=fuseki|neo4j` (default fuseki). `create_store()` factory selects the backend; all tools/routes/CLI depend on the `GraphStore` protocol only.
- n10s import: `handleVocabUris=SHORTEN` + `handleRDFTypes=LABELS_AND_NODES`. URIs round-trip through a shorten/expand layer with prefixes pinned from the loaded TTL.
- **subClassOf inference is implemented natively on Neo4j** (Cypher `[:rdfs__subClassOf*0..N]`), so `find_entities(Animal)` includes Dog/Cat instances. ⚠️ This *diverges* from the current Fuseki deployment, which runs `--mem` with inference OFF (plain type-match). See Open questions.
- L2 `query_pattern` translated via `core/cypher.py` (`pattern_to_cypher`), symmetric to the SPARQL translator. All Cypher rel-types/labels routed through `_safe_rel()` (injection-safe).
- Design note: `docs/design/neo4j-n10s.md`. Verified live against `neo4j:5.26` + n10s 5.26.
- **BM25 full-text search** (`search_text`): **both backends** — Neo4j fulltext index / Fuseki jena-text (Lucene). `docs/design/neo4j-bm25.md`.
- **Graph embeddings** (`find_similar` + `ontorag embed`): **both backends** — structural + textual (`EmbeddingProvider`: OpenAI/Ollama via `EMBEDDING_PROVIDER`) + `hybrid` (RRF), explicit `ontorag embed` trigger. Neo4j: GDS FastRP + native vector index. Fuseki: `core/fastrp.py` + EmbeddingProvider → **Qdrant**. `docs/design/neo4j-embedding.md`, `docs/design/fuseki-parity.md`.
- **Reasoning, full-text, and vector similarity have full backend parity** (Fuseki ⇄ Neo4j); each uses its native tech.
- **Multi-ontology per instance** (shipped): one instance hosts many ontologies; every read tool + `load` + `embed`/`find_similar` takes an optional `ontology` scope (`None` = union/all, backward-compatible). Fuseki = per-ontology named graphs (`urn:ontorag:{id}:schema/data`); Neo4j = node `_ontology` list tag. Embeddings are scoped too — Qdrant points carry an `ontology` payload (un-tagged, not deleted, when shared across ontologies); Neo4j post-filters kNN by `_ontology`. `docs/design/multi-ontology.md`.

### v0.6.1 (shipped)
- **Per-ontology access control** — config-driven read/write/none via `ONTOLOGY_ACCESS` env (`core/access.py` + `stores/access_wrapper.py`, factory-wired). Scope-lock at the GraphStore boundary; unset = fully open (backward-compatible). Write methods (load/clear) fully guarded; capability reads (search/similar/aligned) pass through (v0.7 follow-up).
- **Cross-ontology entity alignment** — `owl:sameAs` transitive+symmetric closure via `sameas_closure` (both backends) → `find_aligned` tool/route.
- **`load_rdf` pre-parsed-graph fast path** — optional `graph=` kwarg avoids the directory loader's double-parse.

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

**Backend-capability tools (MCP 노출) — v0.5부터 양 백엔드 모두 지원**

라우트가 `getattr`로 지원 여부를 확인(미지원 백엔드는 501)하지만, v0.5에서 Fuseki·Neo4j 둘 다 구현하므로 실질적으로 공통 제공. 백엔드마다 다른 기술을 씀:

| operation_id | 엔드포인트 | Fuseki | Neo4j | 설명 |
|---|---|---|---|---|
| `search_text` | POST /tools/search/text | jena-text (Lucene) | fulltext 인덱스 | BM25 풀텍스트 → ranked `SearchHit`. `class_uri` 주면 subClassOf 포함. |
| `find_similar` | POST /tools/similar | FastRP(`core/fastrp.py`)+EmbeddingProvider → **Qdrant** | GDS FastRP+EmbeddingProvider → native vector index | 그래프 임베딩 kNN. `mode=structural\|textual\|hybrid`(RRF) → `SimilarHit`. `class_uri`로 subClassOf-aware 제한. `ontorag embed`로 사전 생성. |
| `find_aligned` | POST /tools/aligned | `(owl:sameAs\|^owl:sameAs)+` 프로퍼티 패스 | `[:owl__sameAs*1..]` 무방향 | owl:sameAs 전이+대칭 폐포 → 교차-온톨로지 동치 엔티티 `list[{uri,label}]`. v0.6.1. |

추론(subClassOf): **양 백엔드 모두 구현** — Neo4j는 Cypher `[:rdfs__subClassOf*]`, Fuseki는 쿼리 레벨 `?inst a/rdfs:subClassOf*`(SCHEMA·DATA named graph 조인, config 변경 없음). `find_entities(Animal)`이 양쪽에서 Dog/Cat 인스턴스를 포함.

## CLI design

```bash
# RDF 로드 (진행률 표시 — rich 라이브러리)
ontorag load schema ./ontology.ttl     # TBox (클래스/속성 정의)
ontorag load data   ./instances.ttl   # ABox (인스턴스 데이터)
ontorag load        ./combined.ttl    # 자동 감지 (파일)
ontorag load        ./ontologies/     # 디렉토리 — 서브디렉토리명=ontology id, schema→data 순서 보장
                                       #   (--ontology 플랫병합 · --replace · --no-recursive)
                                       #   core/batch_loader.py 오케스트레이션 (GraphStore Protocol 불변)

# LLM 설정 (.env 또는 커맨드)
ontorag config set --provider anthropic --api-key sk-ant-...
ontorag config set --provider openai --model gpt-4o
ontorag config show

# 서버 실행
ontorag serve [--host 0.0.0.0] [--port 8000]

# 채팅 REPL
ontorag chat

# 그래프 임베딩 생성 (Neo4j 전용 — v0.5)
ontorag embed --mode both          # structural(GDS FastRP) + textual(EmbeddingProvider)
ontorag embed --mode structural    # 구조만 (외부 API 불필요)
ontorag embed --mode textual       # 텍스트만 (EMBEDDING_PROVIDER 필요)

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
│   │           ├── _sparql.py     # L3: POST /tools/query/sparql (Fuseki-only, getattr 501)
│   │           ├── learning.py    # v0.3 L1: type_term, extract_triples (MCP exposed)
│   │           ├── search.py      # v0.5 POST /tools/search/text (BM25 — both backends)
│   │           ├── similar.py     # v0.5 POST /tools/similar (find_similar — both backends)
│   │           └── bayes.py       # v0.7.3 POST /tools/bayes/posterior + /mpe (both backends)
│   ├── core/
│   │   ├── loader.py          # RDF parsing & loading (with progress callback)
│   │   ├── sparql.py          # PatternQuery DSL → SPARQL translator (Fuseki) + uri_ref
│   │   ├── cypher.py          # PatternQuery DSL → Cypher translator (Neo4j) + _safe_rel
│   │   ├── ontology.py        # named-graph URIs + OntologyLayer (v0.7.0)
│   │   ├── bayes.py           # v0.7.1 bn: vocab + BN/StructureSpec models + RDF round-trip
│   │   └── fastrp.py          # v0.5 pure-Python FastRP structural embeddings (Fuseki)
│   ├── stores/
│   │   ├── base.py            # GraphStore + BayesianStore Protocols + result types
│   │   ├── factory.py         # create_store() — GRAPH_STORE=fuseki|neo4j
│   │   ├── fuseki.py          # v0.1 default (SPARQL over HTTP)
│   │   ├── neo4j.py           # v0.5 (Neo4j + n10s, async driver)
│   │   ├── _neo4j_schema_mixin.py    # get_schema / get_class_detail
│   │   ├── _neo4j_entity_mixin.py    # find/describe/count/aggregate
│   │   ├── _neo4j_traversal_mixin.py # traverse/path/closure/related
│   │   ├── _neo4j_export.py          # dump_graph TTL/XLSX serialisation
│   │   ├── _neo4j_values.py          # n10s ARRAY-multival unpack helpers
│   │   ├── _neo4j_search_mixin.py    # v0.5 BM25 full-text (search_text)
│   │   ├── _neo4j_embedding_mixin.py # v0.5 GDS FastRP + textual embeddings (build_embeddings, find_similar)
│   │   ├── _fuseki_search_mixin.py   # v0.5 jena-text full-text (search_text)
│   │   ├── _fuseki_embedding_mixin.py# v0.5 FastRP + textual → Qdrant (build_embeddings, find_similar)
│   │   ├── _fuseki_bayes_mixin.py     # v0.7.1 BayesianStore via GSP on urn:ontorag:probabilistic
│   │   ├── _neo4j_bayes_mixin.py      # v0.7.2 BayesianStore via :_Bayes* nodes (_scope tag)
│   │   └── _qdrant.py                # v0.5 async Qdrant wrapper (Fuseki vector store)
│   ├── llm/
│   │   ├── base.py            # LLMProvider abstract base
│   │   ├── anthropic.py
│   │   ├── openai.py
│   │   ├── ollama.py
│   │   └── embedding.py       # v0.5 EmbeddingProvider (OpenAI/Ollama) for textual embeddings
│   ├── chat/
│   │   └── agent.py           # Agentic MCP loop (LLM + tool calls + SSE emit)
│   ├── learn/                 # v0.3 — LLMs4OL ontology learning
│   │   ├── __init__.py
│   │   ├── base.py            # OntologyLearner Protocol + result types
│   │   ├── term_typing.py     # Task A: term → TBox class
│   │   ├── taxonomy.py        # Task B: rdfs:subClassOf discovery
│   │   ├── relation.py        # Task C: object/data property extraction
│   │   └── pipeline.py        # A+B+C orchestration + auto-load
│   ├── bayes/                 # v0.7 — probabilistic layer (pgmpy, [bayes] extra)
│   │   ├── engine.py          # v0.7.3 BayesianEngine: compute_posterior / mpe
│   │   └── learn.py           # v0.7.4 CPT learning from ABox data
│   └── cli_bayes.py           # v0.7.4 `ontorag bayes` group (load/show/posterior/mpe/clear/learn-cpt)
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

### v0.3 — LLMs4OL ✅ shipped
- `learn/` module: term typing (Task A), taxonomy discovery (Task B), relation extraction (Task C), A+B+C pipeline with auto-load
- MCP tools: `type_term`, `extract_triples`
- CLI: `ontorag learn type-term/taxonomy/extract/populate`
- Validation: predicate_uri/class_uri must exist in current TBox

### v0.4 — RAGAS eval harness + prompt externalization + SHACL gate ✅ shipped
- v0.4.0: 4-domain RAGAS eval, evaluation harness
- v0.4.1: prompt externalization, SHACL validation gate
- v0.4.2: PyPI publish readiness, positioning table in README

### v0.5 — Neo4j backend + capability parity ✅ shipped
- Neo4j + n10s adapter behind `GRAPH_STORE` env var (factory-selected); full GraphStore protocol in Cypher with native subClassOf inference; `pattern_to_cypher` for L2; live-tested against neo4j:5.26.
- `docker compose --profile neo4j up neo4j`; `[neo4j]` extra for the driver.
- BM25 full-text (`search_text`) + vector similarity (`find_similar`/`ontorag embed`) — both backends.
- Multi-ontology per instance (`ontology` scope on all read tools + `load`).

### v0.6 / v0.6.1 ✅ shipped
- v0.6.0: agent tools, directory loader, backend config
- v0.6.1: config-driven per-ontology access control + cross-ontology entity alignment (`owl:sameAs` → `find_aligned`); `load_rdf` pre-parsed-graph fast path.

---

### v0.7 — Probabilistic Foundation (Bayesian) ✅ shipped

**Goal**: ontorag becomes a probabilistic reasoning system — LLM agents can call `compute_posterior(evidence, query)` and `mpe(evidence)` against a BN layered over the OWL graph. Activates Palantir's Dynamic layer.

**Decomposition** (target ~12 weeks total):

| Sub-version | Deliverable |
|---|---|
| **v0.7.0** ✅ shipped | **Named Graph foundation** (extracts Phase 1 of paused `layered-ontology-plan.md`): `OntologyLayer` enum (`semantic`/`policy`/`state`/`provenance`) + `LAYER_GRAPH_URI` + `layer_graph_uri()` in `core/ontology.py` (re-exported from `stores/base.py`); opt-in multi-graph inference assembler `docker/fuseki/config-inference.ttl.template` (OWLMicro reasoner over `urn:x-arq:UnionGraph`, selected via `FUSEKI_CONFIG_TEMPLATE`); `schema/data` → `semantic/state` **vocabulary** rename. **Key decision**: physical graph URIs are kept (`semantic`→`urn:ontorag:schema`, `state`→`urn:ontorag:data`) — only the layer *names* changed, so persisted TDB2 data + tests are untouched; `"schema"`/`"data"` stay accepted as aliases (`resolve_layer`). `policy`/`provenance` are reserved vocabulary (no read/write path until deferred Phases 2/4). Design: `docs/design/named-graph-layers.md`. *Note: layered-plan's "Dynamic" is renamed to "State" to avoid collision with Palantir Dynamic (= reasoning capability).* |
| **v0.7.1** ✅ shipped | `bn:` mini-vocabulary + spec models + RDF round-trip (`core/bayes.py`) + `BayesianStore` Protocol (`stores/base.py`) + Fuseki CPT mixin (`_fuseki_bayes_mixin.py`); stores CPTs in `urn:ontorag:probabilistic` named graph **only**. `probabilistic_graph_uri()` is deliberately NOT an `OntologyLayer` member (reasoning-stack storage ≠ document layer). |
| **v0.7.2** ✅ shipped | Neo4j CPT mixin (`_neo4j_bayes_mixin.py`) — `:_BayesVariable`/`:_BayesCPD` nodes tagged `_scope`; full backend parity (identical `BayesNetwork` returned). |
| **v0.7.3** ✅ shipped | `BayesianEngine` (pgmpy wrapper, `bayes/engine.py`, lazy import) + MCP tools `compute_posterior`, `mpe` (`api/routes/tools/bayes.py`). pgmpy is the `[bayes]` optional extra. Quality bar: hand-computed Pokémon posteriors in `tests/test_bayes_engine.py`. |
| **v0.7.4** ✅ shipped | CPT learning from data — `ontorag bayes` CLI (load/show/posterior/mpe/clear/learn-cpt; `cli_bayes.py`) + `bayes/learn.py`; `bn:dependsOn` structure specs; ties v0.3 LLMs4OL output to BN parameter estimation. Design: `docs/design/bayesian-layer.md`. |

**Quality bar**: synthetic Pokémon BN (type matchup → battle outcome) verified against hand-computed posteriors; both backends return identical results.

**Library choice**: pgmpy (Python-native, MIT, async-friendly via `asyncio.to_thread`). OpenMarkov rejected (Java GUI focus, no fit). pyAgrum as fallback for scale.

### v0.8 — Causal Layer (Pearl Rung 2 + 3) ✅ shipped

**Goal**: `do_query(intervention, query)`, `identify_effect(treatment, outcome)`, and `counterfactual(observed, intervention, query)` MCP tools — interventional and counterfactual reasoning over the BN. Activates Pearl Rung 2-3.

**Decomposition**:

| Sub-version | Deliverable |
|---|---|
| **v0.8.0** ✅ shipped | `causal:` vocabulary (`core/causal.py`: `CausalModel`/`CausalVariable`, `causal:influences`/`causal:observed`/`causal:basedOn`, acyclicity check) + RDF round-trip + `CausalStore` Protocol (`stores/base.py`); DAG stored in `urn:ontorag:causal` named graph **only**. Fuseki mixin (`_fuseki_causal_mixin.py`, GSP) + Neo4j mixin (`_neo4j_causal_mixin.py`, `:_CausalVariable` nodes + `[:_CAUSES]` edges tagged `_scope`) — full backend parity. |
| **v0.8.1** ✅ shipped | Pearl Rung 2 — `CausalEngine.do_query` (`causal/engine.py`) via pgmpy `CausalInference.query(do=…)` (graph surgery + automatic back-door adjustment) + `identify` (`get_minimal_adjustment_set` / `get_all_frontdoor_adjustment_sets`) + MCP tools `do_query`, `identify_effect` (`api/routes/tools/causal.py`). |
| **v0.8.2** ✅ shipped | Pearl Rung 3 — `counterfactual` MCP tool + `CausalEngine.counterfactual` via abduction-action-prediction over the **canonical independent-noise SCM** consistent with the CPTs (response-function enumeration, `_CF_RESPONSE_CAP`). |
| **v0.8.3** ✅ shipped | Structure learning — `causal/discovery.py` PC algorithm (pgmpy `PC` estimator, reuses `bayes/learn.gather_observations`) → **proposal-only** `CausalModel`; `ontorag causal learn-dag` CLI (`--save` still prints the review warning). Never auto-committed. |

**CLI** (`cli_causal.py`): `ontorag causal load/show/do/identify/counterfactual/clear/learn-dag`.

**Quality bar**: synthetic smoking BN with an **observed genotype confounder** (`examples/smoking/`: Genotype→Smoking, Genotype→Cancer, Smoking→Cancer). Hand-verified: P(Cancer | **see** Smoking=yes) = 0.72 vs P(Cancer | **do** Smoking=yes) = 0.60 (back-door adjusted over Genotype) — `do` ≠ `see`. Counterfactual consistency axiom verified in `tests/test_causal_engine.py`; PC recovers the chain skeleton in `tests/test_causal_discovery.py`. Both backends return identical results.

**Library choice**: **pgmpy-native** (not DoWhy). We already have a fully-specified BN (DAG + CPTs from the v0.7 layer), so `do` is graph surgery + back-door adjustment via pgmpy's `CausalInference`, and counterfactuals come from a canonical-SCM enumeration over the CPTs. DoWhy was rejected — its identification+estimation pipeline targets *raw data* and would add a heavy dependency for capability we get directly from the quantified BN. pgmpy stays the single probabilistic/causal engine (`[bayes]` extra).

**Over-claim guard** (shipped in README + every tool/CLI docstring): *"The causal DAG is user-supplied. ontorag computes interventional / counterfactual queries assuming the DAG is correctly specified; it does not validate causal semantics or discover causation."* Structure discovery (`learn-dag`) emits proposals only.

### v0.9 — FalkorDB backend

**Goal**: third graph backend (Cypher-compatible, GraphBLAS-accelerated, LLM/RAG-positioned). Validates the parity story across all capability layers.

**Decomposition** (~3-4 weeks):

| Sub-version | Deliverable |
|---|---|
| **v0.9.0** | `stores/falkordb.py` + Cypher dialect adaptation; core protocol (schema/entities/traversal). ~2 weeks. |
| **v0.9.1** | Capability parity — full-text (Redis Search), vector (built-in), Bayesian + Causal CPT/DAG storage. ~1-2 weeks. |

**License note**: FalkorDB is **RSAL (Redis Source Available License)**, *not* OSI-approved open source. README will document this honestly alongside Fuseki (Apache 2.0) and Neo4j (GPL/AGPL).

### v1.0+ — Learning Layer (GNN)

**Goal**: GNN integration as the 4th reasoning layer — R-GCN for link prediction over OWL graphs, neural CPT (Pyro) for Bayesian, structure learning for Causal (DECI).

**Out of scope until v1.0**: GPU/training infrastructure, PyTorch Geometric dependency, neural-symbolic loss for OWL constraint preservation. This is the first paradigm shift — ontorag becomes a "training-capable" framework.

### Deferred — layered-ontology-plan Phase 2/3

`docs/design/layered-ontology-plan.md` Phases 2 (Policy/SHACL/SKOS), 3a (State time-series with State Object pattern), 3b (Router/Schema loader), and 4 (Provenance/PROV-O/DCAT) are **deferred until user signal arrives** (G1/G2 gates in that doc). Phase 1 (Named Graph infrastructure) is absorbed into v0.7.0.

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
- v0.7 Bayesian: Don't conflate "Dynamic" (Palantir reasoning capability — Bayesian/Causal) with "State" (time-series ABox — deferred layered-plan Phase 3a). Use the names exactly as defined in the 4-layer stack.
- v0.7 Bayesian: Don't import a Java engine (OpenMarkov, SamIam). Python-native only — pgmpy primary, pyAgrum as performance fallback.
- v0.7 Bayesian: Don't store CPTs in the schema or data named graphs. They go in `urn:ontorag:probabilistic` exclusively.
- v0.8 Causal: Don't auto-modify the causal DAG from observational data without human review. Structure learning (PC algorithm) produces *proposals*, never auto-committed.
- v0.8 Causal: Don't claim causal validity. README and tool docstrings must state the DAG is user-supplied; ontorag computes interventional/counterfactual queries assuming the DAG is correct.
- v1.0 GNN: Don't add GPU/training infrastructure before v0.9 ships. ontorag stays "training-free" through v0.9. v1.0 is the deliberate paradigm shift.

## Open questions (decide when reached)

- ✅ L2 `query_pattern` DSL 검증: 구조적 검증(SPARQL 측 `PatternTriple` regex) + Cypher 측 `_safe_rel()` allowlist + `*` 경로 상한으로 결정.
- ✅ Neo4j SPARQL via n10s endpoint vs. native Cypher translation: **native Cypher translation** 채택 (`core/cypher.py`).
- ✅ **subClassOf 추론 백엔드 divergence 해소**: 이제 양 백엔드 모두 추론 ON. Neo4j는 Cypher `[:rdfs__subClassOf*]`, Fuseki는 쿼리 레벨 `?inst a/rdfs:subClassOf*`(SCHEMA·DATA named graph 조인 + 직접매치 UNION). `ja:OntModelSpec` reasoner 없이 쿼리 레벨로 수렴 — `find_entities`/`count_entities` 결과 일치.
- ✅ Vector similarity: **별도 L1 툴 `find_similar`** 채택 — Neo4j는 native vector index, Fuseki는 Qdrant. `ontorag embed`로 사전 생성.
- ✅ **Multi-ontology per instance 해소**: named-graph 스코핑(Fuseki) + 노드 `_ontology` 태깅(Neo4j), 모든 read 툴 + `load`에 `ontology` 파라미터. 단일 온톨로지 가정 제거(`ontology=None`이 하위호환).
- Auth/multi-tenant: still single-user (no user identity). v0.6.1 adds a config-driven per-ontology **scope lock** (`ONTOLOGY_ACCESS`, read/write/none at the GraphStore boundary) — not authentication; protects against accidental cross-ontology writes/reads, not malicious actors.

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
