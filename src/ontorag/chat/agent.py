from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from ontorag._prompts import load as _load_prompt
from ontorag.llm.factory import LLMProvider
from ontorag.stores.base import GraphStore, PatternQuery, PatternTriple

logger = logging.getLogger(__name__)

_SYSTEM_BASE = _load_prompt("ontorag.chat.prompts", "agent.txt").rstrip()


def _local_name(uri: str) -> str:
    """Extract local name from a URI (handles both # and / separators)."""
    if "#" in uri:
        return uri.split("#")[-1]
    return uri.rstrip("/").split("/")[-1]


def _format_schema_for_prompt(schema: Any) -> str:
    """SchemaResult를 시스템 프롬프트용 compact 텍스트로 변환.

    Single source of truth = TBox. Emits class/property URIs, hierarchy,
    OWL flags, and counts. rdfs:comment / skos:definition are NOT inlined —
    they are retrievable on-demand via get_class_detail(uri), keeping the
    system prompt small enough to stay within model context windows and to
    benefit from prompt caching.
    """
    lines = ["## Ontology schema (TBox — single source of truth)", ""]
    lines.append("### Classes (URI | label | parent | #properties | #instances)")
    for cls in schema.classes:
        label = cls.label or "-"
        parent = f" ← {_local_name(cls.parent_uri)}" if cls.parent_uri else ""
        lines.append(
            f"- {cls.uri} | {label}{parent} | props={cls.property_count} | "
            f"instances={cls.instance_count}"
        )

    # Properties — exact URIs (LLM must reuse verbatim) + OWL characteristics.
    # Author rdfs:comment lives in get_class_detail, not here.
    props = getattr(schema, "properties", None) or []
    if props:
        lines.append("")
        lines.append(
            "### Properties (URI | label | type | domain → range | OWL flags)"
        )
        for p in props:
            label = p.label or "-"
            dom = _local_name(p.domain_uri) if p.domain_uri else "?"
            rng = _local_name(p.range_uri) if p.range_uri else "?"
            flags: list[str] = []
            if getattr(p, "is_transitive", False):
                flags.append("TRANSITIVE")
            inv = getattr(p, "inverse_of_uri", None)
            if inv:
                flags.append(f"inverseOf={_local_name(inv)}")
            flag_str = (" | " + ", ".join(flags)) if flags else ""
            lines.append(
                f"- {p.uri} | {label} | {p.prop_type} | {dom} → {rng}{flag_str}"
            )

    ns_relevant = {
        k: v
        for k, v in schema.namespaces.items()
        if k not in ("rdf", "rdfs", "owl", "xsd")
    }
    if ns_relevant:
        lines.append("")
        lines.append("### Domain namespaces")
        for prefix, uri in ns_relevant.items():
            lines.append(f"- {prefix}: {uri}")
    return "\n".join(lines)


_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_schema",
        "description": (
            "Returns the full TBox overview (classes, properties, OWL "
            "characteristics). The system prompt already embeds this — "
            "call only if you suspect the schema changed mid-session."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_class_detail",
        "description": (
            "Returns class metadata including the author's rdfs:comment / "
            "skos:definition (natural-language meaning), properties whose "
            "domain is this class, rdfs:subClassOf parents and children, "
            "and a sample of ABox instances. Call this when you need the "
            "author's description of a class to disambiguate its semantics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "class_uri": {
                    "type": "string",
                    "description": "Class URI (verbatim from schema, e.g. http://xmlns.com/foaf/0.1/Person)",
                }
            },
            "required": ["class_uri"],
        },
    },
    {
        "name": "find_entities",
        "description": (
            "Returns ABox instances of a class (rdfs:subClassOf-aware — "
            "subclass instances included automatically). Optional filters "
            "are AND-combined; rdfs:label filters with `=` are "
            "case-insensitive and language-tag-insensitive. "
            "Result shape: list[{uri, label, class_uri, properties}]. "
            "Empty list = no matching instances in the data graph."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "class_uri": {"type": "string", "description": "클래스 URI"},
                "filters": {
                    "type": "array",
                    "description": "필터 조건 목록. rdfs:label로 이름 검색 가능.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "property": {
                                "type": "string",
                                "description": "속성 URI 또는 prefixed name (예: rdfs:label, pk:hp)",
                            },
                            "op": {
                                "type": "string",
                                "enum": [
                                    "=",
                                    "!=",
                                    ">",
                                    ">=",
                                    "<",
                                    "<=",
                                    "contains",
                                    "starts_with",
                                ],
                                "default": "=",
                            },
                            "value": {"type": "string", "description": "비교할 값"},
                        },
                        "required": ["property", "value"],
                    },
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "최대 결과 수",
                },
            },
            "required": ["class_uri"],
        },
    },
    {
        "name": "describe_entity",
        "description": (
            "Returns the full property graph of one ABox entity — outgoing "
            "triples (predicate, object) AND incoming triples (via "
            "owl:inverseOf when available). Use to ground a specific URI "
            "in everything the ontology knows about it. "
            "Result shape: {uri, label, class_uri, properties}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"uri": {"type": "string", "description": "Instance URI (verbatim)."}},
            "required": ["uri"],
        },
    },
    {
        "name": "count_entities",
        "description": (
            "Counts instances of a class (rdfs:subClassOf-aware). "
            "Use for 'How many X' questions. Returns a single integer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "class_uri": {"type": "string", "description": "Class URI."}
            },
            "required": ["class_uri"],
        },
    },
    {
        "name": "traverse_graph",
        "description": (
            "Generic BFS up to max_depth hops from a start entity. "
            "Use for **non-transitive** multi-hop questions where the "
            "exact predicate or depth is unknown. "
            "**Prefer `property_path_query`** whenever the predicate is "
            "flagged TRANSITIVE in the schema — that gives a single "
            "round-trip closure with a cleaner result shape. "
            "direction: outgoing | incoming (use for inverseOf-style "
            "reversal) | both. Result shape: {start_uri, nodes:[{uri, "
            "label, depth}], edges:[{from, to, predicate, predicate_label}], "
            "depth_reached}. Only nodes with depth>0 are the answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_uri": {"type": "string", "description": "시작 엔티티 URI"},
                "predicate": {
                    "type": "string",
                    "description": "따라갈 predicate URI (없으면 전체 관계)",
                },
                "max_depth": {
                    "type": "integer",
                    "default": 2,
                    "description": "최대 탐색 깊이 (전체 체인은 3 이상)",
                },
                "direction": {
                    "type": "string",
                    "enum": ["outgoing", "incoming", "both"],
                    "default": "outgoing",
                    "description": "outgoing=시작→이웃, incoming=이웃→시작(역방향), both=양방향",
                },
            },
            "required": ["start_uri"],
        },
    },
    {
        "name": "find_path",
        "description": (
            "Shortest path between two known instance URIs (BFS, any "
            "predicate). Result shape: traversal record with the path "
            "in `nodes`/`edges`. Empty path means the two are disconnected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "uri_a": {"type": "string"},
                "uri_b": {"type": "string"},
                "max_depth": {"type": "integer", "default": 4},
            },
            "required": ["uri_a", "uri_b"],
        },
    },
    {
        "name": "property_path_query",
        "description": (
            "Native owl:TransitiveProperty closure (SPARQL `predicate+`). "
            "Predicate MUST be one flagged `TRANSITIVE` in the schema. "
            "Three modes (provide inputs for exactly one):\n"
            "  • Instance: pass `start_uri` — closure from that URI.\n"
            "  • Label lookup: pass `start_label` (and optional "
            "`start_class_uri` to disambiguate) — store does the "
            "rdfs:label → URI lookup in the same round-trip.\n"
            "  • Class-wide: pass only `start_class_uri` (no start_uri/label) "
            "— closure from EVERY instance of that class, results unioned. "
            "Use this for questions like 'any X is transitively …' / "
            "'all members of class C transitively related via P'.\n"
            "Result shape: list[{uri, label}]. Length > 0 ⇒ every item "
            "is part of the answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "predicate_uri": {
                    "type": "string",
                    "description": "Predicate URI to follow transitively (must be flagged TRANSITIVE).",
                },
                "start_uri": {
                    "type": "string",
                    "description": "Instance URI to start from. Use this when known.",
                },
                "start_label": {
                    "type": "string",
                    "description": "rdfs:label of the start instance. Used when start_uri is unknown.",
                },
                "start_class_uri": {
                    "type": "string",
                    "description": "Optional class URI to disambiguate the label lookup.",
                },
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["predicate_uri"],
        },
    },
    {
        "name": "find_related",
        "description": (
            "One-hop JOIN over a single predicate: every (a, b) pair where "
            "`?a a class_uri_a . ?a predicate ?b . ?b a class_uri_b`. "
            "Use for direct relations between class extents. For multi-hop "
            "or transitive variants use traverse_graph / property_path_query. "
            "Result shape: list[{entity_a, entity_b}]."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "class_uri_a": {"type": "string"},
                "predicate": {"type": "string"},
                "class_uri_b": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["class_uri_a", "predicate", "class_uri_b"],
        },
    },
    {
        "name": "query_pattern",
        "description": (
            "JSON triple-pattern DSL — safe SPARQL escape hatch when no "
            "intent tool covers the query (e.g. arbitrary multi-variable "
            "joins). Inputs are structurally validated; no injection risk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "select": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "반환할 변수 목록 (예: ['?person', '?name'])",
                },
                "where": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "s": {"type": "string"},
                            "p": {"type": "string"},
                            "o": {"type": "string"},
                        },
                        "required": ["s", "p", "o"],
                    },
                    "description": "트리플 패턴 목록",
                },
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["select", "where"],
        },
    },
]


def _parse_rate_limit_retry(exc: Exception) -> int | None:
    """Return retry delay in seconds if exc is a 429 rate-limit error, else None."""
    if "RateLimit" in type(exc).__name__:
        resp = getattr(exc, "response", None)
        headers = dict(getattr(resp, "headers", {}) or {}) if resp else {}
        for key in ("retry-after", "x-ratelimit-reset-requests"):
            val = headers.get(key)
            if val:
                try:
                    return max(5, int(float(val)))
                except (ValueError, TypeError):
                    pass
        return 30
    if getattr(exc, "status_code", None) == 429:
        return 30
    return None


class AgentLoop:
    """Agentic MCP loop: LLM ↔ ontology tools ↔ SSE stream.

    Drives multi-turn tool-use iteration until the LLM returns end_turn.
    All events are yielded as dicts that the /chat route converts to SSE.
    Conversation history is preserved across calls to run() for REPL sessions.
    """

    MAX_TURNS = 12

    def __init__(
        self,
        store: GraphStore,
        llm: LLMProvider,
        schema_context: str | None = None,
        initial_history: list[dict[str, Any]] | None = None,
        has_ontology_data: bool = False,
    ) -> None:
        self._store = store
        self._llm = llm
        self._has_ontology_data = has_ontology_data
        self._system = (
            f"{_SYSTEM_BASE}\n\n{schema_context}" if schema_context else _SYSTEM_BASE
        )
        self._history: list[dict[str, Any]] = (
            list(initial_history) if initial_history else []
        )

    async def run(self, user_message: str) -> AsyncGenerator[dict[str, Any], None]:
        """Run one user turn and yield SSE event dicts until done.

        History is accumulated in self._history so consecutive calls build
        on prior conversation context.
        """
        messages: list[dict[str, Any]] = list(self._history)
        messages.append({"role": "user", "content": user_message})

        phase_timings: list[dict[str, Any]] = []
        run_started = time.perf_counter()

        for turn in range(self.MAX_TURNS):
            logger.debug("Agent turn %d", turn + 1)
            yield {"type": "thinking", "content": f"분석 중... (턴 {turn + 1})"}

            # Force a tool call on the first LLM call when ontology has data.
            # This prevents the LLM from answering from training knowledge instead
            # of querying the actual ontology graph.
            force_tool = self._has_ontology_data and turn == 0

            response = None
            llm_t0 = time.perf_counter()
            for _attempt in range(4):  # up to 3 retries on rate limit
                try:
                    response = await self._llm.complete(
                        messages, _TOOLS, self._system, force_tool_use=force_tool
                    )
                    break
                except Exception as exc:
                    wait = _parse_rate_limit_retry(exc)
                    if wait is None or _attempt >= 3:
                        raise
                    logger.warning(
                        "Rate limit hit (attempt %d), retrying in %ds",
                        _attempt + 1,
                        wait,
                    )
                    yield {"type": "rate_limit", "retry_after": wait}
                    await asyncio.sleep(wait)
            assert response is not None
            phase_timings.append(
                {
                    "phase": "llm_call",
                    "turn": turn,
                    "ms": (time.perf_counter() - llm_t0) * 1000,
                    "usage": getattr(response, "usage", None),
                }
            )

            assistant_blocks: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []

            for block in response.content:
                if block.type == "text":
                    yield {"type": "text", "content": block.text}
                    assistant_blocks.append({"type": "text", "text": block.text})

                elif block.type == "tool_use":
                    yield {
                        "type": "tool_call",
                        "tool": block.name,
                        "content": block.input,
                    }
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

                    tool_t0 = time.perf_counter()
                    try:
                        result = await self._call_tool(block.name, block.input)
                    except Exception as exc:
                        logger.warning("Tool %s failed: %s", block.name, exc)
                        result = {"error": str(exc)}
                    phase_timings.append(
                        {
                            "phase": "tool_call",
                            "turn": turn,
                            "tool": block.name,
                            "ms": (time.perf_counter() - tool_t0) * 1000,
                        }
                    )

                    yield {"type": "tool_result", "tool": block.name, "content": result}
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_blocks})

            if response.stop_reason == "end_turn":
                break

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            # MAX_TURNS reached without end_turn — notify the user
            yield {
                "type": "text",
                "content": "\n\n⚠️ 분석이 최대 턴 수에 도달했습니다. 질문을 더 구체적으로 작성해 보세요.",
            }

        # Persist history for the next run() call
        self._history = messages

        total_ms = (time.perf_counter() - run_started) * 1000
        llm_total = sum(p["ms"] for p in phase_timings if p["phase"] == "llm_call")
        tool_total = sum(p["ms"] for p in phase_timings if p["phase"] == "tool_call")
        prompt_tokens = sum(
            (p.get("usage") or {}).get("prompt_tokens", 0)
            for p in phase_timings if p["phase"] == "llm_call"
        )
        cached_tokens = sum(
            (p.get("usage") or {}).get("cached_tokens", 0)
            for p in phase_timings if p["phase"] == "llm_call"
        )
        completion_tokens = sum(
            (p.get("usage") or {}).get("completion_tokens", 0)
            for p in phase_timings if p["phase"] == "llm_call"
        )
        yield {
            "type": "phase_summary",
            "total_ms": total_ms,
            "llm_total_ms": llm_total,
            "tool_total_ms": tool_total,
            "overhead_ms": total_ms - llm_total - tool_total,
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "completion_tokens": completion_tokens,
            "cache_hit_ratio": (cached_tokens / prompt_tokens) if prompt_tokens else 0.0,
            "phases": phase_timings,
        }

        yield {"type": "done"}

    async def _call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Dispatch a tool call to the graph store."""
        store = self._store

        if name == "get_schema":
            return (await store.get_schema()).model_dump()

        if name == "get_class_detail":
            return (await store.get_class_detail(args["class_uri"])).model_dump()

        if name == "find_entities":
            from ontorag.stores.base import EntityFilter, FilterOp

            raw_filters = args.get("filters") or []
            filters = [
                EntityFilter(
                    property=f["property"],
                    op=FilterOp(f.get("op", "=")),
                    value=f["value"],
                )
                for f in raw_filters
            ] or None
            results = await store.find_entities(
                class_uri=args["class_uri"],
                filters=filters,
                limit=args.get("limit", 20),
            )
            return [r.model_dump() for r in results]

        if name == "describe_entity":
            return (await store.describe_entity(args["uri"])).model_dump()

        if name == "count_entities":
            return await store.count_entities(args["class_uri"])

        if name == "traverse_graph":
            from ontorag.stores.base import TraversalDirection

            direction = TraversalDirection(args.get("direction", "outgoing"))
            result = await store.traverse(
                start_uri=args["start_uri"],
                predicate=args.get("predicate"),
                max_depth=args.get("max_depth", 2),
                direction=direction,
            )
            return result.model_dump()

        if name == "find_path":
            result = await store.find_path(
                uri_a=args["uri_a"],
                uri_b=args["uri_b"],
                max_depth=args.get("max_depth", 4),
            )
            return result.model_dump()

        if name == "property_path_query":
            return await store.property_path_closure(
                predicate_uri=args["predicate_uri"],
                start_uri=args.get("start_uri"),
                start_label=args.get("start_label"),
                start_class_uri=args.get("start_class_uri"),
                limit=args.get("limit", 100),
            )

        if name == "find_related":
            return await store.find_related(
                class_uri_a=args["class_uri_a"],
                predicate=args["predicate"],
                class_uri_b=args["class_uri_b"],
                limit=args.get("limit", 20),
            )

        if name == "query_pattern":
            query = PatternQuery(
                select=args["select"],
                where=[PatternTriple(**t) for t in args["where"]],
                limit=args.get("limit", 100),
            )
            return (await store.query_pattern(query)).model_dump()

        raise ValueError(f"Unknown tool: {name}")
