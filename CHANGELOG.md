# Changelog

All notable changes to this project will be documented in this file.

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
