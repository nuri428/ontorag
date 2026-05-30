# Web UI

After `ontorag serve`, open **<http://localhost:8000/ui>** in your browser.
The UI is HTMX-driven, server-rendered, and has 4 tabs:

| Tab | What it does | Backing tools |
|---|---|---|
| 📐 **Schema** | TBox class hierarchy as a Cytoscape graph | `get_schema`, SHACL validate |
| 📊 **Data** | ABox instances per class + entity drill-down | `find_entities`, `describe_entity`, `traverse_graph` |
| 🛝 **Playground** | Chat with the LLM agent, watch tool calls live | All MCP tools |
| 🧮 **Reasoning** | Bayesian / Causal interactive runner (v0.8.4) | `compute_posterior`, `mpe`, `do_query`, `counterfactual`, `identify_effect` |

A language toggle (KO / EN) lives in the top nav, and every tab keeps its
state across HTMX swaps.

## Schema tab

Interactive class hierarchy:

- Click a node → highlight its neighbourhood (subclasses, properties).
- Double-click → reset.
- **Upload TBox** — TTL / JSON-LD / RDF-XML, *always replace* mode (schema
  changes are destructive by design — incremental schema edits go through
  `ontorag learn` instead).
- **Validation** — Syntax check (rdflib parse) and SHACL conformance check
  against an inline SHAPES TTL.

![Schema tab](https://github.com/nuri428/ontorag/raw/main/assets/TBox.png)

## Data tab

Pick a class, browse instances:

- Click any row → entity detail panel with all properties + a depth-2
  neighbourhood graph.
- **Upload ABox** — append *or* replace mode (data is incremental).
- Object-property values render as readable `label (URI)` chips on every
  backend (Fuseki / Neo4j / FalkorDB) — see commit `8a4c00f`.

Search panel under the same tab wraps `search_text` (BM25), `find_similar`
(vector kNN), and `aggregate` (group-by + count/sum/avg) — the v0.5
capability tools.

![Data tab](https://github.com/nuri428/ontorag/raw/main/assets/ABox.png)

## Playground tab

Chat with the agent. Tool calls appear in real time as they execute:

- A `find_entities` call shows class + filters before the result.
- Query results that contain graph data render as an interactive result
  graph (same Cytoscape engine as the Schema tab).
- **History** sidebar — sessions are persisted to `chat.db`; switch and
  resume.
- **LLM settings** in-tab — change provider/model/key without restarting
  the server. The change is written to `.env` and applies to the next chat.
- Rate-limit handling — when an LLM API rate-limits, a `retry_after` banner
  counts down and the turn resumes automatically.

![Playground tab](https://github.com/nuri428/ontorag/raw/main/assets/playground.png)

## Reasoning tab (v0.8.4)

Two sub-tabs over the existing HTMX-partial pattern. Requires the `[bayes]`
extra and a Bayesian network loaded via `ontorag bayes load`.

### Bayesian

- Build evidence (`variable = state`) and pick query variables.
- **Compute posterior** → `P(query | evidence)` rendered as distribution
  bars (`partials/dist_bars.html`).
- **MPE** → most-probable explanation (argmax joint).

### Causal

With a DAG loaded (`ontorag causal load`):

- `do(X)` interventions
- `counterfactual` queries (observed + intervention)
- `identify` — minimal back-door + all front-door adjustment sets

Highlights:

- The **DAG edges** are listed alongside the form so you always know what
  graph you're reasoning over.
- A **"do(X)로 비교 →"** link on a posterior result seeds the Causal tab
  with the *same* evidence as an intervention — the see ≠ do contrast in
  two clicks.
- v1.1 — the result bars now include a **"why:" trace** under the
  distribution: the back-door adjustment set that the engine used, plus a
  one-line "why do ≠ see" summary.

### Capability guards

When no backend / network / `pgmpy` is available, the tab renders an
actionable amber hint (`partials/reasoning_error.html`) instead of an
error — tells you exactly which env var or extra is missing.

## Where the code lives

- Routes — `src/ontorag/web/router.py`
- Templates — `src/ontorag/web/templates/` (Jinja2)
- Shared partials — `templates/partials/dist_bars.html`, `instances_grid.html`,
  `reasoning_error.html`

## Tests

Web UI behaviour is regression-tested at the route level in
`tests/test_web_reasoning.py` (10 tests, capability guards run without
pgmpy; happy-path asserts see 0.72 ≠ do 0.60).
