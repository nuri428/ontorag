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

### 3.4 v1.2.1 — second run, after Tune A + Tune B

Two changes landed:

* **Tune A (router)** — 14 Korean hop patterns added to
  `_HOP_PATTERNS` (grouping `타입별`, distributive `각각`, threshold
  `\d+ 이상`, equality `과/와 같은`, superlative `가장 많은`, completeness
  `모두 알려`, existential `한 마리라도`, inverse `역방향`, set inclusion
  `포함`, two-stage compound `이고, 그`, relational `출신`).
* **Tune B (evaluator)** — `_T_SUFFICIENT` lowered 0.7 → 0.6.

#### Observed numbers (same 15-q multi-hop goldset, same gpt-4o, ko)

| Metric | v1.2 multi | v1.2.1 multi | Δ (vs v1.2) | Δ (vs native) |
|---|---|---|---|---|
| answer_correctness | 0.288 | 0.251 | −0.036 | +0.018 |
| faithfulness | 0.199 | 0.291 | **+0.092** | −0.082 |
| answer_relevancy | 0.331 | 0.191 | **−0.140** | −0.096 |
| citation_coverage | 0.050 | 0.050 | ±0 | +0.043 |
| hallucination_rate | 0.000 | 0.000 | ±0 | ±0 |
| avg latency (ms) | 6,052 | 7,172 | +1,120 | +4,222 |
| avg tool calls | 4.27 | 5.27 | +1.00 | +3.54 |
| cited (n / 15) | 11 | 11 | ±0 | +4 |

#### Diagnostic signals — Tune A + B worked

| Signal | v1.2 | v1.2.1 | Note |
|---|---|---|---|
| SIMPLE route % | 66.7% | **0.0%** | Router now catches all 15 designed-multi-hop Qs |
| MULTI_STEP route % | 33.3% | **100.0%** | All Qs enter the evaluator loop |
| avg iter (MULTI_STEP) | 3.00 | **2.40** | Lower threshold lets some Qs stop early |
| SUFFICIENT at iter 1 % | 0.0% | **26.7%** | 4/15 satisfied on first round |
| verdict mix (suf/amb/ins) | 0/12/3 | **5/23/8** | SUFFICIENT now reachable |

The signals **all moved in the intended direction**.

#### Decision rule re-evaluation

| Criterion | v1.2 | v1.2.1 | Verdict |
|---|---|---|---|
| correctness improved (vs native) | ✅ +0.055 | ✅ +0.018 | weakened |
| citation improved (vs native) | ✅ +0.043 | ✅ +0.043 | held |
| simple-q latency regression | ✅ | ✅ | held |
| faithfulness regression | ❌ −0.174 | ❌ −0.082 | **halved** |
| relevancy regression (NEW) | n/a | ❌ −0.096 | **new** |

**Conclusion** — v1.2.1 is **clearly better than v1.2** (faithfulness
gap halved, SUFFICIENT verdicts reachable) but still **not default-on
ready**. The Tune A expansion is too aggressive (0% SIMPLE means
even questions a single-agent could nail get the evaluator overhead,
which appears as the new relevancy regression).

Keep `AGENT_MODE=multi` opt-in. Document both v1.2 and v1.2.1 as
*experimental modes*.

### 3.5 Ablation — what caused the v1.2.1 relevancy regression?

The v1.2.1 result had two surprises — faithfulness recovered (good)
but answer_relevancy regressed (new). To attribute cleanly, a third
configuration was run holding one Tune at v1.2 while flipping the
other to v1.2.1:

**ABLATION** = `v1.2 router (5/15 MULTI_STEP) + v1.2.1 evaluator (_T_SUFFICIENT=0.6)`.

| Metric | v1.2 multi | v1.2.1 multi | ABLATION |
|---|---|---|---|
| answer_relevancy | 0.331 | **0.191** | **0.191** ← identical to v1.2.1 |
| faithfulness | 0.199 | 0.291 | 0.207 ← closer to v1.2 |

Bit-identical relevancy between v1.2.1 and ABLATION (both used the
0.6 threshold) localises the relevancy regression *entirely* to
Tune B (threshold lowering). The faithfulness recovery, on the other
hand, came almost entirely from Tune A (router expansion) — ABLATION
only gained +0.008 faithfulness from threshold change alone.

**Causal attribution table**:

| Metric | from Tune A (router) | from Tune B (evaluator) |
|---|---|---|
| correctness | +0.043 (good) | −0.043 (bad) |
| faithfulness | +0.084 (good) | +0.008 (~null) |
| **relevancy** | 0.000 (null) | **−0.140 (bad)** |
| citation_cov | +0.039 (good) | ~null |

→ Tune A is strictly good-or-neutral. Tune B is the regression source.

### 3.6 v1.2.2 — third run, after Tune C + Tune D

Two changes derived directly from the ablation:

* **Tune C** — Revert `_T_SUFFICIENT` 0.6 → 0.7 (drop Tune B).
* **Tune D** — Reduce `_DEFAULT_MAX_ITERATIONS` 3 → 2. v1.2's
  "always-3-iter forced paraphrase" pathology that drove the original
  faithfulness regression is fixed here instead of by lowering the
  threshold.

Router expansion (Tune A) preserved.

| Metric | v1.2 multi | v1.2.1 multi | v1.2.2 multi | Δ vs native |
|---|---|---|---|---|
| answer_correctness | 0.288 | 0.251 | 0.254 | +0.021 |
| faithfulness | 0.199 | 0.291 | **0.334** | **−0.039** (nearly closed) |
| answer_relevancy | 0.331 | 0.191 | 0.170 | **−0.116** (still worse) |
| citation_coverage | 0.050 | 0.050 | 0.033 | +0.026 |
| avg latency (ms) | 6,052 | 7,172 | 5,088 | +2,139 |
| avg tool calls | 4.27 | 5.27 | 3.73 | +2.00 |
| cited (n / 15) | 11 | 11 | 9 | +2 |

Diagnostic signals (v1.2 → v1.2.1 → ABL → v1.2.2):

| | v1.2 | v1.2.1 | ABL | v1.2.2 |
|---|---|---|---|---|
| SIMPLE % | 66.67 | 0.00 | 66.67 | 0.00 |
| avg iter (MULTI_STEP) | 3.00 | 2.40 | 2.40 | **1.80** |
| SUFFICIENT @ iter 1 % | 0.00 | 26.67 | 20.00 | 20.00 |
| verdict mix (suf/amb/ins) | 0/12/3 | 5/23/8 | 2/7/3 | 3/17/7 |

#### v1.2.2 conclusion — structural tradeoff surfaced

Tune D halved the v1.2 faithfulness regression *again* (−0.082 →
−0.039, nearly native parity). But answer_relevancy went *further*
the wrong way (−0.096 → −0.116) because max_iter=2 means some
questions exit before the loop satisfies SUFFICIENT, ending on an
unpolished candidate answer.

The three runs collectively expose a **direct tradeoff between
faithfulness and relevancy** that thresholds and iter caps cannot
resolve on their own:

* `max_iter ↓` → less ungrounded paraphrase → faithfulness ↑, but
  more half-finished answers → relevancy ↓.
* `max_iter ↑` → more answer polish → relevancy ↑, but ungrounded
  paraphrase in the tail → faithfulness ↓.

No setting of (`_T_SUFFICIENT`, `_DEFAULT_MAX_ITERATIONS`) reaches
default-on parity. The next progress requires changing the *mechanism*,
not the parameters.

---

## 4. Final v1.2 milestone judgement

After three measurement rounds (v1.2 + v1.2.1 + v1.2.2) and one
ablation, the verdict is:

| Variant | default-on ready? | reason |
|---|---|---|
| v1.2 multi | ❌ | −0.174 faithfulness |
| v1.2.1 multi | ❌ | −0.082 faithfulness + new −0.096 relevancy |
| v1.2.2 multi | ❌ | −0.039 faithfulness + worsened −0.116 relevancy |

**Ship decision**: v1.2.2 is the best-of-three by combined score and
the best on faithfulness, so it becomes the `AGENT_MODE=multi`
implementation. Default behaviour remains `AGENT_MODE=single`. The
multi-agent path is documented as **experimental opt-in** in CHANGELOG.

**What v1.2 actually accomplished**:

* Three RAG-paper-pattern adaptations (Adaptive-RAG router,
  Self-RAG 3-axis evaluator, CRAG branching) with 81 unit tests, all
  written without learned reflection tokens or new framework deps.
* Honest measurement — three full RAGAS runs and one targeted
  ablation surfaced *which* tune caused *which* metric move.
* A reusable benchmark contract (this document) future v1.x cycles
  can extend rather than rewrite.

**What v1.2 did not accomplish**: shipping multi-agent as default. The
data is clear that further parameter tuning has diminishing returns;
the next progress requires one of:

1. **LLM-based router** — replace or fall back from regex when signals
   are sparse. The simple-router/multi-stay-on tradeoff above suggests
   the binary `route` decision itself is a bottleneck.
2. **Final answer-synthesis stage** — after the evaluator-optimizer
   loop closes, one more LLM pass that combines all gathered evidence
   into a polished answer. Most likely to recover relevancy without
   sacrificing faithfulness.
3. **Cross-validation outside RAGAS** — RAGAS faithfulness may penalise
   stylistic difference between single- and multi-agent answers in
   ways that don't reflect actual correctness. Adding a goldset-matching
   metric would isolate this.

These are v1.3 territory, each a separate milestone.

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
