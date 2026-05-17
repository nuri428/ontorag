# Tech Stack Ontology Example

This example shows two things ontorag can do that a plain vector-search RAG cannot:

1. **OWL transitive reasoning** — ask "what does Next.js depend on?" and get the full chain
   (Next.js → React → Node.js), even though `Next.js dependsOn Node.js` was never written explicitly.
2. **LLMs4OL (v0.3)** — feed a text corpus to `ontorag learn populate` and extend the ontology
   with new frameworks extracted automatically, no manual RDF authoring needed.

---

## Prerequisites

```bash
docker compose up -d                    # Fuseki + API
cp .env.example .env                    # set ANTHROPIC_API_KEY or OPENAI_API_KEY
```

---

## Step 1 — Load the seed ontology

```bash
# TBox: class and property definitions
uv run ontorag load schema examples/techstack/schema.ttl

# ABox: 15 seed instances (React, Next.js, Node.js, TypeScript, …)
uv run ontorag load data examples/techstack/data.ttl

uv run ontorag status
# Graph store: connected
# TBox triples:  ~60
# ABox triples:  ~80
```

---

## Step 2 — Chat with the seed ontology

```bash
uv run ontorag serve          # http://localhost:8000/ui
# or
uv run ontorag chat
```

### Queries that exercise OWL reasoning

**Transitive dependency chain** (`ts:dependsOn owl:TransitiveProperty`)

```
> What does Next.js depend on?
```
Expected: Next.js → React → Node.js  
*(React dependsOn Node.js is explicit; Next.js dependsOn Node.js is inferred by Fuseki.)*

**Class hierarchy** (`ts:FrontendFramework rdfs:subClassOf ts:Framework rdfs:subClassOf ts:Technology`)

```
> List all frameworks
```
Expected: React, Angular, Next.js, Remix — even though React and Angular are FrontendFramework
and Next.js/Remix are FullstackFramework. `find_entities(Framework)` picks them all up via subClassOf.

**Cross-class join**

```
> Which technologies are maintained by Vercel?
```
Expected: Next.js, SvelteKit (after Step 3), Turborepo (after Step 3).

---

## Step 3 — Extend with LLMs4OL (v0.3)

`corpus.txt` describes 10 technologies not in `data.ttl` (SvelteKit, Svelte, Astro, Deno, Fresh, …).
Run the A+B+C pipeline to extract and load them automatically:

```bash
uv run ontorag learn populate examples/techstack/corpus.txt
```

You will see:

```
Task A — 타입 매핑 (10건)
텀             클래스 URI                              신뢰도
SvelteKit      http://example.org/techstack#FullstackFramework   0.94
Svelte         http://example.org/techstack#FrontendFramework    0.91
Astro          http://example.org/techstack#FullstackFramework   0.89
Deno           http://example.org/techstack#RuntimeEnvironment   0.96
...

Task C — RDF 트리플 (18건)
주어        서술어          목적어                               신뢰도
SvelteKit   dependsOn       techstack#Vite                       0.92
SvelteKit   maintainedBy    techstack#Vercel                     0.95
Svelte      dependsOn       techstack#NodeJS                     0.88
Astro       dependsOn       techstack#Vite                       0.91
...

위 항목을 Fuseki에 로드하시겠습니까? [y/N]: y
✓ 38개 트리플을 ABox에 로드했습니다.
```

### After loading — queries that now work

```
> Which fullstack frameworks depend on Vite?        # SvelteKit, Astro — new from corpus
> What runtime does Fresh use?                      # Deno — new from corpus
> List all technologies maintained by Vercel        # Now includes SvelteKit, Turborepo
> Which tools supersede an existing technology?     # Bun→Node.js, pnpm→npm, Deno→Node.js, Biome→ESLint
```

---

## Ontology highlights

| OWL feature | Example | Effect |
|---|---|---|
| `owl:TransitiveProperty` | `ts:dependsOn` | A→B→C makes A→C queryable without explicit triple |
| `rdfs:subClassOf` | `FrontendFramework ⊆ Framework ⊆ Technology` | `find_entities(Technology)` returns all 25+ items |
| `owl:ObjectProperty` | `ts:maintainedBy` | Cross-entity join: "who maintains React?" |
| `owl:DatatypeProperty` | `ts:license`, `ts:version` | Filter: "all MIT-licensed frameworks" |

---

## Individual LLMs4OL commands

```bash
# Task A only — map a term to a TBox class
uv run ontorag learn type-term "Remix"
uv run ontorag learn type-term "esbuild" --context "JavaScript bundler written in Go"

# Task B only — propose subClassOf from text
uv run ontorag learn taxonomy examples/techstack/corpus.txt

# Task C only — extract triples without loading
uv run ontorag learn extract examples/techstack/corpus.txt --min-confidence 0.8

# Full pipeline, skip confirmation prompt
uv run ontorag learn populate examples/techstack/corpus.txt --yes
```

---

## Files

| File | Description |
|---|---|
| `schema.ttl` | TBox — 9 classes, 7 object properties, 4 data properties |
| `data.ttl` | ABox seed — 15 technologies, 5 organizations |
| `corpus.txt` | Plain-text description of 10 additional technologies for LLMs4OL |
