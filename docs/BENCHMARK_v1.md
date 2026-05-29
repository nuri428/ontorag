# ontorag v1.0 — Benchmark & Backend-Parity Evidence

Two deterministic, **no-LLM-key** measurements back the v1.0 claims:

1. **Goldset quality** — every benchmark question's `gold_sparql` executes
   cleanly against its schema+data (rdflib, backend-agnostic).
2. **Backend parity** — the same protocol tools return **identical** results on
   all three backends (Fuseki, Neo4j, FalkorDB). This is ontorag's headline
   differentiator, now measured rather than asserted.

Both are reproducible from a clean checkout; commands are at the bottom.

## 1. Goldset quality (5 domains, 130 questions)

`ontorag eval run` parses each domain's TTL into an in-memory rdflib graph and
executes every `gold_sparql`. **0 failures across all 130 questions.**

| Domain | Questions | Failures | Inference-dependent q |
|---|---|---|---|
| pokemon | 20 | 0 | 6 |
| commerce | 20 | 0 | 3 |
| ods | 20 | 0 | 5 |
| pure_land | 50 | 0 | 3 |
| techstack | 20 | 0 | 6 |
| **total** | **130** | **0** | **23** |

This is a *data-hygiene* check (goldset is internally consistent), independent of
which graph store serves queries at runtime.

## 2. Backend parity (deterministic protocol tools)

Pokémon schema+data loaded into each backend via the normal order
(`clear → load schema → load data`), then the deterministic L1 tools run with no
LLM involvement. **Every metric is identical across all three backends.**

| Metric | Fuseki | Neo4j | FalkorDB | Parity |
|---|---|---|---|---|
| `get_schema` classes | 6 | 6 | 6 | ✅ |
| `get_schema` properties | 20 | 20 | 20 | ✅ |
| `count_entities(Pokemon)` (subClassOf-inferred) | 13 | 13 | 13 | ✅ |
| `count_entities(LegendaryPokemon)` | 1 | 1 | 1 | ✅ |
| `aggregate(hasType)` groups | 8 | 8 | 8 | ✅ |
| `aggregate(hasType)` total | 18 | 18 | 18 | ✅ |
| `traverse(Pikachu, depth 2)` nodes | 42 | 42 | 42 | ✅ |

**`full_parity = True`** — each backend uses its own native tech (Fuseki:
query-level SPARQL `subClassOf*`; Neo4j: Cypher `[:rdfs__subClassOf*]` via n10s;
FalkorDB: the same Cypher over the custom rdflib→Cypher loader), yet the
observable results are the same. `count_entities(Pokemon)=13` includes the one
`LegendaryPokemon` (Mewtwo) on all three — i.e. OWL subclass inference is live
and consistent everywhere.

The probabilistic/causal layers were separately verified identical across
backends (P(Cancer|see)=0.72, P(Cancer|do)=0.60, counterfactual=0.28) — see
`docs/design/bayesian-layer.md` / `causal-layer.md`.

## Scope & honesty notes

- **Deterministic only.** RAGAS / LLM-as-judge metrics (faithfulness,
  answer-correctness) need an LLM key + cost; they are available via
  `ontorag eval bench --with-ragas` but are *not* part of this key-free v1.0
  evidence. Prior real RAGAS runs (Fuseki, gpt-4o-mini) live in
  `BENCHMARK_RESULTS.md`.
- **Reasoning layers** now have a goldset too: `examples/smoking/reasoning_goldset.jsonl`
  (6 hand-verified posterior / do / counterfactual / identify checks) runs via
  `ontorag eval reasoning <goldset>` against the stored BN + causal DAG on any
  backend — e.g. P(Cancer|see)=0.72, P(Cancer|do)=0.60, counterfactual=0.28,
  back-door set {Genotype}. All 6 pass.
- **Known sharp edge (not a v1.0 blocker):** on Neo4j/FalkorDB, re-loading with
  `replace=True` for *both* schema and data in succession can drop property-type
  declarations (schema and data share one physical graph). The normal load order
  (`clear → schema → data`, no `replace`) is unaffected and is what the parity
  run uses.

## Reproduce

```bash
# 1. goldset quality (no backend, no key)
for d in pokemon commerce ods pure_land techstack; do
  uv run ontorag eval run examples/$d/goldset.jsonl \
    --schema examples/$d/schema.ttl --data examples/$d/data.ttl
done

# 2. backend parity — start the three backends, then load + compare.
#    Fuseki: docker compose up -d fuseki
#    Neo4j:  docker compose --profile neo4j up -d neo4j
#    FalkorDB: docker compose --profile falkordb up -d falkordb
#    For each backend, set GRAPH_STORE + creds, then:
#      ontorag clear all && ontorag load schema examples/pokemon/schema.ttl \
#        && ontorag load data examples/pokemon/data.ttl && ontorag status
#    and compare get_schema / count_entities / aggregate / traverse outputs.
```
