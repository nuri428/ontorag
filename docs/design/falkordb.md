# FalkorDB backend (v0.9) — design note

Status: ✅ shipped in v0.9.0–v0.9.1. Third graph backend behind
`GRAPH_STORE=falkordb` (the `[falkordb]` extra). Full protocol + capability
parity with Fuseki and Neo4j — verified by `tests/test_falkordb_integration.py`
(11 live tests) against `falkordb/falkordb:latest`.

FalkorDB is a Cypher-compatible (OpenCypher), GraphBLAS-accelerated graph
database packaged as a Redis module. Because it speaks Cypher, the adapter
**reuses the Neo4j L1 + reasoning mixins** — only connection, RDF loading, and
the full-text / vector capabilities are FalkorDB-specific.

## License (honest disclosure)

FalkorDB is **RSAL (Redis Source Available License)** — *not* OSI-approved open
source, unlike Fuseki (Apache 2.0) and Neo4j (GPL/AGPL). The README and
docker-compose document this; choose FalkorDB for its GraphRAG/GraphBLAS
performance positioning with that caveat in mind.

## What ships

| Sub-version | Deliverable |
|---|---|
| v0.9.0 | `stores/falkordb.py`: async client, `_run` Node→dict normalisation, custom rdflib→Cypher loader, TBox/ABox classify, status, query_pattern, dump, clear_graph. Reuses Neo4j schema/entity/traversal mixins. |
| v0.9.1 | `_falkordb_search_mixin.py` (native full-text) + `_falkordb_embedding_mixin.py` (FastRP + native vector). Bayesian + Causal mixins reused from Neo4j. |

## Maximal reuse: why the Neo4j mixins port unchanged

The graph model is **edge-based, not label-based** for the hot paths: instances
are matched via `(inst:Resource)-[:rdf__type]->(class)-[:rdfs__subClassOf*0..N]->(target)`,
not via type-labels. The Neo4j entity/traversal mixins issue plain OpenCypher
against `:Resource {uri}` nodes with `prefix__local` properties/rel-types — all
of which FalkorDB supports. The adapter's `_run` flattens a returned FalkorDB
`Node` to its property dict (`{uri, …}`), matching Neo4j's `result.data()`
semantics the shared mixins assume.

`FalkorDBStore` inherits: `_Neo4jSchemaMixin`, `_Neo4jEntityMixin`,
`_Neo4jTraversalMixin`, `_Neo4jBayesMixin`, `_Neo4jCausalMixin`, plus the two
FalkorDB capability mixins. `shorten`/`expand`/`_safe_rel`/`pattern_to_cypher`/
`_neo4j_values`/`_neo4j_scope` are backend-agnostic and reused directly.

## The n10s replacement (custom RDF loader)

FalkorDB has no neosemantics. `_rdf_to_graph` + `_import` reproduce the three
n10s conventions the shared mixins depend on:

- **SHORTEN** — URIs → `prefix__local` for property keys, rel-types, and labels
  (`_safe_rel`-validated). Prefixes come from rdflib namespaces; any unbound
  namespace gets a generated `nsN`. The prefix map is **persisted** in a single
  `:_OntoragMeta {kind:'prefixes'}` node (the `_NsPrefDef` analogue) so a fresh
  process can shorten/expand.
- **LABELS_AND_NODES** — every `s rdf:type o` adds `shorten(o)` as an extra node
  label on `s` (FalkorDB supports multi-label), so label-based schema queries
  (`MATCH (c:owl__Class)`) work. The `rdf__type` edge is created too.
- **ARRAY** (`handleMultival`) — every literal property is stored as a LIST, so
  `inst.\`prop\`[0]` and `unpack_value` behave exactly as on Neo4j.

Blank-node subjects/objects are skipped (rare in target ontologies; documented).

## Dialect differences (vs Neo4j) — all live-verified

| Concern | Neo4j | FalkorDB |
|---|---|---|
| full-text procs | `db.index.fulltext.*` | `db.idx.fulltext.*` (first arg = label) |
| vector procs | `db.index.vector.*` | `CREATE VECTOR INDEX … OPTIONS{dimension,similarityFunction}` + `db.idx.vector.queryNodes` |
| existential subquery | `EXISTS { … }` | **unsupported** → OPTIONAL MATCH + `count()=0` |
| `CONTAINS` | operator or function | **operator only** (`x CONTAINS y`) — shared `_build_filter_cypher` fixed to operator form (correct on both) |
| full-text values | indexes scalars + arrays | **scalars only** → see `_fulltext` shadow below |
| multi-label, `*0..N`, array props, `vecf32()` | yes | **yes** (all confirmed) |

## Full-text: the `_fulltext` scalar shadow

FalkorDB's full-text index indexes **scalar** string properties only — it
silently ignores the LIST-valued RDF properties this adapter stores. So
`_ensure_fulltext_index` concatenates every string value on each `:Resource`
node into one scalar `_fulltext` property and indexes that single field. The
original array properties are untouched (the entity/schema mixins read them);
search returns `node.rdfs__label` (array, unpacked) for display. `_extract_props`
skips `_`-prefixed keys so the shadow / embedding / `_ontology` props never leak
into `describe_entity` output (a strict improvement applied to both backends).

## Embeddings: FastRP + native vector index

No GDS, so structural embeddings use the pure-Python `core/fastrp.py` (the Fuseki
path); the resulting `vecf32` vectors land in a FalkorDB **native** vector index
(`_struct_embedding` / `_text_embedding`, cosine). Textual embeddings use the
`EmbeddingProvider`; `hybrid` mode fuses both via RRF (k0=60). FalkorDB's cosine
index returns a *distance* (0 = identical), converted to a `1/(1+d)` similarity
so higher = more similar (matching the other backends' `SimilarHit.score`).

## Reasoning storage (Bayesian + Causal)

Reused verbatim from the Neo4j mixins: `:_BayesVariable` / `:_BayesCPD` and
`:_CausalVariable` + `[:_CAUSES]` nodes tagged with `_scope`. These carry distinct
labels (never `:Resource`), so they never pollute the `:Resource` graph queries.

## Known limitations (surfaced by the v0.9 skeptic-code + pre-mortem review)

These are deliberately deferred (out of v0.9.0 scope / would touch all backends);
tracked here so they are not silent:

- **Typed-literal fidelity** — `_literal_to_value` stores int/float/bool natively
  but drops the `xsd:` datatype; `dump_graph` re-emits literals as plain/lang
  strings. So an `xsd:date` / `xsd:integer` round-trips as `xsd:string`. Numeric
  aggregation still works (`toFloat`), but TTL fidelity diverges from Fuseki.
  Fix needs a parallel datatype map + a cross-backend dump→reload fidelity test.
- **No query timeout** — `_run`/`_run_write` don't pass a timeout (identical to
  the Neo4j adapter — a pre-existing repo-wide pattern). A pathological query can
  block; under concurrent `/chat` the single graph handle is a scaling ceiling.
  A timeout + connection pool is a cross-backend follow-up.
- **Full-text rebuild cost** — `_ensure_fulltext_index` rebuilds the `_fulltext`
  shadow for the whole graph on every `load_rdf` (O(N) writes), capped at
  `_FT_SCAN_LIMIT` (50 000) nodes with a warning past the cap. Fine at ontology-
  ABox scale; delta-only rebuild + pagination is the eventual fix.
- **Blank nodes** — skipped by the loader (documented v0.9.0 simplification).

## Anti-patterns honored (CLAUDE.md)

- GraphStore Protocol preserved — `create_store()` selects the backend by env var;
  callers never import a concrete store.
- No premature optimization — `_fulltext` rebuild + label batches are simple and
  correct at ontology-ABox scale; index tuning deferred until profiled.
- Honest licensing — RSAL documented, not glossed over.
