# Neo4j BM25 full-text search — design note

Status: design (v0.5 "v1" — BM25 text retrieval). Companion to `neo4j-n10s.md`.

Adds keyword/BM25 text retrieval over instance data on the Neo4j backend,
exposed as an MCP tool. Neo4j ships a Lucene-backed full-text index whose
`db.index.fulltext.queryNodes` returns native BM25 relevance scores — no
external search engine needed.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| API surface | **capability**, not a core `GraphStore` protocol method | mirrors raw-SPARQL (L3) isolation; Fuseki would need jena-text reindex (out of v1 scope) |
| MCP exposure | new L1 tool `search_text` at `POST /tools/search/text`, `hasattr` 501 guard | LLM-callable on Neo4j; clean 501 on Fuseki |
| What is indexed | **all string-valued properties** discovered dynamically (incl. `rdfs:label`) | "텍스트 조회" intent — names *and* comments/definitions/arbitrary text |
| Class scoping | optional `class_uri` filters hits to instances of the class **or subclasses** | consistent with `find_entities` subClassOf inference |
| Result | `SearchHit{uri, label, class_uri, score}` (stores/base.py) | exposes Lucene BM25 score directly |
| Injection | query string is a bound `$query` param; index property keys routed through `_safe_rel` | same posture as the rest of the adapter |

## Index lifecycle

Single named full-text index `ontorag_fulltext` over `:Resource` nodes:

```cypher
CREATE FULLTEXT INDEX ontorag_fulltext IF NOT EXISTS
  FOR (n:Resource) ON EACH [n.`rdfs__label`, n.`pk__name`, ...];   // discovered string props
```

- Property set is discovered after each `load_rdf`: scan `:Resource` nodes for
  string / string-array valued property keys, validate each with `_safe_rel`,
  then **drop + recreate** the index if the property set changed. (Neo4j
  full-text indexes are created over a fixed property list; new text properties
  require recreation.)
- `rdfs__label` is always included when present.

## Query

```cypher
CALL db.index.fulltext.queryNodes('ontorag_fulltext', $query)
  YIELD node, score
WHERE node:Resource
  // optional: AND (node)-[:rdf__type]->()-[:rdfs__subClassOf*0..N]->(:Resource {uri:$class_uri})
RETURN node.uri AS uri, node.`rdfs__label` AS label, score
ORDER BY score DESC LIMIT $limit
```

- `$query` is a Lucene query string passed verbatim as a parameter (BM25 scoring).
- `class_uri` filter reuses the subClassOf-aware pattern from `find_entities`.
- `uri`/`label`/`class_uri` expanded back to full URIs for the `SearchHit`.

## Out of scope (later)
- Fuseki parity (jena-text + Lucene assembler) — deferred; Fuseki returns 501.
- Vector/semantic similarity (`find_similar`) — that is the v2 GDS embedding track.
- Per-property boosting / fielded queries — start flat, revisit if needed.
