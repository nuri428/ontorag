# Installation

ontorag is distributed via the source repository. Python **3.12+** and
[uv](https://docs.astral.sh/uv/) are required; uv is the project's package
manager of choice but `pip install -e .` works too.

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
uv sync                      # core (Fuseki backend, no reasoning)
```

That single command installs the FastAPI server, the agent loop, the 3
LLM providers, the Fuseki adapter, and the CLI. Everything past that is
**opt-in** — pick the extras you actually need:

## Extras matrix

| Extra | Pulls in | When to install |
|---|---|---|
| `bayes` | `pgmpy`, `pandas` (+ numpy/scipy) | Bayesian + Causal layers (`compute_posterior`, `do_query`, `counterfactual`). All `ontorag bayes` / `ontorag causal` commands. |
| `neo4j` | `neo4j>=5` async driver | When `GRAPH_STORE=neo4j`. Requires neosemantics (`n10s`) installed in the Neo4j container. |
| `falkordb` | `falkordb>=1.0` | When `GRAPH_STORE=falkordb`. v0.9 backend, RSAL license. |
| `vector` | `qdrant-client` | Fuseki + `find_similar` / `ontorag embed`. Neo4j uses native vector index; FalkorDB uses native `vecf32()` — both extra-free. |
| `mcp` | `mcp>=1.0` (official SDK) | Standalone stdio MCP server (`ontorag-mcp`) for Claude Desktop / Cursor / Claude Code. |
| `bench` | `langchain`, `ragas`, `chromadb`, `datasets` | LangChain baseline + RAGAS metrics in `ontorag eval bench --with-ragas`. Optional, key-required. |
| `dev` | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff` | Contributors only. |
| `docs` | `mkdocs-material`, `mkdocs-static-i18n`, `pymdown-extensions` | Build this documentation site (`mkdocs serve`). |

```bash
# Combine freely
uv sync --extra bayes --extra neo4j --extra vector
```

## Backends

Each backend ships as a docker-compose service with a health check.

=== "Fuseki (default)"

    Apache 2.0 · SPARQL 1.1 · ~200 MB image.

    ```bash
    docker compose up -d fuseki
    # no extra needed — bundled in core
    ```

=== "Neo4j + n10s"

    GPL / AGPL · Cypher · v0.5+.

    ```bash
    docker compose --profile neo4j up -d neo4j
    uv sync --extra neo4j
    export GRAPH_STORE=neo4j
    ```

    Note: `n10s` (neosemantics) is pre-installed in the compose image; without
    it RDF round-tripping breaks.

=== "FalkorDB"

    **RSAL** (Redis Source Available License — *not* OSI open source) ·
    Cypher · GraphBLAS-accelerated · v0.9+.

    ```bash
    docker compose --profile falkordb up -d falkordb
    uv sync --extra falkordb
    export GRAPH_STORE=falkordb
    ```

## LLM providers

```bash
# Anthropic (default)
ontorag config set --provider anthropic --api-key sk-ant-...

# OpenAI
ontorag config set --provider openai --api-key sk-... --model gpt-4o

# Ollama (local, no key)
ontorag config set --provider ollama --model llama3.1
```

Provider credentials are written to `.env` in the project root. `ontorag config
show` prints them with passwords masked.

## Verification

```bash
ontorag status
```

Reports backend health, loaded triple count, active LLM, and resolved
`GRAPH_STORE`. If anything is missing, the relevant line shows `unavailable`
with a hint.

## Next

- [Quickstart](quickstart.md) — first query in 5 minutes.
- [CLI reference](cli.md) — every subcommand.
- [Architecture](architecture.md) — how the pieces fit together.
