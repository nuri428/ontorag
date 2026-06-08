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

## 3. Results — first run (2026-06-09, gpt-4o, ko)

15-question multi-hop goldset, Fuseki backend, RAGAS-backed.
Same store, same LLM, same schema context — only the chat loop changes.

### 3.1 Multi-hop goldset (15 q) — observed

| Metric | `ontorag_native` | `ontorag_multiagent` | Δ |
|---|---|---|---|
| answer_correctness (RAGAS) | 0.233 | 0.288 | **+0.055** |
| faithfulness (RAGAS) | 0.373 | 0.199 | **−0.174** ← regression |
| answer_relevancy (RAGAS) | 0.287 | 0.331 | +0.044 |
| citation_coverage | 0.007 | 0.050 | **+0.043** (~7×) |
| hallucination_rate | 0.000 | 0.000 | ±0.000 |
| avg latency (ms) | 2,949 | 6,052 | +3,103 (×2.05) |
| avg tool calls | 1.73 | 4.27 | +2.53 (×2.47) |
| cited (n / 15) | 7 | 11 | +4 (47% → 73%) |

### 3.2 v1.2-only diagnostic signals (multi-agent only)

| Signal | Value | What it tells us |
|---|---|---|
| SIMPLE route % | 66.7% (10/15) | Router classified most multi-hop q as "easy" — signal set too narrow |
| MULTI_STEP route % | 33.3% (5/15) | Only these reach the evaluator loop |
| avg iterations (MULTI_STEP) | **3.00** | Always hits max — no early termination |
| SUFFICIENT at iter 1 % | **0.0%** | Evaluator never satisfied first time |
| verdict mix | 0 suf / 12 amb / 3 ins | Thresholds too strict for this domain |

### 3.3 Verdict against decision rule

The decision rule in §0 was: *material improvement on correctness or
citation, without unacceptable regression on simple-question latency
or token cost.*

| Criterion | Outcome | Pass? |
|---|---|---|
| answer correctness improved | +0.055 (small) | ✅ marginal |
| citation completeness improved | +0.043 coverage, +26pp rate | ✅ strong |
| simple-question latency regression | SIMPLE route → single agent, zero extra cost | ✅ |
| **faithfulness regression** | **−0.174** | ❌ **fails** |

**Conclusion** — do NOT ship `ontorag_multiagent` as the v1.2.0 default.
Keep it opt-in via `AGENT_MODE=multi`. The citation gains are real but
the faithfulness regression is large enough to harm production trust.

### 3.4 Tuning targets surfaced by the diagnostic signals — for v1.2.1

The signals point at two specific tuning levers:

1. **Evaluator thresholds too strict.** 0/15 SUFFICIENT verdicts and a
   3.00 iteration average means `_T_SUFFICIENT=0.7` is unreachable
   on this domain. Each extra iteration adds ungrounded paraphrase that
   shows up as the faithfulness regression. *Lower the threshold (try
   0.6) or make it data-adaptive and re-run the same goldset.*
2. **Router signal set too narrow.** 66.7% SIMPLE on a goldset that was
   *designed* to be multi-hop means the heuristic is missing common
   Korean multi-hop phrasing — "타입별", "각각", "공유", "이상". *Add
   those signals to `router._HOP_PATTERNS` and re-run.*

Together these two tuning passes are the v1.2.1 milestone. Re-running
this benchmark after both is the gate to v1.2.0 default-on.

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
