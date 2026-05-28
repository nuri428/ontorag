# Named-graph layers (v0.7.0) — design note

Status: shipped in v0.7.0. Establishes the **4-layer named-graph model** as
pure infrastructure — the vocabulary and URI mapping that later releases
(v0.7.1 Bayesian, v0.8 Causal) build on. No new reasoning capability is added
here; this is the structural tidy-up of Layers 0–1 of the 4-layer reasoning
stack (see `.claude/CLAUDE.md`).

This absorbs **Phase 1** of the paused `docs/design/layered-ontology-plan.md`.
Phases 2 (Policy/SHACL/SKOS), 3a (State time-series), 3b (router), and 4
(Provenance/PROV-O) remain deferred until user signal arrives.

## The two scoping dimensions

A triple's home named graph is decided by two **orthogonal** dimensions:

| Dimension | Values | Source of truth |
|---|---|---|
| **Ontology** | `None` (default/legacy) or a slug `^[a-zA-Z0-9_-]+$` | v0.5, `multi-ontology.md` |
| **Layer** | `semantic` / `policy` / `state` / `provenance` | v0.7.0, this note |

Combined, they produce the named-graph URI:

| | `ontology=None` (default) | `ontology="<id>"` |
|---|---|---|
| `semantic` | `urn:ontorag:schema` | `urn:ontorag:{id}:schema` |
| `state` | `urn:ontorag:data` | `urn:ontorag:{id}:data` |
| `policy` | `urn:ontorag:policy` | `urn:ontorag:{id}:policy` |
| `provenance` | `urn:ontorag:provenance` | `urn:ontorag:{id}:provenance` |

All construction goes through `core/ontology.py` — the single source of truth:

- `OntologyLayer` — the enum (`str` mixin so it interpolates without `.value`).
- `LAYER_GRAPH_URI` — default (ontology=None) URI per layer.
- `layer_graph_uri(ontology, layer)` — always-concrete URI for any pair.
- `resolve_layer(value)` — coerces a layer name or alias to `OntologyLayer`.
- `schema_graph_uri` / `data_graph_uri` / `scoped_graph` — thin wrappers kept
  for the existing call sites; now derived from the layer primitives.

## Decision: keep the physical URIs, rename only the vocabulary

The layered-ontology plan mapped `"schema"` → `urn:ontorag:semantic` and
`"data"` → `urn:ontorag:state`. We **renamed the vocabulary but kept the
physical graph URIs**:

- `OntologyLayer.semantic` → physical `urn:ontorag:schema` (unchanged)
- `OntologyLayer.state` → physical `urn:ontorag:data` (unchanged)

Why: renaming the physical URIs would (1) orphan every triple in a persisted
TDB2 store under the old graph names, requiring a data migration, and (2) break
the test suite and the modules that hardcode the strings
(`core/sparql.py`, `_fuseki_embedding_mixin.py`). The layer *name* is the API
surface that matters; the URI suffix is an implementation detail. So `_LAYER_SUFFIX`
maps the two legacy layers back to their original suffixes (`schema`/`data`)
while new layers (`policy`/`provenance`) use their own name. This is the literal
meaning of CLAUDE.md's "`schema/data` → `semantic/state` rename **for backward
compat**".

`"schema"` and `"data"` stay accepted everywhere as aliases for `semantic` and
`state` (`resolve_layer`), so existing CLI/API/`target=` arguments are unaffected.

## Reserved vocabulary: policy & provenance

`policy` and `provenance` are defined in the enum and the URI map but have **no
read or write path yet** — their owning features (SHACL/SKOS validation;
PROV-O/DCAT) are deferred Phases 2 and 4. They exist now so that:

1. the URI-construction pattern is uniform and future code adds a layer by
   adding one enum member + suffix, not by reinventing URI strings; and
2. the inference assembler and any "which graphs exist" logic can enumerate the
   full layer set up front.

Deliberately **not** added in v0.7.0 (would be speculative): `load_rdf` /
`dump_graph` / `clear_graph` paths for the reserved layers, and a CLI
`--layer` flag. Those land with the features that use them.

## Inference and named-graph separation

Separating TBox and ABox into different named graphs means a naive reasoner no
longer sees them together. ontorag handles this **at query level by default**:
read queries use `?inst a/rdfs:subClassOf* <Class>` and join the SCHEMA + DATA
named graphs, so `find_entities(:Animal)` returns Dog/Cat instances without any
reasoner configured. This is what the test suite exercises and what the default
`docker/fuseki/config.ttl.template` (jena-text + `unionDefaultGraph`) supports.

### Opt-in native reasoner: `config-inference.ttl.template`

For deployments that want the **store** to materialise entailments (so even raw
SPARQL or a third-party client sees inferred triples), v0.7.0 ships an opt-in
assembler `docker/fuseki/config-inference.ttl.template`:

- Layers an `ja:InfModel` (OWLMicro reasoner) over the special ARQ union graph
  `urn:x-arq:UnionGraph` — the merge of *all* named graphs — so TBox and ABox
  reason together regardless of which graph holds what.
- OWLMicro covers the three OWL constructs ontorag relies on (`subClassOf`,
  `owl:TransitiveProperty`, `owl:inverseOf`, plus `owl:sameAs`) without the cost
  of full OWL.
- Same TDB2 location as the default config → flip inference on/off over the same
  data, no reload.

Select it with `FUSEKI_CONFIG_TEMPLATE=config-inference.ttl.template` (the
entrypoint defaults to `config.ttl.template`).

**Trade-off**: this reference config focuses on inference and does not wire the
jena-text Lucene index, so `search_text` (BM25) is unavailable while it is
active. Composing a `text:TextDataset` with an inference default graph is
possible but deployment-specific and left out of the reference config. Scoped
reads (`GRAPH <urn:ontorag:...>`) bypass the inference default graph by design;
they continue to rely on query-level inference.

## Backward-compatibility invariants (must always hold)

- `schema_graph_uri(None) == "urn:ontorag:schema"`,
  `data_graph_uri(None) == "urn:ontorag:data"`.
- `LAYER_GRAPH_URI[OntologyLayer.semantic] == "urn:ontorag:schema"`,
  `LAYER_GRAPH_URI[OntologyLayer.state] == "urn:ontorag:data"`.
- `resolve_layer("schema") is OntologyLayer.semantic`,
  `resolve_layer("data") is OntologyLayer.state`.
- `scoped_graph(None, ...)` still returns `None` (union default graph).
- The fixed graph URIs appear verbatim in both Fuseki config templates.
