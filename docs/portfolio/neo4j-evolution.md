# Neo4j Backend Evolution

> The full narrative is written in Korean. This page is an English summary; for the
> step-by-step decisions, alternatives considered, and retrospective, switch the
> **language selector** in the header to **한국어** — the page at the same URL
> contains the detailed writeup.

## One-line

A second graph store (Neo4j + neosemantics/n10s) was added to ontorag's RDF/SPARQL-native
architecture while preserving full **capability parity** with the existing Fuseki
backend — reasoning, full-text search, and vector similarity all answer identically.

## Headline result

**3-backend deterministic parity** — Fuseki, Neo4j, and FalkorDB return *bit-identical*
results on every protocol metric in `docs/BENCHMARK_v1.md` (`full_parity = True`). The
most compact piece of evidence:

```
count_entities(Pokemon) = 13      # Fuseki = Neo4j = FalkorDB
```

That `13` includes the single `LegendaryPokemon` instance (Mewtwo) surfaced through
`rdfs:subClassOf` inference — i.e. OWL subclass reasoning is live and consistent on
all three backends, each using its own native technology.

## The eight evolution stages

1. **GraphStore Protocol abstraction (v0.1)** — every MCP tool depends only on the
   `Protocol`, so swapping in a new backend is one adapter class + one factory branch.
2. **n10s adapter** — `handleVocabUris=SHORTEN` + `handleRDFTypes=LABELS_AND_NODES`,
   URI ↔ `prefix__local` bidirectional map, prefixes pinned from the TTL pre-import.
3. **OWL `subClassOf` inference parity** — Cypher `[:rdfs__subClassOf*0..N]` on
   Neo4j; **query-level** SPARQL `?inst a/rdfs:subClassOf* <cls>` on Fuseki — no
   Jena reasoner config change, 600+ tests untouched.
4. **L2 DSL translator `pattern_to_cypher`** — mirror of `pattern_to_sparql`, with
   a `_safe_rel` allowlist guarding every interpolation site Cypher cannot bind
   (rel-types, labels, property keys).
5. **BM25 full-text (`search_text`)** — Neo4j native fulltext index / Fuseki
   `jena-text` (Lucene), same `SearchHit` contract, subClassOf-aware class filter.
6. **Vector similarity (`find_similar`)** — Neo4j GDS FastRP + native vector index;
   Fuseki uses a **pure-Python FastRP** (zero deps) + `EmbeddingProvider` → Qdrant.
   `hybrid` mode fuses via RRF (`k0=60`). This pure-Python FastRP also became the
   vector path for the later FalkorDB backend (v0.9).
7. **Multi-ontology scoping** — Fuseki named graphs (`urn:ontorag:{id}:schema/data`)
   ↔ Neo4j node `_ontology` tagging. `ontology=None` keeps the legacy single-ontology
   behaviour exactly.
8. **v0.6.1 follow-up** — `owl:sameAs` transitive+symmetric closure
   (`find_aligned`) and config-driven per-ontology access control
   (`ONTOLOGY_ACCESS`) wrapping the `GraphStore` boundary.

## Where to read further

- **Korean full writeup** — switch the language selector to 한국어 (this same
  page in Korean is ~640 lines, with detailed problem→decision→alternatives→result
  for each stage + a retrospective on five key calls and how the Neo4j-vs-Fuseki
  reasoning divergence was eventually closed).
- **Design notes** — see the Design notes section under Resources in the site
  navigation (Neo4j n10s, BM25 search, embeddings, Fuseki parity, multi-ontology,
  SPARQL approach).
- **Benchmark** — see Benchmark under Resources for the parity table cited above.
