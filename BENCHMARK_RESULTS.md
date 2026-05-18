# Benchmark Results — Phase B (mock simulation)

> **Honest disclaimer**: these numbers are from **deterministic mock
> baselines**, not live LLM API calls. They demonstrate the evaluation
> harness end-to-end and show what shape the comparison will take when
> real LangChain + OpenAI numbers are produced. They are *not* a claim
> about real-world ontorag vs LangChain performance.
>
> See **How to reproduce with real APIs** below.

---

## What was actually measured

Two mock baselines were run against the two Phase B goldsets:

| Mock | Behaviour | Cites triples? |
|---|---|---|
| `ontorag_mock` | Perfect retrieval — runs `gold_sparql`, returns gold answer + supporting triples | **Yes** |
| `vector_rag_mock` | Deterministic per-question bucket: 70 % correct / 20 % hallucinated / 10 % "I don't know" | **No** (chunks only) |

Combined: 4 bench runs (2 domains × 2 baselines) + 2 comparison files.

## Headline numbers

### Pure Land (50 questions, 948 triples)

| Metric | ontorag_mock | vector_rag_mock |
|---|---:|---:|
| Avg latency (ms) | 180 | 420 |
| Avg tool calls | 1.06 | 0.00 |
| Avg hallucination rate | **0.000** | *(N/A — no triples)* |
| Avg citation coverage | 0.010 | *(N/A)* |
| Citation provided (count / rate) | **21 / 50 (42 %)** | **0 / 50 (0 %)** |
| Total prompt tokens | 26 040 | 26 000 |
| Total completion tokens | 4 000 | 3 000 |

### Commerce (20 questions, 297 triples)

| Metric | ontorag_mock | vector_rag_mock |
|---|---:|---:|
| Avg latency (ms) | 180 | 420 |
| Avg tool calls | 1.15 | 0.00 |
| Avg hallucination rate | **0.000** | *(N/A)* |
| Avg citation coverage | 0.225 | *(N/A)* |
| Citation provided (count / rate) | **9 / 20 (45 %)** | **0 / 20 (0 %)** |
| Total prompt tokens | 8 190 | 10 400 |
| Total completion tokens | 1 600 | 1 200 |

### What this tells us (about the mock)

* **Citation availability is the structural differentiator.** Vector
  RAG cannot cite triples at all — its "citations" are text chunks, not
  KG facts. ontorag produces triple-level citations for ~40–45 % of
  questions (the others are aggregations / count queries that have no
  single citation by design).
* **Hallucination at the triple level is measurable only for ontorag.**
  Because vector RAG provides no structured citations, the hallucination
  metric reports N/A — that's not "perfect", that's "unmeasurable".
* **Citation coverage is low for the mock** (0.010 / 0.225) because the
  mock's answer text is the gold answer literal, which is short — token
  overlap with triple terms is small. A real LLM-generated answer would
  paraphrase using triple terms and score higher.

---

## How to reproduce

### With mocks (no API cost)

```bash
git checkout eval-harness

# Pure Land
uv run ontorag eval bench examples/pure_land/goldset.jsonl \
    --baseline ontorag_mock \
    --schema examples/pure_land/schema.ttl \
    --data examples/pure_land/data.ttl \
    --output examples/pure_land/bench_results/ontorag_mock.json

uv run ontorag eval bench examples/pure_land/goldset.jsonl \
    --baseline vector_rag_mock \
    --schema examples/pure_land/schema.ttl \
    --data examples/pure_land/data.ttl \
    --output examples/pure_land/bench_results/vector_rag_mock.json

uv run ontorag eval compare \
    examples/pure_land/bench_results/ontorag_mock.json \
    examples/pure_land/bench_results/vector_rag_mock.json \
    --name-a ontorag --name-b vector_rag \
    --output examples/pure_land/bench_results/comparison.md

# Commerce — same pattern with examples/commerce/
```

### With real LangChain + OpenAI (~$1)

LangChain is now wired through the CLI (`--baseline langchain`) and
RAGAS LLM-as-judge metrics are integrated into the orchestrator
(`--with-ragas`):

```bash
uv sync --extra bench
export OPENAI_API_KEY=sk-...

# Commerce domain — 20 questions, real LangChain + RAGAS, ~$0.30
uv run ontorag eval bench examples/commerce/goldset.jsonl \
    --baseline langchain \
    --schema examples/commerce/schema.ttl \
    --data examples/commerce/data.ttl \
    --with-ragas \
    --output examples/commerce/bench_results/langchain_real.json

# Pure Land domain — 50 questions, real LangChain + RAGAS, ~$0.75
uv run ontorag eval bench examples/pure_land/goldset.jsonl \
    --baseline langchain \
    --schema examples/pure_land/schema.ttl \
    --data examples/pure_land/data.ttl \
    --with-ragas \
    --output examples/pure_land/bench_results/langchain_real.json

# Compare against the ontorag_mock (perfect-retrieval upper bound)
uv run ontorag eval compare \
    examples/commerce/bench_results/ontorag_mock.json \
    examples/commerce/bench_results/langchain_real.json \
    --name-a ontorag --name-b langchain \
    --output examples/commerce/bench_results/comparison_vs_real.md
```

Real numbers replace the mock columns and the narrative becomes
provable rather than illustrative. The orchestrator's
`avg_ragas_faithfulness` / `avg_ragas_answer_correctness` /
`avg_ragas_answer_relevancy` aggregates carry RAGAS scores in the
JSON output.

---

## What is and isn't proven

| Claim | Status |
|---|---|
| The evaluation harness end-to-end works | **Proven** (4 successful bench runs, 2 comparisons) |
| Per-question reports + per-difficulty rollups generate cleanly | **Proven** |
| Vector RAG cannot produce triple-level citations | **Structural fact** — true by construction of vector RAG |
| ontorag beats vector RAG on accuracy in *these* domains | **Not proven** — needs real LangChain run |
| Hallucination rate of real LangChain on these goldsets | **Unknown** — would require real run |
| Real RAGAS Faithfulness numbers | **Unknown** — would require RAGAS LLM judge calls |

Open issues:
- ~~`--baseline langchain` is not wired into the orchestrator CLI yet.~~
  **Resolved** — wired in commit 20: `_build_baseline` accepts schema/data
  paths and constructs `LangChainVectorBaseline`. Errors degrade to
  `typer.BadParameter` with actionable messages (no traceback).
- ~~RAGAS metric integration into the orchestrator pipeline is pending.~~
  **Resolved** — `BenchRunner(with_ragas=True)` calls
  `evaluate_with_ragas` per question; aggregate carries
  `avg_ragas_faithfulness` / `avg_ragas_answer_correctness` /
  `avg_ragas_answer_relevancy`. Failure modes (missing key, missing
  ragas package, ragas runtime error) degrade silently to None — no
  partial result is lost.

Only remaining gap: **actual API spend has not been authorized**. The
single command above produces real numbers as soon as a user runs it
with their OpenAI key.

---

## Files in this benchmark set

```
examples/pure_land/bench_results/
├── ontorag_mock.json        # raw BenchResult JSON
├── vector_rag_mock.json     # raw BenchResult JSON
└── comparison.md            # side-by-side Markdown

examples/commerce/bench_results/
├── ontorag_mock.json
├── vector_rag_mock.json
└── comparison.md
```
