# Changelog

All notable changes to this project will be documented in this file.

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
