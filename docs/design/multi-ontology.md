# Multi-ontology per instance (v0.5) — design note

Status: in progress. One ontorag instance hosts **multiple ontologies**, each
loaded and queryable in isolation, with optional cross-ontology (union) queries.

## Decision: named-graph scoping + `ontology` parameter

Chosen over per-dataset/per-DB isolation: keeps cross-ontology queries cheap and
avoids per-ontology routing/infra. Every tool gains an optional `ontology`
scope; the GraphStore protocol threads an `ontology: str | None` argument.

## Ontology id → storage mapping

| | `ontology=None` (default) | `ontology="<id>"` |
|---|---|---|
| **Fuseki** named graphs | `urn:ontorag:schema` / `urn:ontorag:data` (the existing pair) | `urn:ontorag:{id}:schema` / `urn:ontorag:{id}:data` |
| **Neo4j** | no `_ontology` filter (all nodes) | nodes tagged `_ontology = "{id}"` |

- `id` is a short slug (e.g. `pokemon`, `foaf`); validated `^[a-zA-Z0-9_-]+$`.
- **Backward compatibility (hard requirement)**: `ontology=None` must behave
  exactly as today — load to the legacy default graphs, query the union of
  everything. The 600+ existing single-ontology tests must stay green.

## Semantics

- **Load** (`load_rdf(path, mode, ontology=None)`): None → legacy default graphs;
  id → the per-ontology graph pair. Neo4j tags imported nodes with `_ontology`.
- **Query** (read tools gain `ontology=None`):
  - None → **union across all ontologies** (including the default). Fuseki relies
    on `tdb2:unionDefaultGraph true` (already set) — query the default graph
    instead of a specific `GRAPH`. Neo4j applies no `_ontology` filter.
  - id → scope to that ontology only. Fuseki: `GRAPH <urn:ontorag:{id}:data>` /
    `…:schema`. Neo4j: `WHERE n._ontology = $id`.
- **clear_graph / status / dump**: also accept `ontology` (None → all/default).

## Graph-URI helper (single source of truth)

Both the data and schema graph URIs are derived from one helper so scoping logic
lives in one place:

```
data_graph(ontology)   -> "urn:ontorag:data"            if None
                          "urn:ontorag:{id}:data"        otherwise
schema_graph(ontology) -> "urn:ontorag:schema" / "urn:ontorag:{id}:schema"
```

For union (`ontology=None`) reads, Fuseki queries the union default graph (no
`GRAPH` wrapper); for a specific id it wraps in the per-ontology `GRAPH`.

## Neo4j wrinkle

Neo4j/n10s has no named graphs, so isolation is node-level:
- After `n10s.rdf.import`, tag the just-loaded resources with `_ontology = $id`
  (set on `:Resource` nodes lacking the property, or scoped to the import).
- Every read scopes by `_ontology` when an id is given. The shared schema vocab
  (rdf/rdfs/owl) may be cross-ontology — handle by not filtering vocab nodes.
- `_ontology=None` queries are unfiltered (current behavior preserved).

## Scope of change
- Protocol: `ontology` arg on load_rdf + read tools + clear/status/dump.
- Fuseki: graph-URI helper + every query method scopes.
- Neo4j: import tagging + every query method scopes by `_ontology`.
- Routes/CLI: optional `ontology` field; `ontorag load --ontology <id>`.
- Injection: `ontology` id validated against `^[a-zA-Z0-9_-]+$` before any
  interpolation into a graph URI / Cypher.

## Out of scope
- Per-ontology access control / quotas.
- Cross-ontology entity alignment / owl:sameAs resolution.
