# ontorag v1.2 — Multi-Agent vs Single-Agent Benchmark

The v1.2 milestone adds a multi-agent evaluator-optimizer loop on top of
the existing single-agent path. This document is the **comparison
contract**: it pins down what the new loop is measured against, how to
reproduce the measurement, and the result table that decides whether
v1.2 graduates from "experiment" to a tagged release.

**Decision rule** — v1.2 ships when the multi-agent baseline shows a
material improvement on at least one of *answer correctness* or
*citation completeness* on the multi-hop subset, **without** unacceptable
regression on simple-question latency or token cost.

---

## 1. What's being compared

Same Fuseki/Neo4j/FalkorDB store, same LLM (`gpt-4o-mini` or
`claude-haiku-4-5`), same prompt scaffolding, same cited-triple
recovery — only the chat loop differs:

| Baseline | Loop | Tools |
|---|---|---|
| `ontorag_native` | `AgentLoop` (single-agent, MAX_TURNS = 8) | 18 MCP tools |
| `ontorag_multiagent` | `MultiAgentLoop` (router + evaluator-optimizer, max_iter = 3) | same 18 MCP tools, called from inner AgentLoop instances |

Holding everything but the loop constant attributes any delta directly
to the v1.2 changes.

---

## 2. Multi-hop goldset

A new "정보 분산형" question set targets exactly the cases where a
single-pass loop tends to under-answer — multi-class joins, transitive
inference, top-N + filter, subClassOf-aware aggregation:

| Domain | File | Questions | Difficulty mix | uses_inference |
|---|---|---|---|---|
| Pokemon | `examples/pokemon/goldset_multihop.jsonl` | 15 | 5 medium · 10 hard | 5 / 15 |

The existing 5-domain factual goldset (130 q in `examples/*/goldset.jsonl`)
stays in the suite so the v1.2 loop is also measured for regression on
the "easy" path.

**Goldset quality (precondition, key-free)** — all 15 `gold_sparql`
statements parse and execute cleanly against the Pokemon schema + data
via `ontorag eval run` (rdflib in-memory, backend-agnostic). 6 queries
return 0 rows because they require IDs (e.g. `:Kanto`, `:Ash`) that the
demo dataset may not pin — by design, evaluation tolerates empty result
sets but flags them.

---

## 3. Result template (fill from `ontorag eval bench --with-ragas`)

The numbers below are placeholders — they get replaced by the actual
RAGAS-backed run defined in §4. The shape of the table is fixed so
two runs can be compared like-for-like.

### 3.1 Multi-hop goldset (15 q)

| Metric | `ontorag_native` | `ontorag_multiagent` | Δ |
|---|---|---|---|
| answer correctness (RAGAS) | _TBD_ | _TBD_ | _TBD_ |
| faithfulness (RAGAS) | _TBD_ | _TBD_ | _TBD_ |
| citation completeness | _TBD_ | _TBD_ | _TBD_ |
| hallucination | _TBD_ | _TBD_ | _TBD_ |
| avg tool calls / q | _TBD_ | _TBD_ | _TBD_ |
| avg latency / q | _TBD_ | _TBD_ | _TBD_ |
| avg LLM tokens / q | _TBD_ | _TBD_ | _TBD_ |

### 3.2 Existing factual goldset (130 q, pre-existing) — regression check

| Metric | `ontorag_native` (v1.1 reference) | `ontorag_multiagent` | Δ |
|---|---|---|---|
| answer correctness | _from BENCHMARK_RESULTS.md_ | _TBD_ | _TBD_ |
| avg latency / q | _from BENCHMARK_RESULTS.md_ | _TBD_ | _TBD_ |

### 3.3 v1.2-only signals (multi-agent only)

| Signal | Multi-hop | Factual |
|---|---|---|
| SIMPLE route % (short-circuit, zero overhead) | _TBD_ | _TBD_ |
| MULTI_STEP route % (evaluator loop activated) | _TBD_ | _TBD_ |
| avg iterations when MULTI_STEP | _TBD_ | _TBD_ |
| SUFFICIENT verdict % at iter 1 | _TBD_ | _TBD_ |

These last four come from `BaselineAnswer.extra` on the multi-agent run
— they explain *why* any quality delta exists.

---

## 4. Reproduce

Requires `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`) and a running graph
store. The harness is the same one v1.1 used for `BENCHMARK_RESULTS.md`,
just pointed at the new goldset and new baseline.

```bash
# Prerequisite: install bench extras and choose a backend
uv sync --extra bench
export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...
docker compose up -d fuseki                  # or --profile neo4j

# Multi-hop subset — main v1.2 comparison
for baseline in ontorag_native ontorag_multiagent; do
  uv run ontorag eval bench \
    examples/pokemon/goldset_multihop.jsonl \
    --schema examples/pokemon/schema.ttl \
    --data   examples/pokemon/data.ttl \
    --baseline $baseline \
    --lang ko \
    --with-ragas \
    --output results/v1.2/${baseline}_multihop.json
done

# Regression on existing factual goldset
for baseline in ontorag_native ontorag_multiagent; do
  uv run ontorag eval bench \
    examples/pokemon/goldset.jsonl \
    --schema examples/pokemon/schema.ttl \
    --data   examples/pokemon/data.ttl \
    --baseline $baseline \
    --lang ko \
    --with-ragas \
    --output results/v1.2/${baseline}_factual.json
done

# Compare two result files side-by-side
uv run ontorag eval compare \
  results/v1.2/ontorag_native_multihop.json \
  results/v1.2/ontorag_multiagent_multihop.json
```

To tune the multi-agent depth without rebuilding:

```bash
# The selector picks the right loop in the chat endpoint, but the
# baseline always uses MultiAgentLoop directly; iteration cap is
# adjustable via the baseline's max_iterations kwarg in code, or by
# patching the default in src/ontorag/chat/multi_agent/loop.py.
AGENT_MODE=multi  ontorag chat   # interactive REPL using the new loop
AGENT_MODE=single ontorag chat   # falls back to the v1.1 path
```

---

## 5. Honesty notes

- **`ontorag_multiagent` calls more LLM** — every iteration after the
  first is an extra full agent turn. The new "SIMPLE route %" signal
  bounds that overhead on the easy-question side.
- **Multi-hop set is 15 q** — large enough to surface a direction of
  effect, too small for tight confidence intervals. Treat any Δ < 0.05
  on a single metric as noise.
- **Pokemon-only for now** — extending the multi-hop goldset to
  commerce, ods, pure_land, techstack is a v1.2.1 follow-up if v1.2
  itself ships.
- **The evaluator's three axes are not validated separately** — the
  benchmark measures the *combined* effect of router + 3-axis
  evaluator + CRAG branching. Ablations (router-only, evaluator-only)
  are a v1.2.1 follow-up.
- **No live causal/Bayesian goldset in this benchmark** — IsSup is
  exercised by the unit tests (`tests/test_multi_agent_evaluator.py`)
  but not by the multi-hop run. Reasoning-layer evaluation lives in
  `examples/smoking/reasoning_goldset.jsonl` (v1.1, separate harness).

---

## 6. Implementation pointers

| Piece | Path |
|---|---|
| Complexity router (Adaptive-RAG style) | `src/ontorag/chat/multi_agent/router.py` |
| 3-axis evaluator (Self-RAG style) | `src/ontorag/chat/multi_agent/evaluator.py` |
| Loop + CRAG branching | `src/ontorag/chat/multi_agent/loop.py` |
| Selector (env-based routing) | `src/ontorag/chat/selector.py` |
| Eval baseline wrapper | `src/ontorag/eval/baselines/ontorag_multiagent.py` |
| Unit tests | `tests/test_multi_agent_{router,evaluator,loop,selector}.py` (60 tests) |
