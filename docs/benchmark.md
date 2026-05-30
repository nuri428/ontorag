# Benchmark

The complete v1.0 benchmark document lives in the repository at
[`docs/BENCHMARK_v1.md`](https://github.com/nuri428/ontorag/blob/main/docs/BENCHMARK_v1.md)
and is included verbatim below. Two **key-free, deterministic** measurements
back the v1.0 claims:

1. **Goldset quality** — every benchmark question's `gold_sparql` executes
   cleanly against its schema + data (rdflib, backend-agnostic). 0 failures
   across 130 questions / 5 domains.
2. **Backend parity** — the same protocol tools return *identical* results
   on Fuseki / Neo4j / FalkorDB. 7/7 metrics match (`full_parity = True`) —
   ontorag's headline differentiator, now measured rather than asserted.

Both are reproducible from a clean checkout; commands are at the bottom of
the page.

!!! info "Reasoning-layer goldset (v1.1)"
    The probabilistic / causal layers now have a parallel goldset too —
    `examples/smoking/reasoning_goldset.jsonl` (6 hand-verified posterior /
    do / counterfactual / identify checks). Run with
    `ontorag eval reasoning <goldset>` against the stored BN + DAG on any
    backend. All 6 pass.

---

--8<-- "docs/BENCHMARK_v1.md"
