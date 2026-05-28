# Causal layer (v0.8) — design note

Status: ✅ shipped in v0.8.0–v0.8.3 on both backends (Fuseki + Neo4j) with the
hand-computed smoking quality bar passing. pgmpy is the optional `[bayes]` extra
(same engine as v0.7 — **no DoWhy**).

This is Layer 3 of the 4-layer reasoning stack (see `.claude/CLAUDE.md`). It sits
on top of the probabilistic layer (v0.7) and makes ontorag answer Pearl's
upper-rung questions:

- **Rung 2 (intervention)** — *"What if we **do** Y?"* (`do_query`, `identify_effect`)
- **Rung 3 (counterfactual)** — *"What if Y **had been** different, given what we saw?"* (`counterfactual`)

The logical layer answers *"is X necessarily true?"*, the probabilistic layer
*"how likely is X?"*; the causal layer separates *seeing* from *doing*.

## Over-claim guard (load-bearing)

The causal DAG is **user-supplied**. ontorag computes interventional /
counterfactual queries *assuming the DAG is correctly specified*; it does **not**
validate causal semantics or discover causation. Structure discovery
(`learn-dag`) emits **proposals only** — never auto-committed. This statement
ships in the README and in every causal tool / CLI docstring.

## What ships

| Sub-version | Deliverable |
|---|---|
| v0.8.0 | `causal:` vocabulary + models + RDF round-trip (`core/causal.py`); `CausalStore` Protocol (`stores/base.py`); Fuseki + Neo4j mixins (backend parity). |
| v0.8.1 | Pearl Rung 2 — `CausalEngine.do_query` + `identify`; MCP tools `do_query`, `identify_effect`. |
| v0.8.2 | Pearl Rung 3 — `CausalEngine.counterfactual` (canonical-SCM); MCP tool `counterfactual`. |
| v0.8.3 | Structure learning — `causal/discovery.py` (PC algorithm) → proposal-only `CausalModel`; `ontorag causal learn-dag`. |

CLI (`cli_causal.py`): `ontorag causal load/show/do/identify/counterfactual/clear/learn-dag`.

## Storage: the causal named graph only

Causal **metadata** (the DAG + observed/latent markers + the `basedOn` link)
lives exclusively in `urn:ontorag:causal` (or `urn:ontorag:{id}:causal` when
scoped) — never in the schema, data, or probabilistic graphs.
`causal_graph_uri()` builds the URI; like `probabilistic_graph_uri()` it is
deliberately **not** an `OntologyLayer` member (reasoning-stack storage, not the
document layer model — see `named-graph-layers.md`). It reuses only the
ontology-id validation for consistent per-ontology scoping.

The split is principled: the *quantified* model (CPTs) is the v0.7 BN in
`urn:ontorag:probabilistic`; the causal graph adds only the **causal reading** of
the structure plus which nodes are latent. `causal:basedOn` links the model to
the `bn:network` that quantifies its observed variables.

### Backend representations (native, same currency)

Both backends round-trip an identical `CausalModel`:

- **Fuseki**: `causal:` triples in the causal named graph, written/read with one
  GSP put/get (atomic at the graph level, no SPARQL UPDATE path).
- **Neo4j**: variables become `:_CausalVariable` nodes and edges become
  `[:_CAUSES]` relationships, both tagged with a `_scope` property equal to the
  causal graph URI. Dedicated labels keep them out of the `:Resource` ontology
  graph.

## `causal:` vocabulary (namespace `https://ontorag.dev/causal#`)

| Term | On | Meaning |
|---|---|---|
| `causal:CausalModel` | model node | singleton `causal:model` per graph; carries `rdfs:label` (name) |
| `causal:Variable` | variable | a node in the causal DAG |
| `causal:hasVariable` | model | model → variable membership |
| `causal:observed` | Variable | `xsd:boolean`; `false` = latent / unobserved confounder |
| `causal:influences` | cause → effect | a directed causal arc |
| `causal:basedOn` | model | URI of the `bn:network` that quantifies observed variables |

A latent variable (`causal:observed false`) has no CPT in the BN and cannot be
conditioned on; it only constrains which adjustment sets are valid. The model is
validated on construction: no duplicate URIs, edge endpoints must be declared
variables, no self-loops, and the graph must be **acyclic** (DFS cycle check).

## Engine (`causal/engine.py`)

`CausalEngine(bn, causal)` is built from the quantified BN (required — the causal
layer is quantified by it) plus an optional `CausalModel`. When the causal DAG is
omitted, the BN's own structure is used (all variables observed). pgmpy calls run
in a worker thread (`asyncio.to_thread`); variables/states accept either the URI
or the `rdfs:label`.

### Rung 2 — `do_query(do, query, evidence)`

`P(query | do(intervention), evidence)` via pgmpy `CausalInference.query(do=…)`.
`do` performs graph surgery (cuts incoming edges to the intervened variables);
pgmpy then applies the correct **back-door adjustment** automatically from the BN
structure. `do`, `query`, and `evidence` must be disjoint. Because the
confounders are present in the BN, `do(X)` de-confounds and differs from the
observational `see(X)`.

### identification — `identify(treatment, outcome)`

Reports identifiability using the **causal DAG** (latent confounders respected):
`get_minimal_adjustment_set` (back-door) and `get_all_frontdoor_adjustment_sets`
(front-door). `identifiable` is true if either a back-door set or a non-empty
front-door set exists.

### Rung 3 — `counterfactual(observed, intervention, query)`

`P(query | observed, had intervention held)` via abduction-action-prediction over
the **canonical independent-noise SCM** consistent with the CPTs: each variable's
per-parent-configuration response is an independent draw with probability equal
to its CPT column. We enumerate the joint response-function space, keep the mass
**consistent with the factual `observed`** (abduction), then re-evaluate the same
noise under the intervention (action + prediction).

> Counterfactuals are **not** uniquely identified by the CPTs alone — the
> canonical SCM is one standard, documented choice. The response space is capped
> at `_CF_RESPONSE_CAP` (= 2,000,000); larger models must reduce variable
> cardinality / parent counts. An observation with probability 0 raises (no
> counterfactual is defined for an impossible observation).

## Structure discovery (`causal/discovery.py`, v0.8.3)

`discover_dag(store, structure, target_class, …)` runs pgmpy's **PC** estimator
over instance data (reusing `bayes/learn.gather_observations`) to **propose** a
DAG. The returned `CausalModel` is a proposal only — PC recovers a
Markov-equivalence class, so some edge orientations are undetermined by data.
`ontorag causal learn-dag` always prints the human-review warning, even with
`--save`. Never auto-committed (CLAUDE.md anti-pattern).

## Quality bar — `do` ≠ `see` (smoking, observed confounder)

`examples/smoking/` defines a BN with an **observed genotype confounder**:
Genotype → Smoking, Genotype → Cancer, Smoking → Cancer. Hand-computed and
asserted in `tests/test_causal_engine.py`:

| Query | Expected | Why |
|---|---|---|
| `P(Cancer=yes \| see Smoking=yes)` | `0.72` | confounded upward by genotype |
| `P(Cancer=yes \| do Smoking=yes)` | `0.60` | back-door adjusted over Genotype |

`see` conditions (genotype correlates with smoking, inflating the estimate); `do`
cuts Genotype → Smoking, so P(Cancer | do) = Σ_g P(Cancer | Smoking=yes, g)·P(g)
= 0.4·0.5 + 0.8·0.5 = 0.60. The counterfactual consistency axiom (a chain X→Y,
observed X=1,Y=1, "had X=0" → P(Y=1)=0.2) is verified, and PC recovers the chain
skeleton (A–B, B–C, not A–C) in `tests/test_causal_discovery.py`. Both backends
return identical results (parity bar) — `tests/test_{fuseki,neo4j}_causal_integration.py`.

## MCP tools

| operation_id | endpoint | purpose |
|---|---|---|
| `do_query` | POST /tools/causal/do | `P(query \| do(X), evidence)` (Rung 2) |
| `identify_effect` | POST /tools/causal/identify | back-door / front-door adjustment sets |
| `counterfactual` | POST /tools/causal/counterfactual | `P(query \| observed, had(X))` (Rung 3) |

Capability-guarded (`getattr`): a backend without `get_bayes_network` → 501; a
scope with no stored BN → 404; invalid do/query/evidence or unidentifiable → 400;
pgmpy not installed → 501.

## Anti-patterns honored (CLAUDE.md)

- Causal metadata never stored in schema/data/probabilistic graphs — causal graph
  only.
- pgmpy-native — no DoWhy, no Java engine. One probabilistic/causal engine.
- Structure discovery produces proposals only — the DAG is never auto-modified
  from observational data.
- No causal-validity claim — the DAG is user-supplied; ontorag computes queries
  assuming it is correct.
