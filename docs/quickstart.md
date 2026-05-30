# Quickstart

Install, load an example ontology, and ask the LLM agent a natural-language
question in **under 5 minutes**.

## 1. Install

ontorag is distributed via the source repository (no PyPI yet).

```bash
git clone https://github.com/nuri428/ontorag.git
cd ontorag
uv sync                      # core (Fuseki backend)
```

Optional extras — install only what you need:

```bash
uv sync --extra bayes        # Bayesian + Causal reasoning (pgmpy)
uv sync --extra neo4j        # Neo4j + n10s backend driver
uv sync --extra falkordb     # FalkorDB backend client
uv sync --extra mcp          # standalone stdio MCP server
uv sync --extra vector       # Qdrant vector store (Fuseki find_similar)
uv sync --extra docs         # this documentation site
```

## 2. Start a graph backend

=== "Fuseki (default)"

    ```bash
    docker compose up -d fuseki
    ```

=== "Neo4j + n10s"

    ```bash
    docker compose --profile neo4j up -d neo4j
    export GRAPH_STORE=neo4j
    ```

=== "FalkorDB"

    ```bash
    docker compose --profile falkordb up -d falkordb
    export GRAPH_STORE=falkordb
    ```

## 3. Load an example ontology

```bash
ontorag load schema examples/pokemon/schema.ttl    # TBox
ontorag load data   examples/pokemon/data.ttl      # ABox
ontorag status                                     # verify the triples loaded
```

## 4. Configure an LLM provider

```bash
# Anthropic (default)
ontorag config set --provider anthropic --api-key sk-ant-...

# OpenAI
ontorag config set --provider openai --api-key sk-... --model gpt-4o

# Ollama (local, no key)
ontorag config set --provider ollama --model llama3.1
```

## 5. Ask the agent

```bash
ontorag serve                # FastAPI on :8000 — open http://localhost:8000/ui
# or REPL
ontorag chat
```

Try:

- *"List all Pokémon."*
- *"What types is Pikachu strong against?"*
- *"Show me everything related to Mewtwo."*

The agent picks MCP tools (`get_schema`, `find_entities`, `traverse_graph`, …)
and you can watch every tool call in the SSE stream.

## 6. (Optional) Probabilistic + Causal

If you installed `--extra bayes`:

```bash
ontorag bayes load   examples/pokemon/bayes.ttl
ontorag bayes posterior \
    --evidence "OpponentType=Water" \
    --query    "BattleOutcome"

ontorag causal load        examples/smoking/causal.ttl
ontorag causal do          --do "Smoking=yes" --query "Cancer"
ontorag causal counterfactual \
    --observed "Smoking=yes,Cancer=yes" \
    --do       "Smoking=no" \
    --query    "Cancer"
```

The smoking example reproduces the textbook *see ≠ do* gap:
**P(Cancer | see Smoking) = 0.72** vs **P(Cancer | do Smoking) = 0.60**.

## Next steps

- [CLI reference](cli.md) — every subcommand.
- [MCP & Tools](mcp.md) — wire ontorag into Claude Desktop / Cursor.
- [Reasoning](reasoning.md) — the Bayesian + Causal layer in depth.
