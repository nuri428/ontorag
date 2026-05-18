# Benchmark Results — Phase B

> **Status**: real head-to-head measurement complete (2026-05-18).
> Both domains run with **real LangChain + RAGAS** *and* **real ontorag
> chat agent (`ontorag_native`)** using gpt-4o-mini as the LLM for both
> baselines. Mock columns retained as the "perfect retrieval" upper bound.

---

## What was actually measured

Two mock baselines were run against the two Phase B goldsets:

| Mock | Behaviour | Cites triples? |
|---|---|---|
| `ontorag_mock` | Perfect retrieval — runs `gold_sparql`, returns gold answer + supporting triples | **Yes** |
| `vector_rag_mock` | Deterministic per-question bucket: 70 % correct / 20 % hallucinated / 10 % "I don't know" | **No** (chunks only) |

Combined: 4 bench runs (2 domains × 2 baselines) + 2 comparison files.

## Headline numbers

### Pure Land (50 questions, 948 triples) — real head-to-head

Six successive `ontorag_native` iterations against the same Pure Land
goldset, same gpt-4o-mini LLM, same Fuseki setup. Each version's diff
to its predecessor is listed below the table; see the eval-harness
branch commits for code-level detail.

| Metric | LangChain | v2 | v3 | v4 | v5 | v6 | **v7** |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Faithfulness** | **0.581** | 0.342 | 0.284 | 0.307 | 0.377 | 0.377 | 0.279 |
| **Correctness** | 0.363 | 0.274 | 0.257 | **0.374** | 0.355 | 0.326 | 0.347 |
| **Relevancy** | 0.537 | 0.479 | 0.460 | 0.540 | 0.684 | **0.725** | 0.703 |
| Citation count | 0 / 50 | 28 | 26 | **29** | 25 | 26 | 25 |
| Citation coverage | — | 0.062 | 0.071 | 0.057 | 0.063 | 0.060 | 0.064 |
| Hallucination rate | N/A | **0.000** | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| Avg tool calls / q | 0.00 | 1.58 | 1.96 | 1.70 | 1.58 | 1.64 | **1.82** |
| Avg latency (ms) | **1 317** | 5 638 | 4 968 | 3 584 | 5 333 | 5 307 | 4 557 |
| Transitive 3 q rc | 0.05/0.05/0.05 | 0.00/0.00/0.05 | 0.06/0.05/0.02 | 0.00/0.05/0.05 | 0.05/0.04/0.05 | 0.04/**0.54**/0.05 | 0.11/**0.92**/**0.92** |

#### Code changes per iteration

* **v2** — multilingual `=` lang-literal fix, schema context lists all
  property URIs, TBox metadata (TRANSITIVE / inverseOf) auto-extracted
  via SPARQL and surfaced as schema-context flags. Established the
  baseline narrative (Citation ↑↑, Correctness slightly trails LC).
* **v3** — added an explicit "transitive-question → 2-step
  (find_entities then property_path_query)" rule to the system
  prompt. **Regressed across the board** (Faithfulness 0.34→0.28,
  Correctness 0.27→0.26); the rule fired on simple questions too and
  added noise. Reverted.
* **v4** — kept v2 + reverted v3 + added case-insensitive `=` for
  rdfs:label (Q039 "peacock" lowercase was missing the data). Hit
  Correctness 0.374 — the *first version to beat LangChain on
  Correctness*.
* **v5** — refactored to ontology-driven prompting: rdfs:comment /
  skos:definition automatically extracted from the TBox and rendered
  inline; tool descriptions rewritten in OWL-semantics terms
  (`rdfs:subClassOf-aware`, `owl:TransitiveProperty closure`, etc.);
  system prompt shrunk 60→25 lines. Relevancy jumped 0.54→0.68
  (LangChain +27%).
* **v6** — `property_path_query` accepts `start_label`; the tool itself
  resolves label → instance URI in a single SPARQL round-trip
  (case-/lang-tag-insensitive). Removes the need to chain
  find_entities → property_path_query via the prompt. Relevancy
  reached 0.725 (LC +35%).
* **v7** — `property_path_query` gains **Mode 3 — class-wide closure**:
  pass only `start_class_uri` and SPARQL `?start a <Class> ; <pred>+
  ?reached` unions the closures from every instance. Q040 ("any
  celestial bird is transitively located") and Q039 both score
  **rc=0.92**. Faithfulness regressed (0.38→0.28) because the LLM
  emits longer, more discursive answers — that lever needs separate
  work (see "Open work").

#### What this iteration arc demonstrates

| Lever | Direction | Evidence |
|---|---|---|
| Prompt rule accumulation | ❌ avoid | v3 regressed every metric vs v2 |
| Lang/case fix at SPARQL builder | ✅ pure win | v4 hit best Correctness with one filter-line change |
| Rendering TBox `rdfs:comment` in prompt | ✅ huge | v5 Relevancy +0.14, Faithfulness +0.07 |
| OWL semantics in tool API (label resolve, class closure) | ✅ targeted | v6/v7 jumped transitive questions from rc<0.10 to rc=0.92 |
| System-prompt closure-keyword routing | ❌ leaky | v3 regression; v5+ dropped it entirely |

**Real LangChain qualitative findings** (`gpt-4o-mini`, k=5, 90 indexed ABox chunks):

* **Transitive inference (Q008, Q039, Q040)**: LangChain produces *partial* answers. For "All places where the peacock is located": gold says **"Jeweled Tree, Sukhāvatī"** (transitive closure over `pl:locatedIn`); LangChain answers **"the Jeweled tree"** only — never makes the second hop. Faithfulness 0.67–1.0 (what it says *is* in the context) but Answer Correctness 0.14–0.63 (misses the second entity). **This is the structural inference gap that mocks could not surface.**
* **Trap questions (5 total)**: LangChain answered all 5 with "I don't know" — surface-correct but RAGAS Answer Correctness scores them ~0.03–0.06 because the gold answer is the longer phrase "No information in this ontology (0). This dataset models only…". RAGAS LLM-as-judge treats the texts as low-similarity even though both refuse to answer.
* **Avg Answer Correctness 0.36** is dragged down by traps + partial inference answers, not by easy questions. Easy single-entity questions score high individually.

**Real ontorag_native qualitative findings — post 5-fix** (`gpt-4o-mini`, fresh AgentLoop per question, 234-triple TBox + 717-triple ABox in Fuseki):

* **Transitive inference (Q008/Q039/Q040)**: agent **now calls `traverse_graph`** (the 5-fix surfaced `locatedIn` as `TRANSITIVE` in the schema context; the v1 run only ever used `find_entities`). Each cite-count is 5 — i.e. agent reached the Peacock URI and traversed `locatedIn`. **However**, the final answer text is still "no places" / "어떤 천상의 새가 위치한 장소는 없습니다" — the LLM is failing to *interpret* the traversal result rather than failing to call the tool. Ad-hoc single-question reproduction *does* yield the gold answer ("Jeweled Tree, Sukhāvatī"), so the failure looks goldset-specific (phrasing? trap-coloured prompt context?). Open issue.
* **Easy questions**: Citation 10/15 (vs 4/15 pre-fix). Commerce Q001 ("Aurora Tech CEO") now returns the correct *"Alice Kim"* with cited=20, RAGAS Correctness 0.87.
* **Trap questions**: 1/5 cited (acceptable — most traps have no triples to cite); 0 hallucinations.
* **Hallucination rate 0.000** — every cited triple is in the graph.

### The 5 surgical fixes (this round)

1. **SPARQL `=` lang-literal mismatch** (`src/ontorag/core/sparql.py`): `?label = "Peacock"` failed against `"Peacock"@en` under RDF semantics. Now OR-disjuncts plain equality with `STR(?label) = "Peacock"`. Surfaced *only* on Pure Land, the first multilingual goldset; same bug was silently hurting any other multilingual ontology.
2. **Schema context exposes properties** (`src/ontorag/chat/agent.py:_format_schema_for_prompt`): v1 listed only classes. v2 lists every property URI + label + type + `domain → range` + flags. LLM no longer guesses predicate URIs from labels.
3. **TBox metadata extraction** (`src/ontorag/stores/fuseki.py + stores/base.py:PropertySummary`): SPARQL now pulls `owl:TransitiveProperty` and `owl:inverseOf`; `PropertySummary` gained `is_transitive` and `inverse_of_uri`. Schema context surfaces these as `TRANSITIVE` / `inverseOf=…` flags. **Fully domain-agnostic** — any ontology declaring those constructs gets them propagated.
4. **traverse_graph description generalised** (`src/ontorag/chat/agent.py:_TOOLS`): removed Pokemon-specific examples ("X가 진화하면?", "evolvesFrom"). Now references "TBox `TRANSITIVE` flag" + closure vocabulary instead.
5. **System-prompt fallback rules**: explicit "find_entities 0건이면 다른 label/sub-class 시도" loop, "predicate 자리에 label 넣지 마라" guard.

Together these fixes are *not* eval-harness scaffolding — they are real
ontorag core changes that any user of the chat agent (regardless of domain)
benefits from. Items 1 and 3 in particular should land on `main`.

### What the head-to-head measurement actually proves

| Claim | Mock said | Real measurement said |
|---|---|---|
| ontorag beats vector RAG on accuracy | ✓ (perfect retrieval simulation) | **✗ — gpt-4o-mini agent under-performs LangChain on every RAGAS metric** |
| Vector RAG cannot follow transitive closures | (untested) | ✓ — LangChain stops at first hop |
| ontorag *can* follow transitive closures | ✓ (mock ran gold_sparql with property paths) | **✗ — gpt-4o-mini agent could not even retrieve the first hop on Q008/Q039/Q040** |
| Vector RAG cannot give triple-level citations | (assumed) | ✓ — 0/70 |
| ontorag native gives triple-level citations | ✓ (45 % on mock) | ✓ — 14/50 (28 %) on Pure Land, 1/20 (5 %) on Commerce |
| Hallucination rate measurable for ontorag | ✓ | ✓ — 0.000 on real run |

**The honest takeaway**: with a small LLM (gpt-4o-mini), ontorag's tool agent is **less accurate** than vector RAG, because synthesising correct schema-aware tool calls (predicate URIs, class URIs, filter syntax) is a heavier cognitive load than extracting answers from natural-language chunks. The **structural differentiators** — triple-level citation and 0-hallucination-rate measurability — are preserved, but they are *not enough on their own* to outperform vector RAG when the LLM is the bottleneck. Whether a larger model (gpt-4o, Claude Sonnet) closes the gap is an open question this run did not answer.

### Commerce (20 questions, 297 triples) — real head-to-head

| Metric | ontorag_mock<br>*(perfect-retrieval)* | **langchain (real)** | **ontorag_native v1**<br>*(pre-fix)* | **ontorag_native v2**<br>*(post-fix)* |
|---|---:|---:|---:|---:|
| Avg latency (ms) | 180 | **1 770** | 4 311 | **4 032** |
| Avg tool calls | 1.15 | 0.00 | 1.55 | **1.70** |
| Avg hallucination rate | 0.000 | *(N/A)* | 0.000 | **0.000** |
| Avg citation coverage | 0.225 | *(N/A)* | 0.214 | 0.076 |
| Citation provided (count / rate) | 9 / 20 (45 %) | **0 / 20 (0 %)** | 1 / 20 (5 %) | **11 / 20 (55 %)** |
| **Avg RAGAS Faithfulness** | — | — | — | **0.46** |
| **Avg RAGAS Answer Correctness** | — | — | 0.17 | **0.31** |
| **Avg RAGAS Answer Relevancy** | — | — | — | **0.53** |

**Δ from the 5 fixes (v1 → v2) on Commerce**: Citation **1 → 11** (11×),
Answer Correctness **0.17 → 0.31** (+82 %). Easy lookups Q001/Q003/Q005
(CEO / founding year / employee count) now answered correctly with
high cited-triple counts.

**Real LangChain qualitative findings** (`gpt-4o-mini` + Chroma + `text-embedding-3-small`, k=5, 31 indexed chunks):

* **Easy questions (Q001–Q005)**: all answered correctly — "The CEO of Aurora Tech is Alice Kim", "$899.00", "1998", "Japanese Yen", "800 employees".
* **Trap questions (Q018–Q020)**: all three returned `"I don't know."` — **the correct answer for KG-grounded benchmarks**. LangChain did not hallucinate Aurora Phone X3 / Orion Labs products / Vega Wearables parent company.
* **Citation provided: 0 / 20.** Vector RAG produces text chunks, not triple-level citations — by construction. The user cannot click a fact to see the supporting triple.
* **Cost**: ~$0.02 for the 20-question run (gpt-4o-mini is cheap).

### Updated Commerce narrative (post 5-fix)

The original ontorag_native v1 run reported "LangChain easy 5/5 vs
ontorag 1/5" — true at the time, but the gap was caused by **ontorag
core bugs**, not by an inherent limitation of schema-aware tool agents:

* Q001 "Aurora Tech CEO" v1 → `find_related(predicate="...#Chief Executive Officer")` → Fuseki "Invalid URI" → fallback `find_entities` → 0 → "정보 없음" (correctness 0.05).
* Q001 v2 → schema_context now lists `pl:ceo` URI explicitly, lang-literal fix lets `?label = "Aurora Tech"` actually match → answer "Alice Kim", cited=20, **correctness 0.87**.

Citation 1 → 11 and Correctness 0.17 → 0.31 are the immediate measurable
impact. The remaining gap to LangChain (still ~0.05 absolute Correctness
on Pure Land) appears to be in **multi-hop result interpretation**, not
in tool selection.

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
| The evaluation harness end-to-end works | **Proven** (5 successful bench runs, 3 comparisons) |
| Per-question reports + per-difficulty rollups generate cleanly | **Proven** |
| Vector RAG cannot produce triple-level citations | **Proven (real run)** — LangChain returned 0 / 20 cited triples on Commerce |
| ontorag beats vector RAG on accuracy on small clean Commerce domain | **Disproven** — LangChain answered easy + trap questions correctly |
| LangChain hallucinates on KG-absent facts | **Disproven for Commerce** — returned "I don't know" on all 3 trap questions |
| Pure Land (multilingual, 50 questions, inference-heavy) real run | **Proven** — LangChain answer accuracy collapses on transitive inference (Answer Correctness 0.14–0.63 on hard inference tier) |
| RAGAS Faithfulness / Answer Correctness numbers | **Proven** — Pure Land Faithfulness 0.58, Answer Correctness 0.36, Answer Relevancy 0.54 (LangChain); 0.32 / 0.20 / 0.36 (ontorag_native) |
| Vector RAG handles single-entity lookups well | **Proven** — Commerce easy 5/5, Pure Land easy single-entity scores high |
| Vector RAG cannot do OWL transitive inference | **Proven** — Q008/Q039/Q040 (`pl:locatedIn+`) all answered with only the direct hop, missing Sukhāvatī |
| **ontorag native (real agent) head-to-head with LangChain** | **Proven** — both domains run with gpt-4o-mini, RAGAS on Pure Land. Result: **ontorag_native loses on accuracy, wins on citation availability + hallucination measurability** |
| ontorag native can follow OWL transitive closures | **Disproven for gpt-4o-mini** — Q008/Q039/Q040 returned "정보 없음" without even retrieving the first hop. Open question whether a larger LLM closes this gap. |
| Real ontorag native vs vector RAG accuracy at small-LLM scale | **Disproven** — gpt-4o-mini ontorag agent under-performs LangChain (0.20 vs 0.36 RAGAS Correctness on Pure Land). Mock simulation gave the opposite answer. |

Remaining open question:
- Does a larger LLM (gpt-4o, Claude Sonnet 4.6) close the schema-aware tool-call accuracy gap and let ontorag_native beat LangChain on answer correctness? Not measured in this run — would cost ~$5-10 to repeat with gpt-4o.

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
├── ontorag_mock.json                  # perfect-retrieval upper bound
├── vector_rag_mock.json               # 70/20/10 deterministic mock
├── langchain_real.json                # real LangChain + RAGAS, ~$0.65
├── ontorag_native.json                # real ontorag agent + RAGAS, ~$0.85
├── comparison.md                      # mock-vs-mock
├── ontorag_vs_langchain_real.md       # mock ontorag vs real LangChain
└── ontorag_native_vs_langchain.md     # real ontorag agent vs real LangChain ⭐

examples/commerce/bench_results/
├── ontorag_mock.json
├── vector_rag_mock.json
├── langchain_real.json                # real LangChain, ~$0.02
├── ontorag_native.json                # real ontorag agent + RAGAS, ~$0.15
├── comparison.md
├── ontorag_vs_langchain_real.md
└── ontorag_native_vs_langchain.md     # real ontorag agent vs real LangChain ⭐
```

Total external spend across all real runs: **~$1.67** (LangChain $0.67 + ontorag_native $1.00).
