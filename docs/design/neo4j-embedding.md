# Neo4j graph embeddings (v2) — design note

Status: design (v0.5 "v2"). Companion to `neo4j-n10s.md`, `neo4j-bm25.md`.

Adds **structural** and **textual** node embeddings + a `find_similar` MCP
tool on the Neo4j backend. Both vectors live as node properties indexed by
Neo4j's native vector index; `find_similar` does kNN via
`db.index.vector.queryNodes`.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Structural embedding | **GDS FastRP** (`gds.fastRP.write`) | GDS Community (free), fast, scalable; verified live |
| Textual embedding | **EmbeddingProvider** (OpenAI / Ollama, `EMBEDDING_PROVIDER`) | mirrors LLM layer; `llm/embedding.py` already shipped |
| Trigger | **explicit** — `ontorag embed [--mode structural\|textual\|both]` | textual = external API cost/time; keep out of `load_rdf` |
| Hybrid combine | **RRF** (reciprocal rank fusion) | structural (e.g. 256-d) and textual (e.g. 1536-d) differ in dim + score scale → fuse at rank level, not vector level |
| Storage | two node props on ABox `:Resource` instances: `_struct_embedding`, `_text_embedding` | separate native vector indexes, one per property |
| API surface | capability tool `find_similar(uri, top_k, mode)` → `SimilarHit` | Neo4j-only, 501 on Fuseki (same as search_text) |

## Verified live (neo4j:5.26, GDS 2.13.10)

- GDS auto-installs via `NEO4J_PLUGINS=["...","graph-data-science"]` (423 procs); `gds.*` in the procedure allowlists.
- `gds.graph.project('g', label, {REL:{orientation:'UNDIRECTED'}})` → catalog projection; `gds.fastRP.write('g', {embeddingDimension, writeProperty, randomSeed})` writes vectors to nodes.
- `CREATE VECTOR INDEX … FOR (n:Label) ON n.prop OPTIONS {indexConfig:{`vector.dimensions`:D, `vector.similarity_function`:'cosine'}}` + `db.index.vector.queryNodes(idx, k, $vec)` returns sensible kNN (self ≈ 1.0).

## build_embeddings(mode)

**structural**: project ABox instances + their object-property relationships into
the GDS catalog (exclude TBox vocab nodes); `gds.fastRP.write` → `_struct_embedding`
(dim from a config constant, e.g. 256, undirected, fixed `randomSeed` for
reproducibility); drop the projection; `CREATE VECTOR INDEX struct_vec` (cosine,
dim). Idempotent: drop+recreate index if dim changed; reuse a fresh projection
name each run.

**textual**: for each ABox instance gather text (rdfs:label + rdfs:comment +
skos:definition + other string props, expanded to readable text), batch through
`EmbeddingProvider.embed`, write `_text_embedding`; `CREATE VECTOR INDEX text_vec`
sized to `provider.dimension`. Skip nodes with no text.

## find_similar(uri, top_k, mode)

- structural / textual: read the start node's `_struct_embedding`/`_text_embedding`,
  `CALL db.index.vector.queryNodes(<idx>, top_k+1, $vec)`, drop the start node,
  map to `SimilarHit(mode=…)`. `uri` is a bound parameter.
- hybrid: run both single-mode queries (each top_k*2), **RRF** fuse:
  `score = Σ 1/(k0 + rank_i)` (k0≈60), sort desc, take top_k, `mode="hybrid"`.
- Empty/missing index or node without an embedding → `[]` (never 500), same
  posture as search_text against a not-ONLINE index.

## Security / safety (carry the adapter lessons)
- `uri` and query vectors are **bound parameters**; index names are hardcoded
  constants; any interpolated label/rel-type/prop-key goes through `_safe_rel`.
- GDS projection uses validated label/rel-type sets, not raw user input.

## Out of scope
- Inductive embeddings (GraphSAGE — GDS Enterprise).
- Auto-re-embed on load (explicit by decision); staleness is the user's call via `ontorag embed`.
- Fuseki parity (no GDS/vector index) — `find_similar` returns 501 on Fuseki.
