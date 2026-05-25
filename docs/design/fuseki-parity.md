# Fuseki capability parity (v0.5) — design note

Status: implemented. Companion to `neo4j-n10s.md`, `neo4j-bm25.md`, `neo4j-embedding.md`.

Goal: each backend supports the same three capabilities — **reasoning**,
**full-text search**, **vector similarity** — using its own native tech, so
`GRAPH_STORE=fuseki` and `GRAPH_STORE=neo4j` are feature-equivalent.

| Capability | Neo4j | Fuseki |
|------------|-------|--------|
| subClassOf reasoning | Cypher `[:rdfs__subClassOf*0..N]` | query-level SPARQL `?inst a/rdfs:subClassOf* <cls>` |
| full-text (`search_text`) | native fulltext index (`db.index.fulltext.queryNodes`) | jena-text Lucene (`text:query`) |
| vector (`find_similar`, `ontorag embed`) | GDS FastRP + native vector index | `core/fastrp.py` + EmbeddingProvider → **Qdrant** |

## Reasoning (no config change)

`find_entities`/`count_entities` join across the two named graphs:

```sparql
GRAPH <urn:ontorag:data>   { ?inst a ?type . … }
{ GRAPH <urn:ontorag:schema> { ?type rdfs:subClassOf* <cls> . } }
UNION { FILTER(?type = <cls>) }     # direct-match fallback if no TBox loaded
```

rdf:type lives in the data graph, subClassOf in the schema graph; the property
path is evaluated at query time. Fuseki still runs `--config` (TDB2) without an
OWL reasoner — the inference is purely in the query. Converges with Neo4j.

## Full-text — jena-text

Fuseki is started from `docker/fuseki/config.ttl`: a TDB2 dataset wrapped in a
`text:TextDataset` with a Lucene index over `rdfs:label`, `rdfs:comment`,
`skos:prefLabel`, `skos:definition`. `search_text` uses `?s text:query (...)`,
filters to ABox instances (mandatory data-graph rdf:type), and applies the
subClassOf-aware class filter. **Asymmetry vs Neo4j**: jena-text fixes the
indexed predicate list at config time, so Fuseki indexes a curated text-property
set, whereas Neo4j discovers *all* string properties dynamically.

## Vector — Qdrant + pure-Python FastRP

Fuseki has no in-graph vector index and no graph-algorithm library, so:

- **structural**: `core/fastrp.py` (dependency-free FastRP) embeds the ABox
  instance graph (instances + object-property edges extracted via SPARQL).
- **textual**: the shared `EmbeddingProvider` (OpenAI/Ollama) embeds node text.
- Both are upserted to **Qdrant** collections (`ontorag_struct`, `ontorag_text`);
  point id = UUID5(uri), uri in payload. `find_similar` does Qdrant kNN, resolves
  label/class via SPARQL, and fuses modes with RRF (k0=60) — same contract as Neo4j.

### Two-store consistency (the Fuseki-only cost)

Vectors live in Qdrant, the graph in Fuseki — a distributed-state problem Neo4j
avoids (vectors are node properties there). Embeddings are built explicitly via
`ontorag embed` (textual embeddings cost external API calls), never on load.
**Deleted entities leave stale points until the next `ontorag embed`** — re-run
it after `ontorag clear data` or any bulk change.

## Injection safety

All URIs interpolated into SPARQL go through `uri_ref()` (core/sparql.py), which
validates absolute URIs, prefixed names, and urn: forms against a safe charset
(closing a latent hole where non-`://` inputs were previously unvalidated). The
jena-text Lucene query string is escaped for the SPARQL string-literal context.
Qdrant client parameters are bound by the driver.

## Deployment

```bash
docker compose up -d fuseki                  # jena-text enabled (TDB2 + Lucene)
docker compose --profile qdrant up -d qdrant # only if using find_similar/embed
# .[vector] extra provides qdrant-client; EMBEDDING_PROVIDER for textual.
```
