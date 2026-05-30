# CLI Reference

`ontorag` exposes 14 command groups. Every command is built on Typer + Rich
(progress bars + colour) and respects the active `GRAPH_STORE` backend.

## Quick map

| Group | What it does |
|---|---|
| `ontorag load` | Load RDF (TTL / JSON-LD / RDF-XML / directory) into the active backend. |
| `ontorag clear` | Wipe a named graph or the entire store. |
| `ontorag config` | Set / inspect LLM provider, model, API keys, backend URLs. |
| `ontorag status` | Show backend health + loaded triple counts + LLM config. |
| `ontorag serve` | Start the FastAPI app (REST + `/ui` + `/mcp`). |
| `ontorag chat` | Terminal REPL against the agent. |
| `ontorag learn` | LLMs4OL ontology learning (type-term / taxonomy / extract / populate). |
| `ontorag map` | Tabular → RDF mapping (CSV / JSON / JSONL → triples). |
| `ontorag embed` | Build structural + textual graph embeddings (`find_similar`). |
| `ontorag eval` | Goldset evaluation (`run` / `bench` / **`reasoning`** v1.1). |
| `ontorag bayes` | Bayesian layer — `load` / `show` / `posterior` / `mpe` / `learn-cpt` / `clear`. |
| `ontorag causal` | Causal layer — `load` / `show` / `do` / `identify` / `counterfactual` / `learn-dag` / `clear`. |
| `ontorag shacl` | SHACL validation against the loaded data. |
| `ontorag-mcp` | Standalone **stdio** MCP server (Claude Desktop / Cursor entrypoint). |

## Loading data

```bash
# Schema (TBox) vs data (ABox)
ontorag load schema ./ontology.ttl
ontorag load data   ./instances.ttl

# Auto-detect (file)
ontorag load ./combined.ttl

# Directory loader
#   sub-directory name = ontology id, schema files load before data
ontorag load ./ontologies/
ontorag load ./ontologies/ --ontology my-onto --replace --no-recursive
```

A `ontorag.yaml` manifest (optional) can override the default sub-dir → ontology
mapping. See `core/manifest.py`.

## Configuration

```bash
# Set provider + model + key
ontorag config set --provider anthropic --api-key sk-ant-...
ontorag config set --provider openai    --model gpt-4o

# Backend (also configurable via env)
ontorag config set --graph-store neo4j \
    --neo4j-url bolt://localhost:7687 \
    --neo4j-user neo4j --neo4j-password ***

# Inspect (passwords masked)
ontorag config show
```

## Reasoning CLI

### Bayesian (`uv sync --extra bayes`)

```bash
ontorag bayes load        ./bayes.ttl
ontorag bayes show
ontorag bayes posterior   --evidence "OpponentType=Water" --query "BattleOutcome"
ontorag bayes mpe         --evidence "OpponentType=Water"
ontorag bayes learn-cpt   --data-class Pokemon
ontorag bayes clear
```

### Causal

```bash
ontorag causal load              ./causal.ttl
ontorag causal show
ontorag causal do                --do "Smoking=yes" --query "Cancer"
ontorag causal identify          --treatment "Smoking" --outcome "Cancer"
ontorag causal counterfactual    --observed "Smoking=yes,Cancer=yes" \
                                 --do       "Smoking=no" \
                                 --query    "Cancer"
ontorag causal learn-dag         --save         # proposal-only — never auto-committed
ontorag causal clear
```

!!! warning "Over-claim guard"
    The causal DAG is **user-supplied**. ontorag computes interventional and
    counterfactual queries assuming the DAG is correctly specified — it does
    not validate causal semantics or discover causation. Structure learning
    (`learn-dag`) emits proposals only.

## Evaluation

```bash
ontorag eval run examples/pokemon/goldset.jsonl \
    --schema examples/pokemon/schema.ttl \
    --data   examples/pokemon/data.ttl

ontorag eval bench  examples/pokemon/goldset.jsonl

# v1.1 — reasoning-layer goldset (posterior / do / counterfactual / identify)
ontorag eval reasoning examples/smoking/reasoning_goldset.jsonl
```

## stdio MCP server

```bash
uv sync --extra mcp
ontorag-mcp                  # spawned by the MCP client over stdio
```

See [MCP & Tools](mcp.md) for the client config snippet.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GRAPH_STORE` | `fuseki` | `fuseki` / `neo4j` / `falkordb` |
| `FUSEKI_URL` | `http://localhost:3030/ontorag` | SPARQL endpoint |
| `FUSEKI_TIMEOUT` | `60` (s) | HTTP timeout — `0` = unbounded |
| `NEO4J_URL` | `bolt://localhost:7687` | Neo4j bolt |
| `NEO4J_QUERY_TIMEOUT` | `30` (s) | Per-query — `0` = unbounded |
| `FALKORDB_URL` | `redis://localhost:6379` | FalkorDB redis |
| `FALKORDB_QUERY_TIMEOUT` | `30` (s) | Per-query — `0` = unbounded |
| `LLM_PROVIDER` / `LLM_MODEL` | — | overrides config |
| `LLM_TIMEOUT` | `60` (s) | LLM HTTP — `0` = unbounded |
| `EMBEDDING_PROVIDER` | — | `openai` / `ollama` for textual embeddings |
| `ONTOLOGY_ACCESS` | unset (open) | `poke:rw,shop:r,secret:none` per-ontology scope-lock |

`env_timeout()` (`core/config.py`) parses these — number/unset → default,
`0` → unbounded, malformed → default + warn.
