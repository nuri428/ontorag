# ontorag

**Ontology-aware RAG framework.** RDF/OWL ontology as the source of truth for LLM-based retrieval and reasoning.

Unlike typical RAG (chunks + embeddings), **ontorag** treats the ontology schema and instance data as the primary structure. An LLM agent calls ontology-aware MCP tools instead of doing similarity search.

---

## Quickstart

**Prerequisites**: Docker, Docker Compose, an Anthropic (or OpenAI) API key.

```bash
# 1. Clone and configure
git clone https://github.com/yourorg/ontorag
cd ontorag
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY (or OPENAI_API_KEY)

# 2. Start Fuseki + API server
docker compose up

# 3. Load the Pokemon example ontology
ontorag load schema examples/pokemon/schema.ttl
ontorag load data   examples/pokemon/data.ttl

# 4. Chat
ontorag chat
```

Expected chat session:

```
> 포켓몬 목록을 보여줘
  ⟳ 분석 중... (턴 1)
  → get_schema {}
  ← get_schema 결과 수신
  → find_entities {"class_uri": "http://example.org/pokemon#Pokemon", "limit": 20}
  ← find_entities 결과 수신

현재 등록된 포켓몬 목록입니다:
- 이상해씨 (Bulbasaur) — 풀/독 타입
- 파이리 (Charmander) — 불꽃 타입
- 꼬부기 (Squirtle) — 물 타입
...
```

---

## Architecture

```
사용자 (CLI / HTTP)
        │
        ▼ POST /chat  (SSE 스트림)
┌───────────────────────────────────────┐
│           FastAPI Server              │
│                                       │
│  /chat ──▶ AgentLoop                  │
│                │                      │
│           LLM (Claude/GPT/Ollama)     │
│                │ tool_use             │
│   ┌────────────────────────────────┐  │
│   │  L1 intent tools (8):          │  │
│   │  get_schema   find_entities    │  │
│   │  describe_entity count_entities│  │
│   │  traverse_graph  find_path     │  │
│   │  find_related  get_class_detail│  │
│   │  L2 DSL: query_pattern         │  │
│   └────────────┬───────────────────┘  │
└────────────────┼──────────────────────┘
                 │ SPARQL (HTTP)
                 ▼
      Apache Jena Fuseki
```

SSE stream events visible to the client:

```json
{"type": "thinking",    "content": "스키마를 확인합니다..."}
{"type": "tool_call",   "tool": "get_schema",     "content": {}}
{"type": "tool_result", "tool": "get_schema",     "content": {...}}
{"type": "text",        "content": "Person 클래스는..."}
{"type": "done"}
```

---

## Installation

```bash
pip install ontorag
# or with uv:
uv add ontorag
```

Optional LLM providers:

```bash
pip install ontorag openai   # for OpenAI / Ollama
```

---

## Configuration

```bash
# Set provider and API key
ontorag config set --provider anthropic --api-key sk-ant-...
ontorag config set --provider openai    --api-key sk-...
ontorag config set --provider ollama    --ollama-url http://localhost:11434

# Set model
ontorag config set --model claude-opus-4-7
ontorag config set --model gpt-4o

# Set Fuseki URL (default: http://localhost:3030)
ontorag config set --fuseki-url http://localhost:3030

# Show current config
ontorag config show
```

Settings are persisted to `.env` in the current directory.

Environment variables (for Docker / CI):

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `ollama` |
| `LLM_MODEL` | provider default | Model name |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic |
| `OPENAI_API_KEY` | — | Required for OpenAI |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama base URL |
| `FUSEKI_URL` | `http://localhost:3030` | Fuseki SPARQL endpoint |
| `FUSEKI_DATASET` | `ontorag` | Fuseki dataset name |

---

## CLI Reference

```bash
# Load ontology
ontorag load schema <FILE>   # TBox (class/property definitions)
ontorag load data   <FILE>   # ABox (instance data)
ontorag load        <FILE>   # auto-detect

# Server
ontorag serve [--host 0.0.0.0] [--port 8000] [--reload]

# Interactive chat
ontorag chat

# Status
ontorag status

# Config
ontorag config set [OPTIONS]
ontorag config show
```

---

## API

### POST /chat

Stream a chat turn as Server-Sent Events.

```bash
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "포켓몬 종류가 몇 개야?"}'
```

Response stream:

```
data: {"type": "thinking", "content": "분석 중... (턴 1)"}
data: {"type": "tool_call", "tool": "get_schema", "content": {}}
data: {"type": "tool_result", "tool": "get_schema", "content": {...}}
data: {"type": "text", "content": "현재 12종의 포켓몬이 등록되어 있습니다."}
data: {"type": "done"}
```

### MCP Tools (via /mcp)

ontorag exposes its ontology tools as an MCP server at `/mcp`. Any MCP client can connect and call the 9 tools directly.

---

## Example: Pokemon Ontology

The included example demonstrates all framework features:

```
examples/pokemon/
├── schema.ttl   # TBox: Pokemon, Type, Move, Trainer, Region classes
└── data.ttl     # ABox: Kanto region, 12 Pokemon, 3 Trainers, 18 Types
```

Key ontology features used:

- `owl:TransitiveProperty` — `evolvesFrom` (Venusaur → Ivysaur → Bulbasaur)
- `rdfs:subClassOf` — `LegendaryPokemon` inherits from `Pokemon`
- Inference — `find_entities(Pokemon)` includes LegendaryPokemon instances automatically

Sample queries:

```
> 이상해씨의 진화 체인을 알려줘
> 불꽃 타입 포켓몬 목록과 지우의 포켓몬은?
> 뮤츠의 모든 속성을 보여줘
> 물 타입에 약한 포켓몬 찾아줘
```

---

## LLM Providers

| Provider | Env var | Default model |
|---|---|---|
| Anthropic (default) | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o` |
| Ollama (local) | `OLLAMA_BASE_URL` | `llama3.1` |

---

## Docker

```bash
# Development (Fuseki + API with hot-reload)
docker compose up

# Production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Services:

- `fuseki` — Apache Jena Fuseki on port 3030
- `api` — ontorag FastAPI server on port 8000

---

## Positioning

| Framework | Ontology support | LLM agent | Notes |
|---|---|---|---|
| LangChain / LlamaIndex | Minimal | Yes | Code-first RAG, ontology not central |
| Dify | None | Yes | Visual builder |
| GraphRAG (Microsoft) | KG from text | Yes | User-defined ontology weak |
| **ontorag** | **First-class** | **Yes** | RDF/OWL/SPARQL as source of truth |

---

## License

MIT
