# MCP & Tools

ontorag exposes its ontology-aware capabilities through the **Model Context
Protocol**. Two transports are available, both backed by the same handler code
(`create_store()` + `GraphStore` protocol + reasoning engines):

| Transport | Endpoint | Use case |
|---|---|---|
| **HTTP / SSE** (built-in) | `http://localhost:8000/mcp` | when ontorag is already running as a FastAPI server |
| **stdio** (v1.1, `[mcp]` extra) | `ontorag-mcp` console script | drops into Claude Desktop / Cursor / Claude Code without running a server |

## The 18 tools

Tools are organised in 3 layers — the LLM only sees **L1 + L2**. Raw SPARQL
(L3) is developer-only.

### L1 — intent-based (the 90% case)

| Tool | Purpose |
|---|---|
| `get_schema` | Compact class + property hierarchy (~30 tokens/class). |
| `get_class_detail` | Drill into one class — properties, parents, children, sample instances. |
| `find_entities` | Class + filters → instance list (subclass-inferred). |
| `describe_entity` | All properties + relations of one entity (inverseOf included). |
| `count_entities` | Instance count with optional filters. |
| `aggregate` | `group_by` + agg (`count`/`sum`/`avg`/`min`/`max`). |
| `traverse_graph` | Graph walk (TransitiveProperty respected). |
| `find_path` | Shortest path between two entities. |
| `find_related` | Multi-hop join between two classes. |
| `search_text` | BM25 full-text (jena-text / Neo4j fulltext / FalkorDB fulltext). |
| `find_similar` | Vector kNN (structural FastRP + textual + RRF hybrid). |
| `find_aligned` | `owl:sameAs` transitive + symmetric closure (cross-ontology). |
| `type_term` (v0.3) | Map a text mention to a TBox class. |
| `extract_triples` (v0.3) | Propose RDF triples from text, validated against schema. |
| `compute_posterior` (v0.7) | Bayesian P(Q \| E) via pgmpy. |
| `mpe` (v0.7) | Most-probable explanation. |
| `do_query` (v0.8) | Pearl Rung 2 interventional — graph surgery + back-door adjustment. **Returns adjustment set + "why" trace** (v1.1). |
| `identify_effect` (v0.8) | Minimal back-door + all front-door adjustment sets. |
| `counterfactual` (v0.8) | Pearl Rung 3 — abduction · action · prediction. |

### L2 — JSON DSL escape hatch (10% case)

`query_pattern` — JSON triple patterns translated into safe SPARQL/Cypher
internally. Injection-safe by construction.

### L3 — raw SPARQL (developer-only, NOT exposed)

`query_sparql_raw` lives in the API for curl-style debugging but is
`exclude_operations`-listed so it never reaches the LLM.

## stdio MCP — Claude Desktop / Cursor config

After `uv sync --extra mcp`:

=== "Claude Desktop"

    `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

    ```json
    {
      "mcpServers": {
        "ontorag": {
          "command": "ontorag-mcp",
          "env": {
            "GRAPH_STORE": "fuseki",
            "FUSEKI_URL": "http://localhost:3030/ontorag"
          }
        }
      }
    }
    ```

=== "Cursor"

    `~/.cursor/mcp.json`:

    ```json
    {
      "mcpServers": {
        "ontorag": {
          "command": "ontorag-mcp",
          "env": { "GRAPH_STORE": "neo4j",
                   "NEO4J_URL": "bolt://localhost:7687",
                   "NEO4J_USER": "neo4j",
                   "NEO4J_PASSWORD": "***" }
        }
      }
    }
    ```

=== "Claude Code"

    Add to `~/.claude.json` under `mcpServers` (same shape as above).

Restart the client, then the ontorag tools appear in the tool palette. The
stdio server exposes the high-value read tools + `compute_posterior` +
`do_query`; raw SPARQL is excluded (same policy as HTTP `/mcp`).

## Verifying

```bash
# A quick end-to-end check
ontorag-mcp <<<'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Inside Claude Desktop / Cursor, ask:

> *List all Pokémon, then compute the posterior of BattleOutcome given the opponent type is Water.*

The client will call `find_entities` → `compute_posterior` and stream the
results back.

## Backend swap

Every tool above works **identically on all three backends** — change
`GRAPH_STORE` and restart. The v1.0 benchmark proves 7/7 protocol metrics
match across Fuseki / Neo4j / FalkorDB; see
[`docs/BENCHMARK_v1.md`](https://github.com/nuri428/ontorag/blob/main/docs/BENCHMARK_v1.md).
