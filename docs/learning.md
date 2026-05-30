# Ontology Learning (LLMs4OL)

v0.3 ships the **LLMs4OL pipeline** — an LLM reads plain text and proposes
RDF triples that extend the live ontology. No manual authoring required;
every proposal is validated against the current TBox before it can land in
the ABox.

## Three canonical tasks (EKAW 2023)

| Task | Input | Output | New triple type |
|---|---|---|---|
| **A — Term Typing** | text mention + TBox classes | ranked `(class_uri, confidence)` | `rdf:type` |
| **B — Taxonomy Discovery** | term pair + existing hierarchy | `is_subclass: bool + confidence` | `rdfs:subClassOf` |
| **C — Relation Extraction** | text + entity pair | predicted `predicate_uri + confidence` | `owl:ObjectProperty` assertion |

The pipeline orders them: `text → A → B → C → proposed triples → optional auto-load`.

## CLI

```bash
# Task A — map a text mention to a TBox class
ontorag learn type-term "Pikachu" --context "evolved Pokémon"
ontorag learn type-term "React"

# Task B — propose subClassOf from a corpus
ontorag learn taxonomy --text corpus.txt

# Task C — propose property triples
ontorag learn extract --text corpus.txt

# A+B+C in one shot, with auto-load
ontorag learn populate examples/techstack/corpus.txt --auto-load
```

## Structured ABox population

`populate-structured` reads **CSV / JSON / JSONL** and turns each row into RDF.
The LLM maps columns to TBox property URIs *once* — the mapping is cached in
a sidecar `<file>.mapping.json`, so subsequent runs use zero LLM calls.

```bash
# First run — LLM maps columns, saves pokemon.csv.mapping.json
ontorag learn populate-structured pokemon.csv \
    --class-uri pk:Pokemon --id-column name

# Second run — mapping reused
ontorag learn populate-structured pokemon.csv --yes

# JSON / JSONL — nested keys flatten: {"stats":{"hp":35}} → "stats.hp"
ontorag learn populate-structured pokemon.jsonl --class-uri pk:Pokemon
```

A SHACL validation gate runs against the proposed triples before they hit
the ABox (v0.4 onwards).

## MCP tools

Two L1 tools are exposed to the agent — the LLM can extend its own ontology
*mid-conversation*:

| Tool | Purpose |
|---|---|
| `type_term(term, context?)` | Task A — map text mention to TBox class. |
| `extract_triples(text, entities?)` | Task C — propose RDF triples from text, validated against schema. |

## Design constraints

- All methods receive the **current TBox** (`SchemaResult`) at call time —
  no stale schema cache.
- `predicate_uri` and `class_uri` in outputs must **exist in the current
  TBox** (validated before return). The pipeline never invents URIs.
- A confidence threshold (`min_confidence`, default 0.7) filters weak
  proposals.
- `auto_load=True` calls `store.load_rdf(...)` with `mode="data"` *after*
  validation.

## What's NOT done (out of scope)

- **No new TBox classes proposed automatically.** The pipeline only emits
  ABox triples using *existing* schema. TBox evolution is intentionally a
  human-review step.
- **No DL embeddings.** LLM-prompting only, no transformer fine-tunes.
- **No fully autonomous schema evolution.** v0.3 stays prompt-based — the
  learning layer (GNN / R-GCN) is deferred to v1.1+.

## Where the code lives

- `src/ontorag/learn/term_typing.py` — Task A
- `src/ontorag/learn/taxonomy.py` — Task B
- `src/ontorag/learn/relation.py` — Task C
- `src/ontorag/learn/pipeline.py` — A+B+C orchestration
- `src/ontorag/api/routes/tools/learning.py` — MCP routes

## Further reading

- README §Ontology learning from text — narrative examples with screenshots.
- Design — `docs/design/directory-loader.md` (batch loading after population).
