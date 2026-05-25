# Neo4j + neosemantics (n10s) backend — design note

Status: design (v0.5.0, Phase 1). Companion to `sparql-approach.md`.

ontorag's `GraphStore` protocol is RDF/URI-first. This note records how the
Neo4j adapter maps that protocol onto a property graph via the **neosemantics
(n10s)** plugin, and the decisions taken for v0.5.0.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| RDF→PG import | **n10s** (`n10s.rdf.import.inline`) | URI-faithful, round-trippable; no hand-rolled MERGE |
| `handleVocabUris` | **SHORTEN** | readable `prefix__local` labels/keys; prefixes pinned from the TTL |
| `handleRDFTypes` | **LABELS_AND_NODES** | classes exist as nodes → real `rdfs:subClassOf` inference possible |
| subClassOf inference | **implemented natively** | `find_entities`/`count` follow `[:rdfs__subClassOf*0..]` — intentional divergence from current Fuseki (`--mem`, inference off) |
| L2 `query_pattern` | **pattern_to_cypher** | full parity with the Fuseki BGP→SPARQL translator |
| verification | **live docker Neo4j** integration tests | exercise real n10s import + Cypher, not mocks |

## Graph shape (SHORTEN + LABELS_AND_NODES)

For `pk: <http://example.org/pokemon#>` registered before import:

```
(:Resource:pk__Pokemon { uri: ".../Pikachu", pk__name: "Pikachu" })
    -[:rdf__type]->   (:Resource:owl__Class { uri: ".../Pokemon" })
    -[:pk__hasType]-> (:Resource:pk__Type  { uri: ".../Electric" })

(:Resource{ uri: ".../LegendaryPokemon" })
    -[:rdfs__subClassOf]-> (:Resource{ uri: ".../Pokemon" })
```

- Every node carries the `:Resource` label + the shortened class label(s).
- Object properties → relationships typed `prefix__local`.
- Datatype properties → node properties keyed `prefix__local`.
- Classes/properties from the TBox are themselves `:Resource` nodes, so the
  schema is queryable and the hierarchy is traversable.

## URI ↔ shortened mapping layer

The protocol passes full URIs (`class_uri`, `predicate`, …); n10s stores the
shortened form. The adapter owns a thin bidirectional map:

- **expand** `prefix__local` → full URI (read path: results back to protocol).
- **shorten** full URI → `prefix__local` (write/query path: build Cypher).

Prefixes are **pinned before import** from the RDF file's own `@prefix`
declarations (rdflib `graph.namespaces()`) via `n10s.nsprefixes.add(prefix, ns)`,
so `pk__Pokemon` is stable rather than n10s's auto-assigned `ns0__`. The map is
also recoverable at runtime from n10s's `_NsPrefDef` node.

## Bootstrapping (once per database)

```cypher
CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS
  FOR (r:Resource) REQUIRE r.uri IS UNIQUE;          // Neo4j 5.x syntax

CALL n10s.graphconfig.init({
  handleVocabUris: 'SHORTEN',
  handleRDFTypes:  'LABELS_AND_NODES',
  handleMultival:  'ARRAY',          // protocol exposes multi-valued props
  keepLangTag:     true              // preserve rdfs:label @lang for resolution
});
```

## Protocol method → Cypher sketch

| Protocol | Cypher strategy |
|----------|-----------------|
| `load_rdf` | pin prefixes → `n10s.rdf.import.inline(ttl, 'Turtle')`; schema vs data separated by label set |
| `get_schema` | match `:owl__Class` / `:rdfs__Class` nodes + `[:rdfs__subClassOf]` + property domain/range |
| `find_entities(C)` | `MATCH (i)-[:rdf__type]->()-[:rdfs__subClassOf*0..]->(c {uri:C})` (inference) |
| `describe_entity` | node props + outgoing/incoming rels (incoming = `owl:inverseOf` surfacing) |
| `traverse` / `find_path` | variable-length `[*1..n]`, `shortestPath()` |
| `property_path_closure` | `[:pred*]` from instance / label / class-wide start modes |
| `aggregate` / `count_entities` | `count`/`sum`/`avg`/… over matched instances |
| `query_pattern` | `pattern_to_cypher` (BGP triples → MATCH, filters → WHERE) |
| `dump_graph` | `n10s.rdf.export.cypher` / full-graph export |

## Resolved (verified live against neo4j:5.26 + n10s 5.26, P1-7)

- **dump_graph**: `n10s.rdf.export.*` round-trips — re-parsed TTL preserves
  `rdf:type` + datatype literals (e.g. `nationalDex`). XLSX path via rdflib.
- **`handleMultival: ARRAY`**: every datatype prop is stored as a list; the
  adapter unwraps single-element arrays to scalars (`stores/_neo4j_values.py`)
  so outputs match the Fuseki shape.
- **`keepLangTag: true`**: `rdfs__label` values keep `@lang`; label lookup in
  `property_path_closure` matches lang- and case-insensitively (parity with Fuseki).
- **Cypher injection**: all rel-type/label/prop-key interpolation routes through
  `core/cypher.py::_safe_rel()` (allowlist regex); all values are bound
  parameters. Variable-length paths are depth-capped (`subClassOf*0..N`, pred `*1..6`).

## Known divergence from Fuseki (intentional)

- **subClassOf inference is ON for Neo4j, OFF for the current Fuseki deploy**
  (`--mem`, no reasoner). `find_entities`/`count_entities` therefore return
  different sets across backends. Follow-up: enable `ja:OntModelSpec` reasoning
  on Fuseki to re-converge, or document the difference as backend-specific.
- TBox/ABox separation is label-based (no named graphs); `clear_graph` counts
  are node counts, and `status().triple_count` is approximate.

## Sources

- [Configuring Neo4j to use RDF data — n10s](https://neo4j.com/labs/neosemantics/4.0/config/)
- [Importing RDF Data — n10s](https://neo4j.com/labs/neosemantics/4.3/import/)
- [Mapping graph models — n10s](https://neo4j.com/labs/neosemantics/4.3/mapping/)
