# Changelog

All notable changes to this project will be documented in this file.

## v0.6.1 — 2026-05-28

### Added — completes the v0.6 roadmap

- **Cross-ontology entity alignment (`owl:sameAs`)** — `sameas_closure` resolves
  the transitive + symmetric `owl:sameAs` closure of an entity across ontology
  scopes (Fuseki: `(owl:sameAs|^owl:sameAs)+` property path; Neo4j: undirected
  `[:owl__sameAs*1..]`). Exposed as the `find_aligned` MCP tool / agent tool
  (POST `/tools/aligned`) — the agent's 14th tool.
- **Per-ontology access control** (config-driven) — `ONTOLOGY_ACCESS`
  (e.g. `poke:rw,shop:r,secret:none`) defines read/write/none per ontology,
  enforced by an `AccessControlledStore` wrapper at the GraphStore boundary
  (`core/access.py` + `stores/access_wrapper.py`, factory-wired). Unset = fully
  open (backward-compatible). A scope-lock against accidental cross-ontology
  writes/reads — **not** authentication (no user identity). Write methods
  (`load_rdf`/`clear_graph`) and ontology-scoped L1 reads are guarded; capability
  reads (`search_text`/`find_similar`/`find_aligned`) pass through (v0.7 item).

### Performance

- **`load_rdf` pre-parsed-graph fast path** — optional `graph=` kwarg (Protocol
  + both backends) lets the directory loader hand back the graph it already
  parsed for mode detection, eliminating a second parse per file
  (directory-loader.md §3).

## v0.6.0 — 2026-05-28

### Added — agent retrieval tools, directory loader, backend config

- **Agent now wields 13 tools** (was 9). The v0.5 retrieval capabilities were
  reachable only via MCP routes; they are now wired into the chat `AgentLoop`:
  `search_text` (BM25), `find_similar` (vector), and `aggregate` (group-by →
  count/sum/avg/min/max). Verified live: the agent picks `search_text` for
  fuzzy/partial-name lookups and chains `search_text`→`find_similar` for
  "similar to X" questions.
- **`find_similar` subClassOf-aware `class_uri` filter** — restricts neighbours
  to instances of a class (or subclass), on both backends (Fuseki post-filters
  via a scoped SPARQL membership query; Neo4j filters in-query with
  `[:rdfs__subClassOf*0..]`). Threaded through the `/tools/similar` route and
  the agent tool. Raised semantic-goldset answer correctness ~0.64 → ~0.74.
- **Directory / multi-file loader** — `ontorag load <DIR>` scans a directory and
  loads its RDF files: each sub-directory name becomes an ontology id
  (`--ontology` flat-merges), schema files load before data per scope, with a
  Rich per-file progress bar + loaded/skipped/failed summary. Orchestrated in
  `core/batch_loader.py` (the `GraphStore` protocol is unchanged — both backends
  get it for free). New options `--ontology`, `--replace`, `--no-recursive`.
  See `docs/design/directory-loader.md`.
- **`ontorag.yaml` manifest** (optional) for the directory loader — explicit
  file→ontology mapping + load order + globs, overriding the default sub-dir
  rule (`core/manifest.py`). Manifest + `--ontology` is a conflict error.
- **Backend config in the CLI** — `ontorag config set` gained `--graph-store`,
  `--neo4j-url/-user/-password/-database`, `--qdrant-url`; `config show`
  displays `GRAPH_STORE`, `NEO4J_*`, `QDRANT_URL`, `EMBEDDING_PROVIDER`
  (password masked). Previously the backend was env-only.
- **Web UI** — full-text search, find-similar, and aggregate panels added to the
  Data tab (`/ui`), backed by the existing tools.

### Fixed

- **`ontorag load <FILE|DIR>` routing** — the positional (no sub-command) form
  failed with "No such command" because Click resolves the first token as a
  sub-command before the group callback runs. Fixed with a default-command
  group that routes unknown tokens to a hidden `auto` command, so the path form
  works alongside `load schema`/`load data`.
- **Neo4j `traverse(start, predicate=<uri>)`** generated malformed Cypher
  (`-[rel[:...]]->`, double brackets) → `CypherSyntaxError`. Now emits a bare
  type label.
- **`[bench]` extra dependency resolution** — loose pins resolved
  `ragas 0.4.x` + `langchain 1.x` which fails at `import ragas`; pinned to the
  verified-working `ragas<0.3` + `langchain 0.3.x` line.

### Tests

- Stabilized two flaky tests: `test_neo4j_integration_inverse_surfaces`
  (cleared n10s global `_NsPrefDef` prefix state per fixture to kill cross-file
  pollution) and the LangChain live baseline (now gated behind
  `RUN_LIVE_LLM_TESTS=1` + `@pytest.mark.integration`, so a key in the env no
  longer fires a billable live call). Full suite: 886 passed with containers.

### Docs

- README + CLAUDE.md: directory-loader usage, backend config via `config set`,
  13-tool agent + capability tools table, environment-variable table, v0.5.x
  4-domain RAGAS re-run results. `directory-loader.md` marked Implemented.

## v0.5.0 — 2026-05-25

### Added — Neo4j backend, full backend parity, multi-ontology

- **Pluggable graph store** via `GRAPH_STORE=fuseki|neo4j` (default `fuseki`).
  A `create_store()` factory selects the backend; all MCP tools, routes, and
  the CLI depend only on the `GraphStore` protocol. Phase-0 refactor made the
  store layer backend-neutral (`clear_graph`/`aclose` promoted to the protocol;
  raw SPARQL isolated as a Fuseki-only capability).
- **Neo4j + neosemantics (n10s) adapter** — full `GraphStore` protocol in
  Cypher. `handleVocabUris=SHORTEN` + `handleRDFTypes=LABELS_AND_NODES`; URIs
  round-trip through a shorten/expand layer. Native `rdfs:subClassOf` inference
  via `[:rdfs__subClassOf*]`. `docker compose --profile neo4j up` (n10s + apoc +
  GDS auto-installed); install the `[neo4j]` extra for the driver.
- **Full backend parity** — reasoning, full-text, and vector similarity now
  work on **both** backends, each with its native tech:
  - *Reasoning*: Fuseki uses a query-level `?inst a/rdfs:subClassOf*` join
    (no reasoner config); Neo4j uses Cypher subClassOf paths. `find_entities`,
    `count_entities`, and `aggregate` are all subclass-aware.
  - *BM25 full-text* (`search_text` MCP tool): Fuseki via jena-text (Lucene,
    TDB2 config); Neo4j via a native full-text index.
  - *Graph embeddings* (`find_similar` MCP tool + `ontorag embed` CLI):
    structural + textual + `hybrid` (RRF). Neo4j uses GDS FastRP + native
    vector index; Fuseki uses pure-Python FastRP (`core/fastrp.py`) +
    `EmbeddingProvider` (OpenAI/Ollama via `EMBEDDING_PROVIDER`) → **Qdrant**
    (`[vector]` extra, `--profile qdrant`).
- **Multi-ontology per instance** — load and query many ontologies in one
  instance. `load`/`embed`/all read tools take an optional `ontology` scope
  (`None` = union of all, backward-compatible). Fuseki isolates with
  per-ontology named graphs (`urn:ontorag:{id}:schema/data`); Neo4j tags nodes
  with an `_ontology` list. Embeddings are scoped too (Qdrant `ontology`
  payload with un-tag-on-shared semantics; Neo4j `_ontology` post-filter).
- **`describe_entity` now surfaces `owl:inverseOf`** relationships (incoming
  edges presented under their declared inverse predicate).
- **CLI**: `ontorag embed [--mode structural|textual|both] [--ontology <id>]`;
  `ontorag load [--ontology <id>]`. **API**: `/load` gains an `ontology` form
  field; the api image installs the `[neo4j,vector]` extras.

### Security / fixes

- Hardened `uri_ref` to validate prefixed names and `urn:` (not only `://`),
  closing a latent SPARQL-injection path used across all Fuseki tools.
- Cypher rel-types/labels/keys are gated by `_safe_rel`; embedding/search query
  strings and ontology ids are bound/validated.

## v0.4.1 — 2026-05-19

### Added — prompt externalization + SHACL validation gate

- **SHACL validation step** between LLM-generated triples and Fuseki load.
  Triples that fail validation are filtered out; full violation detail
  (focus node, result path, message, severity) is surfaced through the
  new `PopulationResult.violations` field.
- **`ontorag learn derive-shapes <schema.ttl> [-o out.ttl]`** — generates
  a SHACL skeleton from an OWL TBox using three mechanical mappings:
  `rdfs:range xsd:T` → `sh:datatype`, `rdfs:range <Class>` → `sh:class` +
  `sh:nodeKind sh:IRI`, `owl:FunctionalProperty` → `sh:maxCount 1`.
  Domain knowledge (enumerations, value ranges, cardinality > 1) is
  refined by hand afterwards.
- **`--shapes PATH` option** on `ontorag learn populate` and
  `ontorag learn populate-structured`. Optional; default preserves the
  prior v0.4.0 behaviour (no SHACL step). When the file is missing the
  CLI logs a warning and proceeds without validation.
- **Pre-authored shapes** for all five example domains —
  `examples/{pokemon,techstack,ods,pure_land,commerce}/shapes.ttl` —
  with realistic OWL-inexpressible constraints (Pokémon types ≤ 2,
  HP ∈ [1, 999], ISO 4217 currency regex, vowNumber ∈ [1, 48], etc.).
- **Six prompts moved to package resources** under
  `src/ontorag/learn/prompts/` and `src/ontorag/chat/prompts/`, loaded
  via `importlib.resources`. Byte-identical to v0.4.0 inline strings
  (verified against git HEAD). Per-domain overrides not introduced in
  v0.4.1 — single source of truth at package level.
- **CLI shows SHACL drop count** — `⚠ SHACL 위반으로 N건 제외됨` follows
  the load summary when violations were filtered.

### Fixed

- `cli_learn._load()` now unpacks the new `(loaded_count, violations)`
  tuple returned by `LLMOntologyLearner._load_triples`. Without this
  fix the `populate` and `populate-structured` commands would have
  crashed with `TypeError: unsupported format string passed to
  tuple.__format__` once they reached the load step.

### Documentation

- `README.md` / `README.ko.md` — new four-step SHACL walkthrough with
  real input TTL, real derive-shapes output, hand-refinement diff,
  expected CLI output, and a Python SDK example that iterates
  `PopulationResult.violations`. Closes with a five-domain shapes
  summary table.
- Quick-reference CLI block in both READMEs lists `derive-shapes` and
  the `--shapes` option on the two populate commands.

### Notes

- Backwards compatible: every existing v0.4.0 invocation continues to
  work unchanged. SHACL is opt-in via `--shapes` and the new Python
  kwarg.
- `pyshacl>=0.25.0` was already declared in v0.4.0 dependencies — no
  new top-level requirement.
- Tests: +11 in `tests/test_learn_shacl.py` (validate + derive_from_owl).
  Full learn + cli regression: 159 passing.

## v0.4.0 — 2026-05-19

### Added — 4-domain RAGAS final benchmark + decision-grid guide

- **4-domain head-to-head RAGAS benchmark** with `gpt-4o` as both agent
  and judge — Pure Land (50q, ko), ODS (20q, en), Pokemon (20q, ko),
  Techstack (20q, ko). Full result JSONs under each
  `examples/<domain>/bench_results/`.
- **New goldsets** — `examples/pokemon/goldset.jsonl` and
  `examples/techstack/goldset.jsonl` (20q each, easy/medium/hard/trap
  distribution, hard cases exercising the respective
  TransitiveProperty closures).
- **`examples/pokemon/README.md`** — new domain README (rationale,
  TBox/ABox summary, evolution chains, RAGAS table).
- **`RAGAS_JUDGE_MODEL` env-var fallback** in
  `src/ontorag/eval/metrics/ragas_wrapper.py` for opt-in judge model
  selection (default remains `gpt-4o-mini`).
- **2×2 decision grid** (OWL richness × LLM contamination) in
  top-level `README.md` / `README.ko.md`, plotting all four domains
  with their qualitative outcomes.
- **Standardized `## Disclaimer` policy** across all five example
  READMEs (Pokemon · Techstack · ODS · Pure Land · Commerce) with
  uniform 4-item structure: Rights / Nature / No affiliation /
  Takedown commitment. Pokemon disclaimer is bilingual EN+KO due to
  trademark sensitivity.
- **BENCHMARK_RESULTS.md v9 section** — 4-domain cross-comparison
  with OWL-feature-richness × RAGAS-win correlation analysis.

### Changed — README structure and disclaimers

- Top-level READMEs now include a "Benchmark results — 4-domain RAGAS
  final (2026-05)" section between "Evaluation Harness" and "Roadmap",
  with three findings explained in plain prose: (1) RAGAS Faithfulness
  has a chunk-quote style bias; (2) ontorag's edge grows with OWL
  feature richness; (3) Hallucination 0% and Citation 45-66% are
  ontorag-exclusive — and the "—" entries for LangChain mean
  "not measurable", not "zero".
- Pure Land's existing "Disclaimer / 면책 조항" section renamed to
  "Doctrinal Disclaimer / 교리적 면책 조항" to separate religious
  doctrinal scope from copyright/attribution; new standard
  "## Disclaimer" section added separately.
- Cross-domain attribution footnote now describes the **policy**
  (4 items, uniform location) rather than enumerating ad hoc per-domain
  notices.

### Fixed — merged accumulated v2-v8 surgical fixes from eval-harness

- `src/ontorag/core/sparql.py` — case-insensitive `rdfs:label`
  equality with lang-literal support.
- `src/ontorag/stores/base.py` — `PropertySummary` / `ClassSummary`
  extended with `description`, `is_transitive`, `inverse_of_uri`.
- `src/ontorag/stores/fuseki.py` — `get_schema` now extracts
  `owl:TransitiveProperty`, `owl:inverseOf`, `rdfs:comment`,
  `skos:definition`.
- `src/ontorag/chat/agent.py` — TBox-driven prompt generation
  (`rdfs:comment` + OWL flags rendered into the system prompt); system
  prompt reduced from ~60 to ~25 lines; `property_path_query` tool
  added with three modes (start_uri / start_label / start_class_uri).

### Notes

- During the eval-harness → main merge, 4 files in `src/` had conflicts
  with main's `ae3c308 fix(core): multilingual label equality +
  TBox-driven prompt generalisation`. Resolution chose eval-harness
  versions throughout — all `bench_results/*.json` in this release were
  produced against that code; `ae3c308`'s parallel approach to the
  same OWL semantics surface is subsumed by the broader eval-harness
  treatment.

---

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
