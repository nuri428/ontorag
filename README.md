# ontorag

**Ontology-aware RAG framework — RDF/OWL as the source of truth.**

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[한국어 문서](README.ko.md)

---

Most RAG systems treat knowledge as flat text chunks searched by embedding similarity.
**ontorag** treats the ontology as the source of truth: an LLM agent navigates your RDF/OWL graph using structured MCP tools rather than approximate vector search.

```
User query → LLM agent → ontology tools (get_schema / find_entities / traverse_graph …)
                                      ↓
                              Apache Jena Fuseki  (SPARQL 1.1)
                                      ↓
                         Structured JSON answers
```

---

## Key features

| Feature | Detail |
|---|---|
| **Ontology-first** | RDF/OWL schema (TBox) + instance data (ABox) as primary structure |
| **Agentic MCP loop** | LLM calls 9 typed tools; tool calls visible in SSE stream |
| **Web UI** | Built-in browser interface — Schema graph, Data browser, Playground chat at `/ui` |
| **Multi-LLM** | Anthropic Claude · OpenAI · Ollama (local) |
| **GraphStore Protocol** | Abstract interface — swap Fuseki → Neo4j without changing tool code |
| **SSE streaming** | `thinking / tool_call / tool_result / text / done / rate_limit` events |
| **Progressive disclosure** | `get_schema` (compact) + `get_class_detail` (drill-down) |
| **Injection-safe L2 DSL** | `query_pattern` translates JSON triple patterns to SPARQL internally |
| **Schema caching** | Schema injected into system prompt at session start — no `get_schema` call per turn |
| **Docker first** | `docker compose up` → ready in < 60 s |

---

## Quickstart

**Prerequisites:** Docker · Docker Compose · Anthropic _or_ OpenAI API key

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
cp .env.example .env           # set ANTHROPIC_API_KEY (or OPENAI_API_KEY)

docker compose up -d           # starts Fuseki + API

uv run ontorag load schema examples/pokemon/schema.ttl
uv run ontorag load data   examples/pokemon/data.ttl

uv run ontorag chat
```

Example session:

![Pokemon chat demo](assets/pokemon_chat_en.png)

---

## Web UI

After starting the server, open **http://localhost:8000/ui** in your browser.

### Schema tab (TBox)

Browse the ontology class hierarchy as an interactive Cytoscape.js graph. Click a node to highlight its neighbourhood; double-click to reset. Upload TBox files (always replace) and run syntax / SHACL validation directly in the browser.

![Schema tab](assets/TBox.png)

### Data tab (ABox)

Select a class from the dropdown to browse its instances. Click any row to open an entity detail panel showing all properties and a depth-2 neighbourhood graph. Upload ABox files with **append** or **replace** mode.

![Data tab](assets/ABox.png)

### Playground tab

Chat with the LLM agent. Tool calls (`find_entities`, `traverse_graph`, …) are shown in real time as they execute. Query results that contain graph data render as an interactive result graph. Manage conversation sessions and configure the LLM provider without restarting the server.

![Playground tab](assets/playground.png)

---

## Architecture

```
User  (CLI / browser)
  │
  ▼  POST /chat   (SSE stream)
┌────────────────────────────────────────┐
│             FastAPI Server             │
│                                        │
│   /chat ──▶  AgentLoop                 │
│                  │                     │
│        LLM  (Claude / GPT / Ollama)    │
│                  │  tool_use           │
│  ┌───────────────────────────────────┐ │
│  │  L1 intent tools  (MCP exposed):  │ │
│  │  get_schema        find_entities  │ │
│  │  get_class_detail  describe_entity│ │
│  │  count_entities    traverse_graph │ │
│  │  find_path         find_related   │ │
│  │  L2 DSL:  query_pattern           │ │
│  │  L3 dev:  query_sparql_raw (hide) │ │
│  └───────────────┬───────────────────┘ │
└──────────────────┼─────────────────────┘
                   │ SPARQL (HTTP)
                   ▼
        Apache Jena Fuseki   ← Phase 1
        Neo4j + n10s         ← Phase 2
```

### SSE event types

| Event | Payload | When |
|---|---|---|
| `thinking` | `content: str` | Before each LLM turn |
| `tool_call` | `tool: str, content: dict` | LLM requested a tool |
| `tool_result` | `tool: str, content: any` | Tool returned |
| `text` | `content: str` | LLM final answer chunk |
| `done` | — | Turn complete |
| `error` | `content: str` | Unrecoverable error |
| `rate_limit` | `retry_after: int` | API rate limit hit — retrying in N seconds |

---

## Installation

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
uv sync          # installs all dependencies
```

Requires [uv](https://docs.astral.sh/uv/) and Docker.

---

## Configuration

```bash
# Anthropic (default)
ontorag config set --provider anthropic --api-key sk-ant-...

# OpenAI
ontorag config set --provider openai --api-key sk-...

# Ollama (local, no key required)
ontorag config set --provider ollama --ollama-url http://localhost:11434

# Override model
ontorag config set --model claude-opus-4-7
ontorag config set --model gpt-4o-mini

# Fuseki endpoint
ontorag config set --fuseki-url http://localhost:3030

# Inspect
ontorag config show
```

Settings are written to `.env` in the current directory.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` · `openai` · `ollama` |
| `LLM_MODEL` | provider default | Model name |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic |
| `OPENAI_API_KEY` | — | Required for OpenAI |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server |
| `FUSEKI_URL` | `http://localhost:3030` | SPARQL endpoint |
| `FUSEKI_DATASET` | `ontorag` | Dataset name |

---

## CLI reference

```bash
ontorag init [DIR]              # Scaffold project files (docker-compose, .env.example, examples)

ontorag load schema <FILE>               # Load TBox (class / property definitions)
ontorag load data   <FILE>               # Load ABox — appends to existing data
ontorag load data   <FILE> --replace     # Load ABox — replaces existing data
ontorag load        <FILE>               # Auto-detect TBox vs ABox

ontorag clear schema                     # Drop TBox graph
ontorag clear data                       # Drop ABox graph
ontorag clear all                        # Drop both graphs

ontorag serve [--host HOST] [--port PORT] [--reload]

ontorag chat                    # Interactive REPL

ontorag status                  # Graph store connection + triple counts

ontorag config set [OPTIONS]
ontorag config show

# v0.3 — Ontology learning from text
ontorag learn type-term "React"                        # Task A — map term to TBox class
ontorag learn taxonomy corpus.txt                      # Task B — propose rdfs:subClassOf
ontorag learn extract corpus.txt                       # Task C — extract RDF triples
ontorag learn populate corpus.txt [--yes]              # A+B+C pipeline → Fuseki

# v0.3.1 — Structured ABox population (CSV / JSON / JSONL)
ontorag learn populate-structured data.csv \
    --class-uri pk:Pokemon --id-column name [--yes]
ontorag learn populate-structured data.jsonl --batch-size 100 --yes
ontorag learn populate-structured nested.json --min-confidence 0.8
```

---

## REST API

### `POST /chat`

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "List all Fire-type Pokémon"}'
```

```
data: {"type": "thinking",    "content": "Analysing... (turn 1)"}
data: {"type": "tool_call",   "tool": "get_schema",      "content": {}}
data: {"type": "tool_result", "tool": "get_schema",      "content": {...}}
data: {"type": "tool_call",   "tool": "find_entities",   "content": {...}}
data: {"type": "tool_result", "tool": "find_entities",   "content": [...]}
data: {"type": "text",        "content": "Fire-type Pokémon: Charmander, ..."}
data: {"type": "done"}
```

### `GET /mcp`

MCP (Model Context Protocol) endpoint. Any MCP-compatible client can connect and call the 9 ontology tools directly.

---

## MCP tools

| Tool | Layer | Description |
|---|---|---|
| `get_schema` | L1 | Class list with property counts (~30 tokens/class) |
| `get_class_detail` | L1 | Properties, parents, children, instance sample |
| `find_entities` | L1 | Filter instances by class + optional predicates |
| `describe_entity` | L1 | All properties and relationships of one entity |
| `count_entities` | L1 | Instance count for a class |
| `traverse_graph` | L1 | BFS from a node (outgoing / incoming / both) |
| `find_path` | L1 | Shortest path between two entities |
| `find_related` | L1 | Cross-class join via a predicate |
| `query_pattern` | L2 | JSON triple-pattern DSL → safe SPARQL translation |

---

## v0.3 — LLMs4OL: Ontology Learning from Text

v0.3 adds the **LLMs4OL pipeline** — an LLM reads plain text and proposes RDF triples that extend the live ontology. No manual authoring required.

### CLI commands

![learn --help](assets/learn_help.png)

### Task A — Term Typing (`type-term`)

Maps a text mention to the best-matching TBox class, with confidence scores and reasoning.

```bash
ontorag learn type-term "Pikachu" --context "evolved Pokémon"
ontorag learn type-term "React"
```

![learn type-term output](assets/learn_type_term.png)

### A+B+C Pipeline (`populate`)

Runs all three tasks in sequence — Term Typing → Taxonomy Discovery → Relation Extraction — then optionally loads the accepted triples into Fuseki.

```bash
ontorag learn populate examples/techstack/corpus.txt
```

![learn populate output](assets/learn_populate.png)

### Structured ABox Population (`populate-structured`) — v0.3.1

Reads a **CSV / JSON / JSONL** file, maps columns to TBox property URIs via LLM, and converts each row into RDF triples. The column mapping is cached in a sidecar `.mapping.json` file — subsequent runs reuse it without any LLM call.

```bash
# First run: LLM maps columns → saves pokemon.csv.mapping.json
ontorag learn populate-structured pokemon.csv \
    --class-uri pk:Pokemon --id-column name

# Second run: mapping reused, zero LLM calls
ontorag learn populate-structured pokemon.csv --yes

# JSON / JSONL (nested keys are flattened: {"stats":{"hp":35}} → "stats.hp")
ontorag learn populate-structured pokedex.jsonl --batch-size 100 --yes
```

![learn populate-structured output](assets/learn_populate_structured.png)

| Option | Default | Description |
|---|---|---|
| `--class-uri` | — | TBox class URI for each row (e.g. `pk:Pokemon`) |
| `--id-column` | — | Column to use as subject URI slug; uuid5 if omitted |
| `--batch-size` | 50 | Rows per LLM mapping call |
| `--min-confidence` | 0.7 | Minimum column-mapping confidence threshold |
| `--yes` | false | Skip Fuseki load confirmation prompt |

### Test suite — v0.3.1 (214 tests)

![v0.3.1 test results](assets/learn_tests.png)

---

## Example: Tech Stack ontology (v0.3 — LLMs4OL)

This example shows what a plain vector-search RAG cannot do.

**Step 1 — load a seed ontology** (15 technologies: React, Next.js, Node.js, TypeScript, …)

```bash
uv run ontorag load schema examples/techstack/schema.ttl
uv run ontorag load data   examples/techstack/data.ttl
```

**Step 2 — extend it from plain text** using the v0.3 LLMs4OL pipeline

```bash
# Feed a text corpus → LLM extracts types + relations → propose RDF triples
uv run ontorag learn populate examples/techstack/corpus.txt
```

**Step 3 — query the expanded graph** — OWL transitive reasoning included

```
> What does Next.js depend on?
```
Answer: Next.js → React → Node.js  
*(Next.js dependsOn Node.js was never written — Fuseki infers it via `owl:TransitiveProperty`.)*

```
> List all fullstack frameworks that depend on Vite
> Which tools supersede an existing technology?
> What technologies are maintained by Vercel?
```

See [`examples/techstack/README.md`](examples/techstack/README.md) for the full walkthrough.

---

## Example: Pokémon ontology

The bundled example exercises every feature of the framework.

```
examples/pokemon/
├── schema.ttl   # TBox: Pokemon, LegendaryPokemon, Type, Move, Trainer, Region
└── data.ttl     # ABox: Kanto region · 12 Pokémon · 3 Trainers · 18 Types
```

**Ontology highlights:**

- `pk:evolvesFrom` — declared `owl:TransitiveProperty`; Fuseki inference follows full chains
- `pk:LegendaryPokemon rdfs:subClassOf pk:Pokemon` — `find_entities(Pokemon)` includes Mewtwo automatically
- `strongAgainst` / `weakAgainst` — type effectiveness modelled as object properties

**Sample queries:**

```
> Show the full evolution chain of Venusaur
> Which Pokémon does Ash own?
> Find all Pokémon weak to Water type
> What are Mewtwo's stats?
```

![Pokemon evolution chain query](assets/pokemon_chat_en.png)

---

## LLM providers

| Provider | Key variable | Default model | Notes |
|---|---|---|---|
| **Anthropic** (default) | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | Best tool-use accuracy |
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o` | |
| **Ollama** | `OLLAMA_BASE_URL` | `llama3.1` | Local, no key needed |

---

## Docker

```bash
# Development — hot reload enabled
docker compose up

# Production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

| Service | Port | Notes |
|---|---|---|
| `fuseki` | 3030 | Apache Jena Fuseki; admin UI at `/dataset.html` |
| `api` | 8000 | ontorag FastAPI; OpenAPI at `/docs`, MCP at `/mcp` |

---

## Comparison

| Framework | Ontology | Agent | Notes |
|---|---|---|---|
| LangChain / LlamaIndex | Minimal | Yes | Code-first RAG, ontology is a plugin |
| Dify | None | Yes | Visual builder, no OWL support |
| GraphRAG (Microsoft) | Property graph from text | Yes | No OWL semantics — no `rdfs:subClassOf` inference, no `owl:TransitiveProperty`, no SPARQL; schema not enforced at query time |
| **ontorag** | **OWL-native** | **Yes** | TBox defines schema; Fuseki enforces OWL reasoning; v0.3 adds LLMs4OL (text → ontology extension) |

---

## Roadmap

- **v0.1** — Fuseki · Anthropic · OpenAI · Ollama · CLI · SSE streaming ✅
- **v0.2** — Web UI (Schema/Data/Playground) · RDF upload from browser · Rate-limit UX · Forced tool-use when ontology has data ✅
- **v0.3** — LLMs4OL: `ontorag learn` CLI (Term Typing · Taxonomy Discovery · Relation Extraction) · `type_term` + `extract_triples` MCP tools · Tech Stack example ✅
- **v0.3.1** (current) — Structured ABox population: `populate-structured` reads CSV/JSON/JSONL → maps columns to TBox via LLM → RDF triples → Fuseki; mapping cache, uuid5 idempotent URIs, batch checkpointing ✅
- **v0.5** — Neo4j + n10s adapter · `GRAPH_STORE` env var · Vector similarity tool (`find_similar`) · Multi-ontology support

---

## Contributing

```bash
# Set up dev environment
uv sync --extra dev

# Run tests
uv run pytest tests/ --cov=src/ontorag

# Run the API in dev mode
uv run ontorag serve --reload
```

---

## License

[MIT](LICENSE)
