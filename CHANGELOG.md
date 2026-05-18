# Changelog

All notable changes to this project will be documented in this file.

## v0.3.3 — 2026-05-18

### Fixed — multilingual rdfs:label equality (silent 0-row bug)

- **`core/sparql.py:build_filter_sparql`** — `?label = "Peacock"` previously
  failed to match `"Peacock"@en` under RDF semantics, so any
  multilingual rdfs:label filter silently returned 0 rows. The `=`
  operator now OR-disjuncts plain equality with `STR(?label) = "Peacock"`,
  matching both plain and language-tagged literals. Other operators are
  unchanged. Discovered while benchmarking against the Pure Land
  multilingual goldset; affects any ontology with `"value"@lang` literals.

### Added — TBox-driven prompt generalisation

- **`stores/base.py:PropertySummary`** — gains `is_transitive: bool = False`
  and `inverse_of_uri: str | None = None`. Backward-compatible (defaults).
- **`stores/fuseki.py:FusekiStore.get_schema()`** — single SPARQL round-trip
  now pulls `owl:TransitiveProperty` and `owl:inverseOf` in addition to
  the existing class/property metadata. Per-property aggregation makes
  flags sticky across multiple row matches.
- **`chat/agent.py:_format_schema_for_prompt`** — schema context now lists
  every property with URI, label, type, `domain → range`, and a
  `TRANSITIVE` / `inverseOf=…` flag column. The LLM no longer has to
  guess predicate URIs from labels.
- **`chat/agent.py:_TOOLS[traverse_graph]`** — description previously
  hard-coded Pokemon examples (`evolvesFrom`, "X가 진화하면?"). Now
  references the TBox `TRANSITIVE` flag + closure vocabulary instead —
  domain-agnostic.
- **`chat/agent.py:_SYSTEM_BASE`** — explicit fallback rule
  (find_entities 0 rows → try other label / sub-class) + explicit guard
  against pasting natural-language labels into URI slots.

### Why this matters

These are not eval-harness scaffolding — they are ontorag chat-agent
behaviour fixes. Together they let `gpt-4o-mini` synthesise correct
schema-aware tool calls on a multilingual ontology and follow `owl:TransitiveProperty`
closures via `traverse_graph`. Measured impact on the v0.3.2 benchmark
goldsets (see `BENCHMARK_RESULTS.md` for details):

| Domain | Citation provided | RAGAS Answer Correctness |
|---|---|---|
| Commerce 20q | 1/20 → **11/20** (11×) | 0.17 → **0.31** (+82 %) |
| Pure Land 50q | 14/50 → **28/50** (2×) | 0.20 → **0.27** (+35 %) |

Hallucination rate held at 0.000 on both runs.

## v0.3.2 — 2026-05-18

### Added — TBox/ABox Dump

- **`ontorag dump schema|data|all`** — CLI 덤프 커맨드 그룹
  - `--format ttl|json|jsonl|xlsx` — 출력 포맷 선택 (기본값: ttl)
  - `--output FILE` — 저장 경로 (생략 시 `ontorag_{target}_{timestamp}.{ext}` 자동 생성)
- **`GET /dump?target=schema|data|all&format=ttl|json|jsonl|xlsx`** — REST 덤프 엔드포인트
  - `Content-Disposition: attachment` 헤더 포함 → 브라우저 직접 다운로드
  - target=all + format=xlsx 시 TBox/ABox를 별도 시트로 분리
- **Web UI 다운로드 버튼** — Schema 탭과 Data 탭에 📥 다운로드 섹션 추가
  - Schema 탭: TBox 다운로드 (TTL/JSON/JSONL/XLSX)
  - Data 탭: ABox 다운로드 + All 다운로드 (TBox+ABox 합쳐서)
- **`GraphStore.dump_graph(target, fmt)`** — Protocol에 추가 (Neo4j 어댑터도 동일 인터페이스 강제)
- **`FusekiStore._gsp_get(named_graph)`** — GSP GET 메서드 추가 (404 시 빈 Graph 반환)
- **`openpyxl>=3.1.0`** — 신규 의존성 추가

### Format details

| 포맷 | MIME | 내용 |
|------|------|------|
| `ttl` | `text/turtle` | RDF Turtle 직렬화 |
| `json` | `application/json` | `[{"s":…,"p":…,"o":…},…]` 트리플 배열 |
| `jsonl` | `application/x-ndjson` | 트리플 1개/줄 NDJSON |
| `xlsx` | `application/vnd.openxmlformats…` | Subject/Predicate/Object 컬럼 (all: TBox+ABox 별도 시트) |

## v0.3.1 — 2026-05-18

### Added — Structured ABox Population

- **`ontorag learn populate-structured`** — reads CSV/JSON/JSONL, maps columns to TBox property URIs via LLM, converts each row to RDF triples, loads to Fuseki
  - Column mapping cached in `<file>.mapping.json` — second run reuses it with zero LLM calls
  - `--class-uri` — TBox class URI for each row (e.g. `pk:Pokemon`)
  - `--id-column` — column to use as subject URI slug; deterministic uuid5 if omitted (idempotent across re-runs)
  - `--batch-size` (default 50) — rows per LLM mapping call
  - `--min-confidence` (default 0.7) — column mapping confidence threshold
  - `--yes` — skip Fuseki load confirmation prompt
  - Nested JSON keys flattened with dotted notation: `{"stats":{"hp":35}}` → `stats.hp`

### Fixed

- `propose_mapping` swallowed all exceptions with a bare `except Exception: return []` — network/auth errors are now re-raised so the CLI shows the real cause; JSON parse errors (recoverable) still return `[]`
- `mint_subject_uri` only replaced spaces with underscores — special characters (apostrophes, colons, hashes) in id-column values produced invalid URI path segments; fixed with `urllib.parse.quote(safe="-._~")`
- `populate_from_structured` ran the full LLM pipeline before detecting an empty TBox, giving a misleading "no triples generated" message; now raises `ValueError` early with actionable guidance

## v0.3.0 — 2026-05-18

### Added — LLMs4OL Ontology Learning pipeline

- **`ontorag learn` CLI** — four commands for LLMs4OL tasks (A/B/C + full pipeline):
  - `type-term` — map a text mention to the most likely TBox class (Task A)
  - `taxonomy` — propose `rdfs:subClassOf` relations from text evidence (Task B)
  - `extract` — extract RDF triples with schema-validated predicate URIs (Task C)
  - `populate` — run A+B+C, preview results in a Rich table, confirm before loading
- **`POST /tools/learn/type-term`** and **`POST /tools/learn/extract-triples`** — two new MCP-exposed API endpoints (L1, `fastapi-mcp` auto-converts to MCP tools)
- **`LLMOntologyLearner`** — concrete `OntologyLearner` protocol implementation backed by an LLM provider + GraphStore; fetches live TBox at call time — no stale schema cache
- **`SchemaResult.properties`** — new field on `SchemaResult` populated by `FusekiStore.get_schema()`; enables Task C predicate validation without N+1 `get_class_detail()` calls
- **`force_tool_name` parameter** on `AnthropicProvider.complete()` and `OpenAIProvider.complete()` — maps to `tool_choice={"type":"tool","name":"..."}` / `{"type":"function","function":{"name":"..."}}`, guarantees structured JSON output for all LLMs4OL prompts
- **Tech Stack ontology example** (`examples/techstack/`) — demonstrates `owl:TransitiveProperty` inference on a dependency chain (Next.js → React → Node.js) and LLMs4OL extension from `corpus.txt`
- **`cli_learn.py`** — learn command group extracted from `cli.py` into a dedicated module

### Fixed
- `populate_from_text` called LLM pipeline twice when `auto_load=True`; now runs once and reuses the stored result for the load step
- `PopulationResult` was mutated after construction; now constructed immutably in a single call
- `str(exc)` leaked internal exception details through HTTP 500 responses in learning routes; replaced with `logger.exception` + generic message

## v0.2.0 — 2026-05-17

### Added
- Web UI at `/ui` with three tabs: Schema (TBox graph), Data (ABox browser), Playground (LLM chat)
- Schema tab: interactive Cytoscape.js class hierarchy graph, TBox upload, SHACL/syntax validation
- Data tab: instance browser by class, entity detail side panel with depth-2 neighbourhood graph, ABox upload with append/replace mode
- Playground tab: real-time tool-call display, result graph visualization, session management, in-browser LLM config
- Rate-limit SSE event (`rate_limit`) with animated banner and automatic retry (up to 3 attempts)
- Forced tool-use on first LLM turn when ontology has data (`tool_choice: any` / `required`) — prevents LLM from answering from training knowledge instead of the graph
- RDF file upload endpoints: `POST /ui/schema/upload`, `POST /ui/data/upload`

### Fixed
- Cytoscape.js result graph not rendering on first query (double `requestAnimationFrame` defers init until container reflow)
- LLM skipping ontology queries for entities it recognises from training data (Pokémon, etc.)

## v0.1.0 — 2026-05-17

### Added
- Apache Jena Fuseki integration with OWL inference
- 9 ontology-aware MCP tools (8 L1 intent + 1 L2 DSL)
- Anthropic / OpenAI / Ollama LLM providers
- FastAPI server with SSE streaming
- CLI: load, config, serve, chat, status
- Pokémon example ontology with TransitiveProperty demo
- Multilingual label support (Korean + English)
- Docker Compose deployment
