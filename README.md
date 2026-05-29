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

## Why ontorag (vs. vector RAG)

Measured on identical TBox + ABox + goldsets — see [BENCHMARK_RESULTS.md](BENCHMARK_RESULTS.md) for the full run (LangChain + Chroma + OpenAI, 70 questions across 2 domains).

| Capability | Vector RAG (LangChain) | ontorag |
|---|---|---|
| Single-entity lookup | ✓ Commerce easy 5/5 | ✓ |
| Multi-hop / OWL transitive inference (`pl:locatedIn+`) | ✗ Q008/Q039/Q040 stopped at first hop | ✓ |
| Triple-level citation (auditable provenance) | ✗ 0 / 70 (structural — chunks only) | ✓ 30 / 70 cited |
| Hallucination measurability against ground truth | N/A (no triple-level grounding) | ✓ 0.000 hallucination rate |
| Refusal on KG-absent facts (trap questions) | ✓ Commerce 3/3 traps refused | ✓ |

Vector RAG handles flat lookups well — the structural advantage of ontorag appears on **transitive inference**, **provenance**, and **measurable grounding**.

**Speed:** the graph layer adds only ~1.5% of wall time — query latency is LLM-bound. See [Performance — agent latency profile](#performance--agent-latency-profile).

**Backend parity (v1.0):** the same protocol tools return **identical** results
across all three backends (Fuseki / Neo4j / FalkorDB) — schema, subclass-inferred
counts, aggregation, traversal, and the probabilistic/causal layers all match.
Measured, key-free, reproducible: [docs/BENCHMARK_v1.md](docs/BENCHMARK_v1.md)
(130-question goldset, 0 failures; 7/7 deterministic metrics at full parity).

---

## Key features

| Feature | Detail |
|---|---|
| **Ontology-first** | RDF/OWL schema (TBox) + instance data (ABox) as primary structure |
| **Agentic MCP loop** | LLM calls 18 typed MCP tools (L1 intent + L2 DSL + capability search/similar/aligned + probabilistic + causal); tool calls visible in SSE stream |
| **Full-text + vector retrieval** | BM25 `search_text` (jena-text / Neo4j fulltext) and graph-embedding `find_similar` (structural / textual / hybrid, with subClassOf-aware `class_uri` filter) — both backends |
| **Probabilistic + causal reasoning** | Bayesian network over the OWL graph: `compute_posterior` / `mpe` (v0.7) + Pearl Rung 2-3 `do_query` / `identify_effect` / `counterfactual` (v0.8) — pgmpy-native, `[bayes]` extra, both backends. Causal DAG is user-supplied (see [note](#causal-reasoning--over-claim-guard)). |
| **Web UI** | Built-in browser interface — Schema graph, Data browser, Playground chat at `/ui` |
| **Multi-LLM** | Anthropic Claude · OpenAI · Ollama (local) |
| **Pluggable backend** | `GRAPH_STORE=fuseki` (default) · `neo4j` (Neo4j + n10s) · `falkordb` (Cypher/GraphBLAS, v0.9) — same tools + full parity, no code change. |
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

> Next: pick a backend (below) · [Installation](#installation) ·
> [Configuration](#configuration) · [CLI reference](#cli-reference) ·
> [Web UI](#web-ui) · [MCP tools](#mcp-tools).

### Choosing a backend (Fuseki ⇄ Neo4j ⇄ FalkorDB — full parity)

`GRAPH_STORE` selects the backend. **All three expose the same CLI and MCP tools**,
including reasoning, full-text search, vector similarity, and the Bayesian/causal
layers — each backend just uses its own native tech:

| Capability | `GRAPH_STORE=fuseki` (default) | `GRAPH_STORE=neo4j` | `GRAPH_STORE=falkordb` |
|---|---|---|---|
| subClassOf reasoning | query-level SPARQL `subClassOf*` | Cypher `[:rdfs__subClassOf*]` | Cypher `[:rdfs__subClassOf*]` |
| `search_text` (BM25) | jena-text (Lucene) | native full-text index | native `db.idx.fulltext` |
| `find_similar` + `ontorag embed` | FastRP + embeddings → Qdrant | GDS FastRP + native vector index | FastRP + native vector index |
| RDF loading | SPARQL named graphs | n10s (neosemantics) | custom rdflib→Cypher loader |

> **FalkorDB license:** FalkorDB is **RSAL (Redis Source Available License)** —
> *not* OSI-approved open source, unlike Fuseki (Apache 2.0) and Neo4j (GPL/AGPL).
> Choose it for its GraphRAG/GraphBLAS performance positioning with that caveat in mind.

Run on Neo4j:

```bash
docker compose --profile neo4j up -d neo4j   # neo4j 5.26 + apoc + n10s + GDS

# Point ontorag at Neo4j — writes GRAPH_STORE + NEO4J_* to .env:
ontorag config set --graph-store neo4j \
  --neo4j-url bolt://localhost:7687 --neo4j-user neo4j --neo4j-password ontorag123
ontorag config show                            # verify backend + connection
# (or set GRAPH_STORE=neo4j and NEO4J_* directly in .env / the environment)
```

Vector search on **either** backend (Fuseki needs Qdrant + the `[vector]` extra):

```bash
docker compose --profile qdrant up -d qdrant   # only for GRAPH_STORE=fuseki
ontorag embed --mode both       # structural (FastRP) + textual (EMBEDDING_PROVIDER)
```

`find_similar(uri, top_k, mode)` modes: `structural` (graph topology), `textual`
(node text via OpenAI/Ollama), `hybrid` (reciprocal-rank fusion). `search_text`
and `find_similar` return ranked hits with scores; an optional `class_uri`
restricts to a class and its subclasses.

Run on FalkorDB (v0.9 — needs the `[falkordb]` extra):

```bash
docker compose --profile falkordb up -d falkordb   # falkordb/falkordb:latest, port 6379
ontorag config set --graph-store falkordb --falkordb-host localhost --falkordb-port 6379
ontorag config show
```

### Multiple ontologies in one instance

Load ontologies under an id and scope queries to one — or query the union:

```bash
ontorag load schema foaf.ttl   --ontology foaf
ontorag load data   people.ttl --ontology foaf
ontorag load schema gist.ttl   --ontology gist
```

Every read tool (`get_schema`, `find_entities`, `search_text`, `find_similar`, …)
plus `ontorag embed --ontology <id>` takes an optional `ontology` argument: pass
an id to isolate, omit it for the union of all ontologies (the default,
backward-compatible). Fuseki isolates with per-ontology named graphs; Neo4j tags
nodes with an `_ontology` property. A scoped `embed` only rebuilds that
ontology's vectors and leaves the others intact.

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

### Reasoning tab

Run the probabilistic and causal layers interactively (needs the `[bayes]` extra
and a Bayesian network loaded via `ontorag bayes load`). Two sub-tabs:

- **Bayesian** — build evidence (variable = state) and pick query variables, then
  compute `P(query | evidence)` (rendered as distribution bars) or the most
  probable explanation (`mpe`).
- **Causal** — with a DAG loaded (`ontorag causal load`), run `do(X)`
  interventions, `counterfactual` queries, and `identify` (back-door / front-door
  adjustment sets). The DAG edges are listed, and a **"do(X)로 비교 →"** link on a
  posterior result seeds the Causal tab with the same evidence as an intervention —
  the see ≠ do contrast in two clicks.

When no backend / network / `pgmpy` is available the tab renders an actionable
hint instead of an error.

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
   Apache Jena Fuseki  (SPARQL)  ← default, GRAPH_STORE=fuseki
   Neo4j + n10s        (Cypher)  ← v0.5, GRAPH_STORE=neo4j
   FalkorDB            (Cypher)  ← v0.9, GRAPH_STORE=falkordb
```

The diagram shows the L1/L2 core. The full MCP surface also includes the v0.5
**capability tools** (`search_text`, `find_similar`, `find_aligned`) and the
v0.7/v0.8 **reasoning tools** (`compute_posterior`, `mpe`, `do_query`,
`identify_effect`, `counterfactual`) — the latter compute over a Bayesian network
/ causal DAG held in dedicated named graphs (`urn:ontorag:probabilistic` /
`urn:ontorag:causal`). See [MCP tools](#mcp-tools) and
[Reasoning stack](#reasoning-stack--probabilistic--causal).

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
uv sync                      # core dependencies (Fuseki backend)

# optional extras — install only what you need:
uv sync --extra bayes        # v0.7/v0.8 probabilistic + causal reasoning (pgmpy + pandas)
uv sync --extra neo4j        # Neo4j + n10s backend driver (GRAPH_STORE=neo4j)
uv sync --extra falkordb     # FalkorDB backend client (GRAPH_STORE=falkordb, v0.9)
uv sync --extra vector       # Qdrant vector store (Fuseki find_similar)
```

Requires [uv](https://docs.astral.sh/uv/) and Docker. The Bayesian / causal tools
(`compute_posterior`, `do_query`, …) return HTTP 501 until the `bayes` extra is
installed.

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
| `GRAPH_STORE` | `fuseki` | Backend: `fuseki` · `neo4j` · `falkordb` |
| `FUSEKI_URL` | `http://localhost:3030` | SPARQL endpoint (`GRAPH_STORE=fuseki`) |
| `FUSEKI_DATASET` | `ontorag` | Dataset name |
| `NEO4J_URI` | `bolt://localhost:7687` | Bolt endpoint (`GRAPH_STORE=neo4j`) |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / — | Neo4j credentials |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database name |
| `FALKORDB_HOST` / `FALKORDB_PORT` | `localhost` / `6379` | FalkorDB endpoint (`GRAPH_STORE=falkordb`) |
| `FALKORDB_PASSWORD` / `FALKORDB_GRAPH` | — / `ontorag` | FalkorDB auth + graph key |
| `QDRANT_URL` | `http://localhost:6333` | Vector store for Fuseki `find_similar` |
| `EMBEDDING_PROVIDER` | `openai` | Textual embeddings: `openai` · `ollama` |
| `ONTOLOGY_ACCESS` | — | Per-ontology access lock, e.g. `poke:rw,shop:r,secret:none`; unset = fully open (default) |
| `FUSEKI_TIMEOUT` | `60` | Fuseki HTTP request timeout (s); `0`/empty = unbounded (v1.0) |
| `NEO4J_QUERY_TIMEOUT` | `30` | Neo4j per-query transaction timeout (s); `0`/empty = unbounded (v1.0) |
| `FALKORDB_QUERY_TIMEOUT` | `30` | FalkorDB per-query timeout (s); `0`/empty = unbounded (v1.0) |
| `LLM_TIMEOUT` | `60` | LLM request timeout (s) for all providers; `0`/empty = unbounded (v1.0) |

All of the above can be written to `.env` via `ontorag config set`
(`--graph-store`, `--neo4j-url/-user/-password`, `--qdrant-url`, …) and
inspected with `ontorag config show`.

---

## CLI reference

`ontorag <group> <command>` — 14 command groups. Run `ontorag <group> --help`
for full options.

| Group | Commands | What it does |
|---|---|---|
| `init` | — | Scaffold a project (docker-compose, .env.example, examples) |
| `load` | `schema` · `data` · `<FILE\|DIR>` (auto) | Load RDF (TBox/ABox); directory loader maps sub-dir = ontology id |
| `clear` | `schema` · `data` · `all` | Drop the TBox / ABox / both graphs |
| `dump` | `schema` · `data` · `all` | Export a graph (`-f ttl\|json\|jsonl\|xlsx`, `-o FILE`) |
| `embed` | — | Build graph embeddings for `find_similar` (`--mode structural\|textual\|both`) |
| `status` | — | Backend connection + triple counts |
| `serve` | — | Start the API + Web UI (`--host` `--port` `--reload`) |
| `chat` | — | Interactive ontology Q&A REPL |
| `history` | `list` · `show` · `delete` · `clear` | Manage saved chat conversations |
| `config` | `set` · `show` | Read/write `.env` (LLM, backend, `--*-timeout`) |
| `learn` | `type-term` · `taxonomy` · `extract` · `populate` · `populate-structured` · `derive-shapes` | LLMs4OL: text/CSV/JSON → RDF triples; OWL→SHACL skeleton |
| `eval` | `validate` · `run` · `report` · `bench` · `compare` | Goldset + RAGAS evaluation harness |
| `bayes` | `load` · `show` · `posterior` · `mpe` · `learn-cpt` · `clear` | Probabilistic layer (needs `[bayes]`) |
| `causal` | `load` · `show` · `do` · `identify` · `counterfactual` · `learn-dag` · `clear` | Causal layer, Pearl Rung 2-3 (needs `[bayes]`) |

```bash
# Load + inspect
ontorag load schema examples/pokemon/schema.ttl   # TBox
ontorag load data   examples/pokemon/data.ttl     # ABox (append; --replace to swap)
ontorag load        ./ontologies/                 # directory → one ontology per sub-dir
ontorag status

# Ontology learning from text (LLMs4OL)
ontorag learn populate corpus.txt --shapes shapes.ttl   # A+B+C pipeline + SHACL gate
ontorag learn populate-structured data.csv --class-uri pk:Pokemon --id-column name

# Probabilistic + causal reasoning  (uv sync --extra bayes)
ontorag bayes  posterior -q Cancer -e Smoking=yes       # P(query | see)  → 0.72
ontorag causal do        -d Smoking=yes -q Cancer       # P(query | do)   → 0.60
ontorag causal counterfactual -O Smoking=yes -O Cancer=yes -i Smoking=no -q Cancer

# Retrieval extras
ontorag embed --mode both                               # then find_similar via /tools/similar
ontorag dump all -f ttl -o export.ttl
```

---

## REST API

System endpoints: `GET /health` (liveness), `GET /status` (backend connection +
triple counts), `POST /load` (upload RDF), `GET /dump?target=…&format=…` (export
TTL/JSON/JSONL/XLSX). All tool routes under `/tools/*` are also plain REST and
auto-exposed as MCP tools at `/mcp`. Any unhandled error returns a structured
`{detail, type}` 500 (v1.0).

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

MCP (Model Context Protocol) endpoint. Any MCP-compatible client can connect and call the 18 ontology / reasoning tools directly (see [MCP tools](#mcp-tools)).

---

## MCP tools

| Tool | Layer | Description |
|---|---|---|
| `get_schema` | L1 | Class list with property counts (~30 tokens/class) |
| `get_class_detail` | L1 | Properties, parents, children, instance sample |
| `find_entities` | L1 | Filter instances by class + optional predicates |
| `describe_entity` | L1 | All properties and relationships of one entity |
| `count_entities` | L1 | Instance count for a class |
| `aggregate` | L1 | Group instances by a property → count / sum / avg / min / max |
| `traverse_graph` | L1 | BFS from a node (outgoing / incoming / both); follows `owl:TransitiveProperty` closure when a predicate is given |
| `find_path` | L1 | Shortest path between two entities |
| `find_related` | L1 | Cross-class join via a predicate |
| `query_pattern` | L2 | JSON triple-pattern DSL → safe SPARQL translation |
| `search_text` | Cap | BM25 full-text (jena-text / Neo4j fulltext) — both backends |
| `find_similar` | Cap | Graph-embedding kNN (structural / textual / hybrid) + subClassOf-aware `class_uri` filter — both backends |
| `find_aligned` | Cap | `owl:sameAs` closure — entities asserted equivalent across ontologies (transitive + symmetric) |
| `compute_posterior` | Prob | P(query \| evidence) over the Bayesian network (v0.7) |
| `mpe` | Prob | Most probable explanation — best joint assignment given evidence (v0.7) |
| `do_query` | Causal | P(query \| do(X), evidence) — interventional, graph surgery + back-door adjustment (v0.8, Rung 2) |
| `identify_effect` | Causal | Back-door / front-door adjustment sets + identifiability for treatment → outcome (v0.8) |
| `counterfactual` | Causal | P(query \| observed, had(X)) — canonical-SCM abduction-action-prediction (v0.8, Rung 3) |

"Cap" = backend capability tool, available once the data is loaded
(`search_text`) or embeddings are built (`find_similar` via `ontorag embed`).
"Prob" / "Causal" require the `[bayes]` extra and a Bayesian network loaded via
`ontorag bayes load` (causal tools also use a DAG from `ontorag causal load`).

### Causal reasoning — over-claim guard

The causal DAG is **user-supplied**. ontorag computes interventional /
counterfactual queries *assuming the DAG is correctly specified*; it does **not**
validate causal semantics or discover causation. Structure discovery
(`ontorag causal learn-dag`) recovers a Markov-equivalence class and emits a
**proposal only** — never auto-committed; a human must review before any causal
claim. See [`docs/design/causal-layer.md`](docs/design/causal-layer.md).

---

## Ontology learning from text (LLMs4OL)

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

### Structured ABox Population (`populate-structured`)

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

### SHACL validation gate

LLM-generated triples can be *syntactically* valid yet *semantically* wrong: HP=99999, six types on one Pokémon, ISO currency = "dollar". v0.5 adds an optional **SHACL validation step** between LLM output and Fuseki load — violating triples are filtered out and surfaced as `PopulationResult.violations`.

#### Why SHACL beyond OWL?

OWL's `rdfs:range pk:Type` is an *inference hint*, not a constraint. The following triple is *legal* under pure OWL:

```turtle
pk:Pikachu pk:hasType pk:Fire, pk:Water, pk:Grass, pk:Electric, pk:Ice, pk:Rock .
```

Six types is impossible in the game, but OWL has no `sh:maxCount`. SHACL fills that gap.

#### Step 1 — derive a skeleton from the OWL schema

The mechanical 80% comes for free. Given this fragment in `schema.ttl`:

```turtle
pk:hp a owl:DatatypeProperty ;
    rdfs:domain pk:Pokemon ; rdfs:range xsd:integer .
pk:hasType a owl:ObjectProperty ;
    rdfs:domain pk:Pokemon ; rdfs:range pk:Type .
pl:vowNumber a owl:DatatypeProperty, owl:FunctionalProperty ;
    rdfs:domain pl:Vow ; rdfs:range xsd:integer .
```

run:

```bash
ontorag learn derive-shapes examples/pokemon/schema.ttl -o examples/pokemon/shapes.ttl
```

and get this back (real output, abridged):

```turtle
pk:PokemonShape a sh:NodeShape ;
    sh:targetClass pk:Pokemon ;
    sh:property [ sh:path pk:hp ;     sh:datatype xsd:integer ] ,
                [ sh:path pk:hasType ; sh:class pk:Type ; sh:nodeKind sh:IRI ] .

pl:VowShape a sh:NodeShape ;
    sh:targetClass pl:Vow ;
    sh:property [ sh:path pl:vowNumber ; sh:datatype xsd:integer ; sh:maxCount 1 ] .
```

The derivation follows three mechanical mappings:

| OWL idiom | SHACL constraint |
|---|---|
| `rdfs:range xsd:T`            | `sh:datatype xsd:T` |
| `rdfs:range <Class>`          | `sh:class <Class>` + `sh:nodeKind sh:IRI` |
| `a owl:FunctionalProperty`    | `sh:maxCount 1` |

#### Step 2 — refine with domain knowledge

The remaining 20% is constraints OWL can't express — enumerations, value ranges, cardinality > 1. Hand-edit `shapes.ttl`:

```turtle
pk:PokemonShape a sh:NodeShape ;
    sh:targetClass pk:Pokemon ;
    sh:property [
        sh:path pk:hasType ;
        sh:class pk:Type ; sh:nodeKind sh:IRI ;
        sh:maxCount 2 ;                                       # ← added: game rule
        sh:message "포켓몬은 최대 2개의 타입만 가질 수 있다." ;
    ] ;
    sh:property [
        sh:path pk:hp ;
        sh:datatype xsd:integer ;
        sh:minInclusive 1 ; sh:maxInclusive 999 ;             # ← added: balance
    ] .

pk:MoveShape a sh:NodeShape ;
    sh:targetClass pk:Move ;
    sh:property [
        sh:path pk:category ;
        sh:in ( "Physical" "Special" "Status" ) ;             # ← added: enum
    ] .
```

#### Step 3 — validate during population

Pass the shapes to either populate command:

```bash
ontorag learn populate corpus.txt \
    --shapes examples/pokemon/shapes.ttl

ontorag learn populate-structured pokemon.csv \
    --class-uri pk:Pokemon --id-column name \
    --shapes examples/pokemon/shapes.ttl
```

When triples violate a shape, the CLI shows what was caught:

```
✓ 38개 트리플을 ABox에 로드했습니다. ← pokemon.csv
⚠ SHACL 위반으로 4건 제외됨.
```

#### Step 4 — inspect violations from Python

The CLI shows the count; the SDK gives full detail:

```python
from ontorag.learn.pipeline import LLMOntologyLearner

result = await learner.populate_from_structured(
    "pokemon.csv",
    class_uri="pk:Pokemon",
    id_column="name",
    auto_load=True,
    shapes_path="examples/pokemon/shapes.ttl",
)

print(f"Loaded: {result.triples_loaded}")
for v in result.violations:
    print(f"  {v.focus_node}")
    print(f"    path: {v.result_path}")
    print(f"    msg:  {v.message}")
    print(f"    severity: {v.severity}")
```

Typical output when LLM hallucinates an HP value:

```
Loaded: 38
  http://example.org/pokemon/MewTwo
    path: http://example.org/pokemon#hp
    msg:  HP는 1-999 범위의 정수여야 한다.
    severity: Violation
```

#### Pre-authored shapes for all five example domains

| Domain | What `shapes.ttl` enforces |
|---|---|
| `examples/pokemon/shapes.ttl`    | max 2 types, HP ∈ [1, 999], Move category ∈ {Physical, Special, Status} |
| `examples/techstack/shapes.ttl`  | `firstReleased` is `xsd:gYear`, `maintainedBy` is an IRI, single homepage |
| `examples/ods/shapes.ttl`        | chapter ∈ [1, 14], every Complexity has exactly one bigO string |
| `examples/pure_land/shapes.ttl`  | vowNumber ∈ [1, 48], contemplationOrder ∈ [1, 16] |
| `examples/commerce/shapes.ttl`   | ISO currency code = `^[A-Z]{3}$`, foundedYear ∈ [1000, 2100], non-negative employeeCount |

### Test suite

As of v1.0 the suite is **910 unit tests** (deselect-`integration`) plus live
integration tests per backend — all gated in CI on every push/PR (see
`.github/workflows/test.yml`). The screenshot below is the v0.3.1 learning-module
run.

![learning-module test results](assets/learn_tests.png)

---

## Reasoning stack — Probabilistic + Causal

ontorag layers a reasoning stack over the OWL graph. Each tier answers a
different *kind* of question:

| Layer | Question | Tools |
|---|---|---|
| Logical (RDFS+) | *Is X necessarily true?* | `subClassOf*` · `inverseOf` · `TransitiveProperty` (shipped) |
| **Probabilistic (v0.7)** | *How likely is X?* | `compute_posterior` · `mpe` |
| **Causal (v0.8)** | *What if we **do** Y? What if Y **had been** different?* | `do_query` · `identify_effect` · `counterfactual` |

Both upper layers are **pgmpy-native** (the `[bayes]` extra), store their models
in dedicated named graphs (`urn:ontorag:probabilistic` / `urn:ontorag:causal`,
never the schema/data graphs), and return **identical results on both backends**
(Fuseki + Neo4j — verified by integration tests). Designs:
[`bayesian-layer.md`](docs/design/bayesian-layer.md) ·
[`causal-layer.md`](docs/design/causal-layer.md).

### Probabilistic layer (Bayesian)

A Bayesian network is authored in the `bn:` vocabulary (variables + CPTs as RDF)
or **learned from ABox data**, then queried for posteriors / most-probable
explanation.

```bash
ontorag bayes load examples/pokemon/bayes-network.ttl
# variables/states accept the full URI or the exact rdfs:label ("Battle outcome")
ontorag bayes posterior -q https://ontorag.dev/pokemon#Outcome      # P(Outcome=win) → 0.53
ontorag bayes posterior -q https://ontorag.dev/pokemon#TypeMatchup \
                        -e https://ontorag.dev/pokemon#Outcome=win  # P(advantage | win) → 0.604
ontorag bayes mpe                                                   # → (advantage, win)
```

**Results** — synthetic Pokémon BN (TypeMatchup → Outcome, prior `.4/.3/.3`,
`P(win | adv/neu/dis) = .8/.5/.2`), hand-computed and asserted in
`tests/test_bayes_engine.py`:

| Query | Result | Hand-check |
|---|---|---|
| `P(Outcome=win)` | **0.53** | `.4·.8 + .3·.5 + .3·.2` |
| P(TypeMatchup=advantage \| win) | **0.604** | `.32 / .53` |
| `MPE()` | **(advantage, win)** | joint `.32` is the max |

**CPT learning from data** — feed `pk:Battle` observations (`matchupKind` →
`battleResult`), then `learn-cpt` estimates the tables (pgmpy BDeu). On 150
observations (advantage 45/5, neutral 25/25, disadvantage 5/45):

| Learned parameter | Value | Data |
|---|---|---|
| P(win \| advantage) | **0.897** | 45/50 (BDeu-smoothed) |
| P(lose \| disadvantage) | **0.897** | 45/50 |

### Causal layer (Pearl Rung 2-3)

A user-supplied causal DAG (`causal:` vocabulary), optionally with latent
confounders, sits over the quantified BN. `do` separates *seeing* from *doing*.

```bash
ontorag bayes  load examples/smoking/bayes-network.ttl       # quantify (CPTs)
ontorag causal load examples/smoking/causal-model.ttl        # causal DAG
ontorag bayes  posterior -q Cancer -e Smoking=yes            # see → 0.72
ontorag causal do        -d Smoking=yes -q Cancer            # do  → 0.60
ontorag causal counterfactual -O Smoking=yes -O Cancer=yes -i Smoking=no -q Cancer
```

**Results** — synthetic smoking BN with an **observed genotype confounder**
(Genotype → Smoking, Genotype → Cancer, Smoking → Cancer), hand-verified in
`tests/test_causal_engine.py`:

| Query | Result | Reading |
|---|---|---|
| P(Cancer=yes \| **see** Smoking=yes) | **0.72** | confounded upward by genotype |
| P(Cancer=yes \| **do** Smoking=yes) | **0.60** | back-door adjusted over Genotype |
| `identify` Smoking → Cancer | backdoor **`{Genotype}`** | effect is identifiable |
| counterfactual: smoked + got cancer, *had not smoked* | **0.28** | between interventional `0.20` and observed `0.72` |

The `0.72 ≠ 0.60` gap is the whole point: plain conditioning over-credits
smoking because genotype drives both smoking *and* cancer; `do` cuts the
Genotype → Smoking edge and removes the confounding.

**Structure discovery** — `learn-dag` runs the PC algorithm over ABox
observations and **proposes** a DAG (never auto-committed — a Markov-equivalence
class, so some edge directions are undetermined by data). On 150 `pk:Battle`
observations it recovers `TypeMatchup → Outcome`. PC is a statistical
independence test: a borderline dependence in a small sample can sit below the
significance threshold — tune `--significance` and review before `--save`.

> **Over-claim guard.** The causal DAG is **user-supplied**. ontorag computes
> interventional / counterfactual queries *assuming the DAG is correctly
> specified*; it does **not** validate causal semantics or discover causation.

---

## Example: Tech Stack ontology (LLMs4OL)

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
| **ontorag** | **OWL-native** | **Yes** | TBox defines schema; Fuseki enforces OWL reasoning; v0.3 LLMs4OL (text → ontology extension); **v0.7-0.8 probabilistic + causal reasoning** (Bayesian posteriors, Pearl Rung 2-3 `do` / counterfactual) over the graph |

---

## Evaluation Harness — `ontorag eval`

A built-in evaluation harness for comparing ontorag against vector RAG
baselines on benchmark goldsets. Available on the `eval-harness` branch.

### What it provides

- **Two benchmark domains** — `examples/pure_land/` (50 questions, fictional+religious — Sukhāvatī cosmology with multilingual labels) and `examples/commerce/` (20 questions, schema.org real vocabulary with fictional companies)
- **Goldset format** — JSONL with `gold_sparql`, `gold_answer`, `gold_triples`, `uses_inference` per question. Pydantic-validated.
- **5 metrics** — `sparql_result_equivalent`, `inference_utilization`, `hallucination_rate`, `citation_coverage`, plus RAGAS (`faithfulness`, `answer_correctness`, `answer_relevancy`)
- **Baselines** — `ontorag_mock` (perfect retrieval upper bound), `vector_rag_mock` (lossy 70/20/10 bucket simulation), `langchain` (real RetrievalQA + Chroma + OpenAI — `--extra bench` + API key)
- **Markdown reports** — automatic generation suitable for PR comments / blog posts
- **CI integration** — GitHub Actions matrix runs both domains on every PR; report uploaded as artifact + posted as sticky comment

### Commands

```bash
# Validate a goldset
uv run ontorag eval validate examples/commerce/goldset.jsonl

# Run gold_sparql against schema+data (data hygiene check)
uv run ontorag eval run examples/commerce/goldset.jsonl \
    --schema examples/commerce/schema.ttl \
    --data examples/commerce/data.ttl \
    --output report.json

# End-to-end bench with a baseline + metrics
uv run ontorag eval bench examples/commerce/goldset.jsonl \
    --baseline ontorag_mock \
    --schema examples/commerce/schema.ttl \
    --data examples/commerce/data.ttl \
    --output ontorag.json

# Side-by-side comparison Markdown
uv run ontorag eval compare ontorag.json langchain.json \
    --name-a ontorag --name-b langchain \
    --output comparison.md

# Markdown report from JSON
uv run ontorag eval report ontorag.json --output report.md
```

### Real LangChain baseline + RAGAS (~$1 / run)

```bash
uv sync --extra bench
export OPENAI_API_KEY=sk-...

uv run ontorag eval bench examples/commerce/goldset.jsonl \
    --baseline langchain \
    --schema examples/commerce/schema.ttl \
    --data examples/commerce/data.ttl \
    --with-ragas \
    --output langchain_real.json
```

See [`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md) for the full
multi-iteration history and an honest accounting of what is and isn't
proven.

---

## Benchmark results — 4-domain RAGAS final (2026-05)

We ran a head-to-head benchmark across **four ontology domains** with
both **agent = `gpt-4o`** and **judge = `gpt-4o`** (RAGAS LLM-as-judge).
The two baselines compared are:

| Baseline | What it does |
|---|---|
| `langchain` | Classic vector RAG — Chroma index over TTL chunks + OpenAI embeddings + `gpt-4o` RetrievalQA. No graph reasoning. |
| `ontorag_native` | ontorag's own agent loop — `gpt-4o` calling 9 ontology-aware MCP tools backed by Apache Jena Fuseki with OWL inference. |

### The four domains — designed to cover different OWL feature mixes

| Domain | Questions | Lang | OWL feature mix | LLM contamination |
|---|---|---|---|---|
| **Pokemon** | 20 | Korean | 1 TransitiveProperty (`evolvesFrom`), small ABox (~50 instances) | Very high — every frontier LLM was pre-trained on Gen-1 Pokemon |
| **Techstack** | 20 | Korean | 1 TransitiveProperty (`dependsOn`), small ABox (15 technologies) | Very high — React/Node.js/TypeScript everywhere |
| **ODS** (Open Data Structures) | 20 | English | 2 TransitiveProperty (`uses`, `specialises`) + 1 inverseOf pair (`implements` ↔ `implementedBy`) | High — Pat Morin's open textbook |
| **Pure Land** | 50 | Korean | TransitiveProperty (`locatedIn`) + multilingual labels (`@ko/@zh-Hant/@en`) + large ABox (717 triples) | Low — Sukhāvatī cosmology, fictional+religious |

The four domains were deliberately chosen to vary along two axes — how
**OWL-feature-rich** the ontology is, and how much the LLM has already
**seen the answers during pre-training**.

> **Attribution & disclaimer policy.** Every example dataset in this
> repository has a uniform `## Disclaimer` section near the bottom of
> its `README.md` covering four items: (1) **rights** — who owns the
> trademarks / copyrights / source material referenced; (2) **nature
> of this work** — fan-made fair use, CC-attributed derivative, or
> original modeling against public-domain sources; (3) **no
> affiliation** with the rights holders; (4) **takedown commitment**
> with a contact path. Per-domain summary:
> *Pokemon* — trademarks of The Pokémon Company / Nintendo /
> Creatures Inc. / Game Freak Inc., fan-made educational example
> ([details](examples/pokemon/README.md#disclaimer));
> *Techstack* — technology names are trademarks of Meta / Google /
> Vercel / OpenJS Foundation / Microsoft etc., used under nominative
> fair use ([details](examples/techstack/README.md#disclaimer));
> *ODS* — CC BY 2.5 derivative with attribution to Pat Morin
> ([details](examples/ods/README.md#disclaimer));
> *Pure Land* — original modeling against public-domain canonical
> sūtras, with separate doctrinal disclaimer
> ([details](examples/pure_land/README.md#disclaimer)).

### The numbers

For each (domain, baseline) pair we measured three RAGAS LLM-as-judge
metrics (Faithfulness, AnswerCorrectness, AnswerRelevancy) plus two
deterministic metrics (Hallucination rate from SPARQL evidence,
Citation provision rate). Values in **bold** are the within-domain
winner.

| Domain | Baseline | Faithfulness | Correctness | Relevancy | Hallucination | Citation% |
|---|---|---|---|---|---|---|
| Pokemon | LangChain | **0.677** | 0.448 | 0.342 | — | 0% |
| Pokemon | ontorag_native | 0.423 | **0.466** | **0.349** | **0.000** | **65%** |
| Techstack | LangChain | **0.808** | **0.523** | **0.420** | — | 0% |
| Techstack | ontorag_native | 0.333 | 0.382 | 0.279 | **0.000** | **45%** |
| ODS | LangChain | 0.521 | 0.493 | 0.641 | — | 0% |
| ODS | ontorag_native | **0.551** | **0.515** | **0.749** | **0.000** | **65%** |
| Pure Land | LangChain | 0.345 | 0.260 | 0.180 | — | 0% |
| Pure Land | ontorag_native | **0.422** | **0.381** | **0.357** | **0.000** | **66%** |

### How to read the table — three findings

#### Finding 1. LLM-judge Faithfulness has a chunk-quote style bias

In Pokemon and Techstack, LangChain wins Faithfulness by a large margin
(0.677 and 0.808). This is **not** evidence that LangChain produces
truer answers — it's evidence that the RAGAS judge rewards answers
whose wording overlaps with the retrieved chunks. LangChain literally
quotes the source TTL text, so the overlap is high.

ontorag_native, by contrast, **runs SPARQL and translates the result
into a fluent answer**. The factual content can be identical, but the
phrasing diverges from the source — so the judge penalizes it.

You can confirm this is a *style* difference rather than a *factual*
difference by looking at the next two metrics: in Pokemon, ontorag's
**AnswerCorrectness is actually higher** (0.466 vs 0.448) and so is
**AnswerRelevancy** (0.349 vs 0.342). Same facts, different style.

#### Finding 2. The richer the OWL feature set, the bigger ontorag's edge

Compare the four domains by how many independent OWL features the TBox
exercises:

| Domain | TransitiveProperty | inverseOf | Multilingual | Domains ontorag wins (5 metrics) |
|---|---|---|---|---|
| Pokemon | 1 | ✗ | ✗ | 3 of 5 |
| Techstack | 1 | ✗ | ✗ | 2 of 5 |
| ODS | 2 | ✓ | ✗ | 5 of 5 |
| Pure Land | 1 | ✓ | ✓ | 5 of 5 |

When the ontology only exercises **one axis of OWL inference**
(Pokemon, Techstack), vector RAG's chunk-quote advantage holds on the
style metrics. When the ontology exercises **two or more axes** (ODS's
two TransProps + inverseOf; Pure Land's transitive locatedIn +
multilingual labels + large ABox), graph reasoning starts winning
*every* RAGAS metric — including Faithfulness.

The most dramatic case is Pure Land. AnswerRelevancy jumps from 0.180
(LangChain) to 0.357 (ontorag), a **98% relative improvement**. Why?
Because the question and the gold answer can be in different
languages, and the URI links them — but the vector index sees them as
unrelated chunks.

##### Decision grid — where each domain sits

Plotting the four domains on a 2×2 of *OWL richness* × *LLM
contamination* makes the trade-off visible at a glance:

```
                           OWL richness  →
                      low                       high
                  ┌──────────────────┬──────────────────┐
                  │                  │                  │
        low       │                  │   ★ Pure Land    │
                  │                  │   ★ ODS          │
                  │                  │                  │
  contamination   ├──────────────────┼──────────────────┤
                  │                  │                  │
        high      │   ★ Pokemon     │   ★ Techstack   │
                  │                  │                  │
                  └──────────────────┴──────────────────┘
                  ontorag             ontorag wins every
                  Correctness/        RAGAS metric +
                  Relevancy ↑,        always wins
                  LangChain           Hallucination 0%
                  Faithfulness ↑      and Citation 45-66%
```

Pure Land sits in the **upper-right** alongside ODS — its TransProp +
inverseOf + multilingual labels make it OWL-rich, and its
fictional+religious cosmology makes it the least-contaminated of the
four. That cell is where ontorag's advantage is largest (Pure Land
AnswerRelevancy +98% relative; ODS Relevancy +17% absolute).

The **lower-right** (Techstack) is the *adversarial* cell for ontorag
on RAGAS scores — high contamination plus a small ABox plays directly
to the chunk-quote style bias. The **lower-left** (Pokemon) is the
*split decision* cell: LangChain wins style metrics, ontorag wins
factual metrics. The **upper-left** is intentionally empty — that
combination (low OWL features + low contamination) is rare in
practice; if you're not exercising graph reasoning *and* the LLM
hasn't memorized your domain, you probably just need a Q&A bot, not a
RAG stack.

#### Finding 3. Hallucination 0% and Citation 45-66% — only ontorag

Look at the rightmost two columns: across **all four domains**,
ontorag has a **Hallucination rate of exactly 0.000** and Citation
provision rates between 45% and 66%. LangChain shows "—" in both — and
this needs an explanation, because "—" is **not** the same as "0".

The two metrics are *deterministic* (not LLM-judged): the harness
parses each answer for explicit triple/URI citations, then checks each
cited triple against the actual ABox.

* `citation_provided_rate` = "did the answer cite anything?"
* `citation_coverage` = "of those citations, what fraction match a real
  ABox triple?"
* `hallucination_rate` = "of those citations, what fraction reference
  triples that do **not** exist in the ABox?"

All three require step 1 — **the answer must emit citations** — before
step 2 can compute anything. LangChain's `RetrievalQA` chain feeds
chunks to the LLM as prompt context and returns prose only; it never
emits RDF triples. So:

* `citation_provided_rate = 0%` is a real measurement (the baseline
  produced no citations on any of the 110 questions).
* `hallucination_rate = "—"` is **"not measurable"**, not "zero". With
  no citations to verify, the falsifiable test has no input — saying
  the rate is 0 would be a false claim of safety.

ontorag's agent loop, by contrast, runs SPARQL through MCP tools and
attaches the result triples as evidence inside the answer. The harness
extracts and verifies them, which is why the same columns *do*
populate for ontorag — and they all clear the bar (0 hallucinations,
45-66% of answers cited at least one triple).

**This is the structural moat that does not depend on the LLM judge.**
If your application is in a domain where producing a confident wrong
answer is expensive — legal, medical, scholarly KGs, multi-locale
catalogs — this is where the value lives. Each goldset includes 20%
**trap questions** (entities the LLM has seen in pre-training but that
are absent from the ontology, e.g. *Eevee* / *Vue.js* / *SplayTree* /
*Mew*); ontorag refuses to answer instead of hallucinating, because
SPARQL returns empty rows and the agent honors that.

> **Want a comparable proxy for LangChain?** RAGAS Faithfulness in
> column 3 is the closest LLM-judged equivalent — but it gives you a
> *fuzzy similarity score*, not a *falsifiable yes/no* check against
> the actual graph.

### Practical takeaway

| If your domain is... | Pick |
|---|---|
| Heavy contamination + small ABox + style/quote matters | **LangChain** wins RAGAS scores; ~$0.45/run |
| Rich OWL features (≥2 TransProps, inverseOf, or multilingual) | **ontorag** wins every RAGAS metric |
| Hallucination cost > retrieval cost (legal/medical/scholarly) | **ontorag** — it refuses instead of fabricating |
| You need to point at the exact triple that produced an answer | **ontorag** — only baseline that cites |

Full per-question breakdown and the v2→v9 iteration history are in
[`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md). Per-domain analyses
live in each `examples/<domain>/README.md`.

### Re-run with the expanded agent tool-set (2026-05)

The benchmark above was produced with the **9-tool** agent. v0.5.x wired
three more tools into the agent loop — BM25 `search_text`, vector
`find_similar` (with a subClassOf-aware `class_uri` filter), and
`aggregate` — and added the Neo4j backend. A re-run on four domains
(`ontorag_native` only, `gpt-4o` agent **and** judge, English questions on
Fuseki) confirms the pattern holds with the richer toolset:

| Domain | Q | Correctness | Faithfulness | Relevancy | Citation% | Hallucination |
|---|--:|--:|--:|--:|--:|--:|
| Commerce | 20 | 0.525 | 0.556 | 0.729 | 75% | **0.000** |
| ODS | 20 | 0.556 | 0.577 | 0.768 | 65% | **0.000** |
| Techstack | 20 | 0.480 | 0.545 | 0.761 | 55% | **0.000** |
| Pure Land | 50 | 0.493 | 0.355 | 0.775 | 74% | **0.000** |

**Hallucination is exactly 0.000 in all four domains** — the structural
moat from Finding 3 holds with the larger toolset. Numbers run a touch
higher than the canonical table for the shared domains because this re-run
uses **English questions aligned to the English/Sanskrit labels** (the
canonical table uses each domain's native question language), plus the
extra retrieval tools. Trap questions score low on Correctness *by design*:
the agent correctly refuses ("X is not in the data"), and the judge scores
refusal phrasing poorly against the gold refusal — so **0% hallucination,
not Correctness, is the trap-handling signal**. Backend parity was
separately confirmed on Pokémon (Fuseki 0.735 vs Neo4j 0.699 Correctness
with the `class_uri` filter active).

### Reproducing

```bash
docker compose up -d                           # Fuseki
cp .env.example .env && edit OPENAI_API_KEY    # OpenAI key required
echo "LLM_MODEL=gpt-4o"            >> .env     # agent model
echo "RAGAS_JUDGE_MODEL=gpt-4o"    >> .env     # judge model (opt-in)

# For each domain — clear graph, reload, run two baselines:
uv run ontorag load schema examples/pokemon/schema.ttl
uv run ontorag load data   examples/pokemon/data.ttl

uv run ontorag eval bench examples/pokemon/goldset.jsonl \
    --baseline langchain      --schema examples/pokemon/schema.ttl \
    --data examples/pokemon/data.ttl --lang ko --with-ragas \
    --output examples/pokemon/bench_results/langchain_gpt4o.json

uv run ontorag eval bench examples/pokemon/goldset.jsonl \
    --baseline ontorag_native --schema examples/pokemon/schema.ttl \
    --data examples/pokemon/data.ttl --lang ko --with-ragas \
    --output examples/pokemon/bench_results/ontorag_native_gpt4o.json
```

Approximate cost: ~$7-9 for the full 4-domain × 2-baseline run with
`gpt-4o` on both agent and judge.

---

## Performance — agent latency profile

Quality is one axis; speed is another. A separate benchmark
(`scripts/bench_query_speed_4domain.py`) profiles where wall-clock time goes in
the agent loop — the same four domains, 20 questions each (80 total), agent =
`gpt-4o`, against a warm Fuseki.

| Domain | wall p50 | wall mean | wall p95 | LLM share | tool calls / Q | prompt tok / Q | prompt-cache hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| pokemon   | 1477 ms | 1601 ms | 2219 ms | 98.6% | 1.10 | 5,550  | 77.9% |
| techstack | 1573 ms | 1744 ms | 2512 ms | 98.3% | 1.15 | 5,502  | 79.6% |
| ods       | 1633 ms | 1876 ms | 2486 ms | 98.4% | 1.30 | 6,172  | 80.9% |
| pure_land | 1650 ms | 1844 ms | 2740 ms | 98.7% | 1.05 | 10,031 | 71.9% |

**Tool execution (SPARQL over HTTP to Fuseki) is ~1.5% of wall time — a median
of 21 ms per question.** Most questions resolve in a single tool call. Latency
is dominated by LLM round-trips (98.5%), so the practical levers are round-trip
count (bounded by `MAX_TURNS`) and model choice — not the graph layer. Because
the 20 questions in a domain share the same schema prompt prefix, OpenAI prompt
caching warms to 72–81%, keeping per-query cost low when one ontology is queried
repeatedly.

> Numbers are a single run on one machine (`gpt-4o`, local Fuseki); absolute
> latency varies with model, hardware, and network. The **shape** — LLM-bound,
> graph layer negligible — is the durable result.

Reproduce:

```bash
FUSEKI_DATASET=ontorag uv run python scripts/bench_query_speed_4domain.py --n 20
```

---

## Roadmap

- **v0.1** — Fuseki · Anthropic · OpenAI · Ollama · CLI · SSE streaming ✅
- **v0.2** — Web UI (Schema/Data/Playground) · RDF upload from browser · Rate-limit UX · Forced tool-use when ontology has data ✅
- **v0.3** — LLMs4OL: `ontorag learn` CLI (Term Typing · Taxonomy Discovery · Relation Extraction) · `type_term` + `extract_triples` MCP tools · Tech Stack example ✅
- **v0.3.1** — Structured ABox population: `populate-structured` reads CSV/JSON/JSONL → maps columns to TBox via LLM → RDF triples → Fuseki; mapping cache, uuid5 idempotent URIs, batch checkpointing ✅
- **v0.3.2** — TBox/ABox dump: `ontorag dump schema|data|all` · `GET /dump` REST endpoint · Web UI download buttons · TTL / JSON / JSONL / XLSX formats ✅
- **v0.4** — Evaluation harness: 4 benchmark domains (Pure Land 50q · Commerce 20q · ODS 20q · Pokemon 20q · Techstack 20q) · Goldset JSONL + Pydantic loader · 4 deterministic metrics + RAGAS wrapper · LangChain + ontorag_native baselines · `ontorag eval` CLI (validate/run/bench/compare/report) · GitHub Actions matrix CI · `BenchRunner` orchestrator · 4-domain `gpt-4o` agent + `gpt-4o` judge results · 2×2 OWL-richness × contamination decision grid · standardized `## Disclaimer` policy across all example READMEs ✅
- **v0.5** — Neo4j + n10s adapter · `GRAPH_STORE=fuseki|neo4j` · **full backend parity** (OWL `rdfs:subClassOf` reasoning · BM25 `search_text` · graph-embedding `find_similar`, each backend's native tech) · L2 `query_pattern` → Cypher · multi-ontology per instance (`ontology` scope, named-graphs / node tags) · per-ontology embedding scoping ✅
- **v0.5.x** — agent now wields the full **13-tool set** (BM25 `search_text`, vector `find_similar`, `aggregate` wired into the agent loop, previously MCP-route-only) · `find_similar` subClassOf-aware `class_uri` filter (both backends) · fixes: Neo4j predicate-`traverse` Cypher, `[bench]` extra dependency pins ✅
- **v0.6** — directory/multi-file loader (`load <DIR>` + `ontorag.yaml` manifest) · agent 14-tool set · `find_similar` `class_uri` filter · CLI backend config (`config set --graph-store/--neo4j-*`) · Web UI search/similar/aggregate panels ✅
- **v0.6.1** — **per-ontology access control** (config-driven read/write/none via `ONTOLOGY_ACCESS`, GraphStore-boundary wrapper) · **cross-ontology entity alignment** (`owl:sameAs` closure → `find_aligned`) · `load_rdf` pre-parsed-graph fast path ✅
- **v0.7** — **Probabilistic layer (Bayesian)**: named-graph foundation (`OntologyLayer`, layer/ontology graph URIs) · `bn:` vocabulary + RDF round-trip · `BayesianStore` (Fuseki + Neo4j parity) · `BayesianEngine` (pgmpy) → `compute_posterior` / `mpe` MCP tools · `ontorag bayes` CLI incl. `learn-cpt` (CPT estimation from ABox). pgmpy is the `[bayes]` extra. ✅
- **v0.8** — **Causal layer (Pearl Rung 2-3)**: `causal:` vocabulary + `urn:ontorag:causal` graph (Fuseki + Neo4j parity) · `CausalEngine` (pgmpy-native, **not DoWhy**) → `do_query` (intervention + back-door adjustment) · `identify_effect` (back-door/front-door sets) · `counterfactual` (canonical-SCM) MCP tools · PC structure discovery (`learn-dag`, proposal-only) · `ontorag causal` CLI · smoking confounder example (`do` 0.60 ≠ `see` 0.72). Causal DAG is user-supplied; ontorag does not validate causal semantics. ✅
- **v0.8.4** — **Reasoning WebUI**: single `🧮 Reasoning` tab with Bayesian / Causal sub-tabs (HTMX) · evidence/query → posterior/mpe · do / observed / query → do_query / counterfactual / identify_effect · DAG view + "do(X) vs see" cross-link · Playwright E2E 16/16. ✅
- **v0.9** — **FalkorDB backend** (3rd, Cypher/GraphBLAS): `stores/falkordb.py` reuses the Neo4j L1 + reasoning mixins; **custom rdflib→Cypher loader** (no n10s) reproducing SHORTEN/LABELS_AND_NODES/ARRAY · native `db.idx.fulltext` (`_fulltext` scalar shadow) + native vector index (pure-Python FastRP) · full protocol + capability parity (schema/entities/traversal/search/similar/bayes/causal), 11 live integration tests identical to Fuseki/Neo4j. **RSAL-licensed** (not OSI open source). ✅
- **v1.0** — **Production-Ready & Proven**: configurable query/LLM timeouts on all backends (`*_TIMEOUT` env) · global structured-500 exception handler (no traceback leak) · CI gate runs ruff + the full unit suite (910) on every push/PR + a Neo4j/FalkorDB integration job · **3-backend deterministic benchmark** ([docs/BENCHMARK_v1.md](docs/BENCHMARK_v1.md): 130-q goldset 0 failures, 7/7 protocol metrics at full parity). GNN/learning layer deferred to v1.1+. ✅

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
