# Probabilistic (Bayesian) layer (v0.7) — design note

Status: ✅ shipped in v0.7.1–v0.7.4 — integration-verified on both backends
(Fuseki + Neo4j, 10 integration tests) with the hand-computed posterior quality
bar passing. pgmpy is the optional `[bayes]` extra.
This is Layer 2 of the 4-layer reasoning stack (see `.claude/CLAUDE.md`) — it
makes ontorag answer *"how likely is X?"* on top of the logical layer's *"is X
necessarily true?"*. Activates Palantir's "Dynamic" reasoning capability.

## What ships

| Sub-version | Deliverable |
|---|---|
| v0.7.1 | `bn:` vocabulary + spec models + RDF round-trip (`core/bayes.py`); `BayesianStore` Protocol (`stores/base.py`); Fuseki CPT mixin. |
| v0.7.2 | Neo4j CPT mixin — backend parity. |
| v0.7.3 | `BayesianEngine` (pgmpy wrapper) + `compute_posterior` / `mpe` MCP tools. |
| v0.7.4 | `ontorag bayes` CLI incl. `learn-cpt` (estimate CPTs from ABox data). |

pgmpy + pandas are an **optional** dependency (`[bayes]` extra). The core graph
/ RAG functionality never imports them; the engine and learner import lazily and
a missing install surfaces as `BayesianEngineError` (→ HTTP 501) with an
actionable message.

## Storage: the probabilistic named graph only

CPTs live **exclusively** in `urn:ontorag:probabilistic` (or
`urn:ontorag:{id}:probabilistic` when scoped) — never in the schema or data
graphs. `probabilistic_graph_uri()` builds the URI; it is deliberately **not**
an `OntologyLayer` member, because the probabilistic graph is reasoning-stack
storage (Layer 2), not part of the document layer model
(semantic/policy/state/provenance — see `named-graph-layers.md`). It reuses only
the ontology-id validation so per-ontology scoping is consistent.

### Backend representations (native, same currency)

Both backends round-trip an identical `BayesNetwork`:

- **Fuseki**: the whole network is `bn:` triples in the probabilistic named
  graph. Written/read with one GSP put/get — atomic at the graph level, no
  SPARQL UPDATE path.
- **Neo4j**: variables/CPDs become `:_BayesVariable` / `:_BayesCPD` nodes tagged
  with a `_scope` property equal to the probabilistic graph URI. Dedicated
  labels keep them out of the `:Resource` ontology graph.

## `bn:` vocabulary (namespace `https://ontorag.dev/bn#`)

| Term | On | Meaning |
|---|---|---|
| `bn:Network` | network node | singleton per graph; carries `rdfs:label` (name) |
| `bn:Variable` | variable | a discrete random variable node |
| `bn:CPD` | CPD (blank node) | one conditional distribution |
| `bn:states` | Variable | ordered RDF list of state labels |
| `bn:represents` | Variable | optional OWL class/property/instance URI |
| `bn:forVariable` | CPD | the variable this CPD is for |
| `bn:evidence` | CPD | ordered RDF list of parent variable URIs (empty = prior) |
| `bn:values` | CPD | JSON 2D array, pgmpy `TabularCPD` layout |
| `bn:dependsOn` | Variable | child → parent edge (structure-only specs) |

### Serialization strategy

Ordered, small structural arrays (`bn:states`, `bn:evidence`) use RDF lists —
order-preserving and hand-authorable. The dense CPT (`bn:values`) is a JSON
literal because a multi-dimensional probability table as nested RDF lists is
unworkable. The `values` matrix uses pgmpy's `TabularCPD` layout: shape
`(variable_card, prod(evidence_card))`, columns enumerate evidence
state-combinations with the **last** evidence variable varying fastest.

## Inference (v0.7.3)

`BayesianEngine` builds a pgmpy `DiscreteBayesianNetwork` (with `check_model()`)
and answers via `VariableElimination`:

- `compute_posterior(evidence, query)` → `P(query | evidence)` marginals;
- `mpe(evidence)` → most probable joint assignment to all non-evidence vars.

Synchronous pgmpy calls run in a worker thread (`asyncio.to_thread`). Variables
and evidence accept either the variable URI or its `rdfs:label`; states are
always labels (never integer indices).

## Quality bar — hand-computed posteriors

Synthetic Pokémon BN (`examples/pokemon/bayes-network.ttl`): TypeMatchup
(prior .4/.3/.3) → Outcome (`P(win|adv,neu,dis)=.8/.5/.2`). Hand-computed and
asserted in `tests/test_bayes_engine.py`:

| Query | Expected |
|---|---|
| `P(Outcome=win)` | `.4·.8 + .3·.5 + .3·.2 = 0.53` |
| `P(TypeMatchup=advantage \| win)` | `.32 / .53 ≈ 0.604` |
| `MPE()` | `(advantage, win)` — joint `.32` is the max |
| `MPE(Outcome=lose)` | `TypeMatchup=disadvantage` — `.8·.3 = .24` is the max |

Both backends must return identical results (the parity bar) — verified by the
Fuseki and Neo4j integration tests.

## CPT learning (v0.7.4)

`learn-cpt` takes a **structure** spec (`bn:dependsOn` edges, each variable
mapped to an OWL property via `bn:represents`) and estimates the CPTs from the
ABox: each instance of the target class contributes one observation (its
property values mapped to declared states); pgmpy's `BayesianEstimator` (BDeu,
robust to unseen combos) or `MaximumLikelihoodEstimator` fits the tables. This
ties v0.3 LLMs4OL output (text → triples) to BN parameter estimation
(triples → probabilities).

Instances missing a variable's value, or whose value is not a declared state,
are skipped. Declared `state_names` are passed to the estimator so every state
appears in the learned CPT even when unobserved.

## MCP tools

| operation_id | endpoint | purpose |
|---|---|---|
| `compute_posterior` | POST /tools/bayes/posterior | `P(query \| evidence)` |
| `mpe` | POST /tools/bayes/mpe | most probable explanation |

Capability-guarded (`getattr`): a backend without `get_bayes_network` → 501; a
scope with no stored network → 404; invalid evidence/query → 400; pgmpy not
installed → 501.

## Anti-patterns honored (CLAUDE.md)

- CPTs never stored in schema/data graphs — probabilistic graph only.
- Python-native only — pgmpy, no Java engine.
- "Dynamic" (this reasoning capability) is not conflated with "State"
  (time-series ABox, a deferred document layer).
