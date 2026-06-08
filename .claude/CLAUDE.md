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
Layer 4 — Learning              ← v1.1+ (GNN: R-GCN link prediction, neural CPT, structure learning)
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

## Architecture

Browser / CLI → FastAPI `POST /chat` (SSE) → Agent Loop calls an LLM
(Claude / GPT / Ollama), which in turn calls MCP tools (L1 intent tools + L2 JSON
DSL `query_pattern`; raw SPARQL is L3 dev-only, MCP-excluded). MCP tools depend
only on the `GraphStore` Protocol, served by one of three swappable adapters —
Fuseki (SPARQL/TDB2), Neo4j (n10s + Cypher), or FalkorDB (Cypher) — selected by
`GRAPH_STORE`. Reasoning capability is layered on top via `BayesianStore` /
`CausalStore` Protocols (v0.7 / v0.8), backed by pgmpy.

SSE event types streamed to the client: `thinking`, `tool_call`, `tool_result`,
`text`, `rate_limit`, `error`, `done`. Tool calls and results are visible —
the agent loop is white-box, not black-box.

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

The `GraphStore` Protocol (`src/ontorag/stores/base.py`) is the single seam
between MCP tools and the underlying store. Every L1 intent tool, the L2 JSON
DSL `query_pattern`, and the capability tools (`search_text`, `find_similar`,
`find_aligned`) target this Protocol — adding a new backend means writing one
adapter + a factory branch, never touching routes/CLI/tests. The same pattern is
repeated by `BayesianStore` and `CausalStore` Protocols (v0.7 / v0.8) for the
reasoning capabilities. Raw SPARQL access is intentionally a *private* method
(`_sparql_select`, Fuseki-only) and is never exposed via MCP.

All read methods take an optional `ontology` scope (`None` = union / legacy
default) for multi-ontology hosting (v0.5).

## LLMs4OL Learner Protocol (v0.3, shipped)

`OntologyLearner` Protocol + result types live in `src/ontorag/learn/base.py`. Three
LLM-prompting tasks against the current TBox: `type_term` (A: text → class_uri),
`discover_taxonomy` (B: propose `rdfs:subClassOf`), `extract_relations` (C: propose
object/data property triples). `populate_from_text` runs A+B+C and optionally
auto-loads via `store.load_rdf(..., mode="data")`.

Invariants enforced in the implementation:
- TBox is read at call time (no stale cache); all output `class_uri` / `predicate_uri`
  are validated to exist in the current schema before return.
- `min_confidence` (default 0.7) filters low-quality proposals.
- New TBox classes are never proposed automatically — human review required.

## Tech stack

- Language: Python 3.12
- Package manager: uv (preferred)
- Web framework: FastAPI
- MCP: `fastapi-mcp>=0.4.0` — FastAPI 라우트를 MCP 툴로 자동 변환, ASGI transport (HTTP 오버헤드 없음), `/mcp` 엔드포인트 자동 생성
- Graph stores: Fuseki (Apache Jena, SPARQL 1.1, ~200MB image) · Neo4j + n10s (Cypher) · FalkorDB (Cypher, RSAL). Select via `GRAPH_STORE`.
- LLM SDKs: anthropic · openai · ollama
- Probabilistic / Causal engine: pgmpy (`[bayes]` extra, lazy import)
- CLI: Typer + Rich (progress bars, status display)
- Deployment: Docker + docker-compose
- Tests: pytest

## Repo layout

High-level only — use `tree src/ontorag` for the current truth.

```
src/ontorag/
├── api/            # FastAPI app + routes (incl. routes/tools/ = MCP-exposed tools)
├── core/           # RDF loader, SPARQL/Cypher translators, FastRP, bayes/causal vocab
├── stores/         # GraphStore Protocol (base.py) + adapters: fuseki / neo4j / falkordb
│                   #   + per-backend mixins (search / embedding / bayes / causal)
│                   #   + access_wrapper.py (v0.6.1 scope lock), _qdrant.py
├── llm/            # LLMProvider (anthropic / openai / ollama) + EmbeddingProvider
├── chat/           # Agentic MCP loop (SSE emit)
├── learn/          # v0.3 LLMs4OL (term typing, taxonomy, relation extraction)
├── bayes/          # v0.7 BayesianEngine (pgmpy wrapper) + CPT learning
├── causal/         # v0.8 CausalEngine (do/counterfactual) + PC discovery
├── web/            # v0.8.4 Reasoning WebUI (HTMX partials)
└── cli*.py         # Typer entry points: cli.py + cli_bayes.py + cli_causal.py

examples/           # pokemon · commerce · ods · pure_land · techstack · smoking · foaf
tests/              # pytest, integration marker for live-container suites
docs/design/        # ALL design notes (single source of truth for shipped decisions)
docker/             # Dockerfiles + Fuseki config templates
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
docker compose up                                       # Fuseki + API + Web UI
docker compose --profile neo4j up                       # + Neo4j backend
docker compose --profile falkordb up                    # + FalkorDB backend
docker compose --profile qdrant up                      # + Qdrant (Fuseki find_similar)
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

**v0.8.4** ✅ shipped — **Reasoning WebUI**: a single `🧮 Reasoning` tab (`web/templates/reasoning.html`) with Bayesian / Causal sub-tabs over the existing HTMX-partial pattern. Bayesian: evidence/query builders → `compute_posterior` / `mpe`. Causal: do / observed / query builders → `do_query` / `counterfactual` / `identify_effect`, plus the DAG edge list and a "do(X)로 비교 →" cross-link that seeds the Causal tab from the posterior evidence (the see≠do demo). Shared renderer `partials/dist_bars.html`; capability-guarded (`partials/reasoning_error.html` amber hint when no backend / no BN / no pgmpy). Routes in `web/router.py` (`/ui/reasoning` + `/ui/reasoning/posterior|mpe|causal/do|causal/identify|causal/counterfactual`), all reusing `BayesianEngine` / `CausalEngine`. Tests: `tests/test_web_reasoning.py` (10, guards run without pgmpy; happy-path asserts see 0.72 ≠ do 0.60).

### v0.9 — FalkorDB backend ✅ shipped

**Goal**: third graph backend (Cypher-compatible, GraphBLAS-accelerated, LLM/RAG-positioned). Validates the parity story across all capability layers.

**Decomposition**:

| Sub-version | Deliverable |
|---|---|
| **v0.9.0** ✅ shipped | `stores/falkordb.py` (async `falkordb` client, `[falkordb]` extra) — **reuses the Neo4j L1 + reasoning mixins** (schema/entity/traversal/bayes/causal) since FalkorDB is OpenCypher. `_run` normalises Node→property-dict so the shared mixins work unchanged. **Custom rdflib→Cypher loader** replaces n10s (FalkorDB has none): reproduces SHORTEN (`prefix__local`), LABELS_AND_NODES (type-as-extra-label — FalkorDB supports multi-label), ARRAY (every literal a LIST), prefixes persisted in a `:_OntoragMeta` node. TBox/ABox classify + status + dump rewritten without `EXISTS{}` / `n10s.export`. |
| **v0.9.1** ✅ shipped | Capability parity — full-text via FalkorDB native `db.idx.fulltext` (**`_fulltext` scalar shadow** property worked around FalkorDB only indexing scalars, not the ARRAY props); vector via native `CREATE VECTOR INDEX` + `db.idx.vector.queryNodes` with **pure-Python FastRP** (`core/fastrp.py`, no GDS — the Fuseki path) for structural embeddings + EmbeddingProvider textual + RRF hybrid; Bayesian + Causal CPT/DAG storage reused from the Neo4j mixins (distinct labels, not `:Resource`). |

**Dialect notes** (vs Neo4j, all live-verified against `falkordb/falkordb:latest`): `db.idx.*` not `db.index.*`; no `EXISTS{}` subqueries (use OPTIONAL MATCH + count); no `CONTAINS()` function (operator form only — fixed in shared `_build_filter_cypher`); full-text indexes scalars only; multi-label + `*0..N` paths + array props + `vecf32()` all supported. **Quality bar**: `tests/test_falkordb_integration.py` (11 tests) — full protocol + search + similar + bayes + causal + dump, identical results to Fuseki/Neo4j.

**License note**: FalkorDB is **RSAL (Redis Source Available License)**, *not* OSI-approved open source. README documents this honestly alongside Fuseki (Apache 2.0) and Neo4j (GPL/AGPL).

### v1.0 — Production-Ready & Proven ✅ shipped

**Goal**: the 0.x→1.0 maturity jump — not a new capability but *trust*. GNN was
deliberately deferred (it's the v1.1+ paradigm shift). Two pillars, both
evidence-backed by a pre-implementation two-agent audit:

| Pillar | Deliverable |
|---|---|
| **Production hardening** | Configurable query/LLM timeouts on **all** backends (`NEO4J_QUERY_TIMEOUT`/`FALKORDB_QUERY_TIMEOUT`/`FUSEKI_TIMEOUT`/`LLM_TIMEOUT` via `core/config.py:env_timeout`; Neo4j `session.run(timeout=)`, FalkorDB `graph.query(timeout=ms)`, Fuseki httpx, anthropic/openai/ollama clients) — closes the "a hung query blocks the worker" gap. Global `@app.exception_handler(Exception)` → structured `{detail,type}` 500 with server-side logging (no raw-traceback leak). **New CI `test.yml`**: ruff + `pytest -m "not integration"` (910) hard-gates every push/PR; an informative integration job runs Neo4j+FalkorDB service containers. (The pre-existing `eval.yml` only ran eval-module tests behind a path filter — main code reached `main` with no CI.) |
| **Proof** | `docs/BENCHMARK_v1.md` — key-free, reproducible: goldset quality (5 domains / 130 q, 0 `gold_sparql` failures) + **3-backend deterministic parity** (7/7 protocol metrics identical across Fuseki/Neo4j/FalkorDB → `full_parity=True`). README leads with the parity headline. |

**Known sharp edge (documented, not a blocker)**: on Neo4j/FalkorDB a double
`replace=True` (schema then data) can drop property-type nodes since schema+data
share one physical graph; the normal `clear → schema → data` path is unaffected.

**Out of scope (→ v1.1+)**: connection-pool tuning, startup health-check,
JSON/JSONL typed-literal fidelity (TTL already preserves datatypes), RAGAS in CI.

### v1.1+ — Learning Layer (GNN)

**Goal**: GNN integration as the 4th reasoning layer — R-GCN for link prediction over OWL graphs, neural CPT (Pyro) for Bayesian, structure learning for Causal (DECI).

**Out of scope until v1.1**: GPU/training infrastructure, PyTorch Geometric dependency, neural-symbolic loss for OWL constraint preservation. This is the first paradigm shift — ontorag becomes a "training-capable" framework.

### Deferred — layered-ontology-plan Phase 2/3

`docs/design/layered-ontology-plan.md` Phases 2 (Policy/SHACL/SKOS), 3a (State time-series with State Object pattern), 3b (Router/Schema loader), and 4 (Provenance/PROV-O/DCAT) are **deferred until user signal arrives** (G1/G2 gates in that doc). Phase 1 (Named Graph infrastructure) is absorbed into v0.7.0.

## What NOT to do (anti-patterns)

**Architecture & dependencies**
- Don't pull in LangChain, LlamaIndex, or LangServe. Each tool is small; write it directly.
- Don't skip the GraphStore / BayesianStore / CausalStore abstractions. All tools depend on the Protocol, never on a concrete backend.
- Don't expose raw SPARQL to the LLM. MCP에는 L1 툴 + L2 `query_pattern`만 노출. raw SPARQL (`_sparql_select`)은 개발자 디버그 전용.
- Don't add features from `patent_board` directly. Domain-specific code stays out.
- Don't add BPM, notifications, or multi-tenant — separate repo.
- Don't include KIPRIS/IPC/CPC code or data. License risk and scope creep.
- Don't optimize prematurely. Get it working first; profile second.

**Ontology learning (LLMs4OL)**
- Don't propose new TBox classes automatically — only ABox triples using existing schema. TBox evolution requires human review.
- Don't output `predicate_uri` or `class_uri` that don't exist in the current TBox — validate against SchemaResult before returning.

**Probabilistic / Causal reasoning**
- Don't conflate "Dynamic" (Palantir reasoning capability — Bayesian/Causal) with "State" (time-series ABox — deferred layered-plan Phase 3a). Use the names exactly as defined in the 4-layer stack.
- Don't import a Java engine (OpenMarkov, SamIam). Python-native only — pgmpy primary, pyAgrum as performance fallback.
- Don't store CPTs in the schema or data named graphs. They go in `urn:ontorag:probabilistic` exclusively; causal DAGs in `urn:ontorag:causal`.
- Don't auto-modify the causal DAG from observational data. Structure learning (PC) emits *proposals only*, never auto-committed.
- Don't claim causal validity. README and tool docstrings must state the DAG is user-supplied; ontorag computes interventional/counterfactual queries assuming the DAG is correct.

**Learning layer (v1.1+, GNN)**
- Don't add GPU/training infrastructure incrementally. GNN is the deliberate paradigm shift in v1.1+; ontorag stays "training-free" through v1.0.

## Open questions (decide when reached)

- **Auth/multi-tenant**: still single-user (no user identity). v0.6.1 added a
  config-driven per-ontology *scope lock* (`ONTOLOGY_ACCESS`, read/write/none at
  the GraphStore boundary) — not authentication; protects against accidental
  cross-ontology writes/reads, not malicious actors. Real auth is deferred.

(Historical decisions on L2 DSL injection defense, n10s-vs-Cypher, subClassOf
parity, vector similarity, multi-ontology scoping — all resolved and captured
in `docs/design/*.md`.)

## How to work with Claude Code on this repo

- Tools depend on `GraphStore` / `BayesianStore` / `CausalStore` Protocols — never import a concrete backend.
- Update or add tests in the same change (cross-backend parity tests where capability-relevant).
- Keep changes scoped to one concern per commit; default to smaller scope when unsure.
- Detailed shipped-decision rationale lives in `docs/design/*.md` — read there before re-litigating settled questions.

## License

MIT. No proprietary or domain-specific code from `patent_board`.
