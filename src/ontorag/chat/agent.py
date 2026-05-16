from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from ontorag.llm.anthropic import AnthropicProvider
from ontorag.llm.factory import LLMProvider
from ontorag.stores.base import PatternQuery, PatternTriple
from ontorag.stores.fuseki import FusekiStore

logger = logging.getLogger(__name__)

_SYSTEM = """\
당신은 RDF 온톨로지 전문 에이전트입니다.
사용자의 질문에 답하기 위해 제공된 툴을 사용해 온톨로지 스키마와 인스턴스 데이터를 조회하세요.

툴 호출 전략:
1. get_schema로 도메인 클래스 구조를 파악하세요.
2. 엔티티 URI를 모를 때는 find_entities(filters=[{"property": "rdfs:label", "op": "=", "value": "이름"}])로 URI를 먼저 조회하세요. URI를 절대 추측하지 마세요.
3. 진화·부모·소속 같은 관계 탐색은 traverse_graph를 사용하세요.
4. 특정 클래스의 속성이 필요하면 get_class_detail을 사용하세요.
5. 인스턴스 상세 조회는 describe_entity를 사용하세요.
6. L1 툴로 불가능한 복잡한 쿼리만 query_pattern을 사용하세요.

최종 사용자 답변에 URI를 절대 노출하지 마세요. rdfs:label이나 자연스러운 이름만 사용하고, URI는 도구 호출 입력에만 사용하세요.
항상 한국어로 답변하세요."""

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_schema",
        "description": "온톨로지 클래스 목록과 속성 수를 반환합니다. 먼저 이 툴로 도메인 구조를 파악하세요.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_class_detail",
        "description": "특정 클래스의 속성 목록, 부모/자식 클래스, 인스턴스 샘플을 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "class_uri": {
                    "type": "string",
                    "description": "클래스 URI (예: http://xmlns.com/foaf/0.1/Person)",
                }
            },
            "required": ["class_uri"],
        },
    },
    {
        "name": "find_entities",
        "description": (
            "클래스 인스턴스를 검색합니다. "
            "엔티티 URI를 모를 때 label로 검색하려면 "
            'filters=[{"property": "rdfs:label", "op": "=", "value": "피카츄"}] 처럼 사용하세요.'
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
                                "enum": ["=", "!=", ">", ">=", "<", "<=", "contains", "starts_with"],
                                "default": "=",
                            },
                            "value": {"type": "string", "description": "비교할 값"},
                        },
                        "required": ["property", "value"],
                    },
                },
                "limit": {"type": "integer", "default": 20, "description": "최대 결과 수"},
            },
            "required": ["class_uri"],
        },
    },
    {
        "name": "describe_entity",
        "description": "특정 엔티티의 모든 속성과 관계를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "uri": {"type": "string", "description": "엔티티 URI"}
            },
            "required": ["uri"],
        },
    },
    {
        "name": "count_entities",
        "description": "클래스 인스턴스 수를 집계합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "class_uri": {"type": "string", "description": "클래스 URI"}
            },
            "required": ["class_uri"],
        },
    },
    {
        "name": "traverse_graph",
        "description": (
            "시작 엔티티에서 관계를 따라 연결된 엔티티를 찾습니다. "
            "진화·부모·소속 같은 관계 탐색에 사용하세요. "
            "direction 주의: predicate가 'B evolvesFrom A' 방향이면 "
            "'A가 진화한 것(B)'을 찾으려면 direction=incoming 사용. "
            "예: '피카츄의 진화형?' → traverse_graph(start_uri=피카츄URI, predicate=evolvesFrom, direction=incoming)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_uri": {"type": "string", "description": "시작 엔티티 URI"},
                "predicate": {"type": "string", "description": "따라갈 predicate URI (없으면 전체 관계)"},
                "max_depth": {"type": "integer", "default": 2},
                "direction": {
                    "type": "string",
                    "enum": ["outgoing", "incoming", "both"],
                    "default": "outgoing",
                    "description": "outgoing=start→neighbor, incoming=neighbor→start, both=양방향",
                },
            },
            "required": ["start_uri"],
        },
    },
    {
        "name": "find_path",
        "description": "두 엔티티 간 최단 경로를 찾습니다.",
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
        "name": "find_related",
        "description": "predicate로 연결된 두 클래스 인스턴스 쌍을 찾습니다.",
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
        "description": "JSON 트리플 패턴 DSL로 복잡한 쿼리를 실행합니다. L1 툴로 표현 불가한 경우만 사용하세요.",
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


class AgentLoop:
    """Agentic MCP loop: LLM ↔ ontology tools ↔ SSE stream.

    Drives multi-turn tool-use iteration until the LLM returns end_turn.
    All events are yielded as dicts that the /chat route converts to SSE.
    """

    MAX_TURNS = 12

    def __init__(self, store: FusekiStore, llm: LLMProvider) -> None:
        self._store = store
        self._llm = llm

    async def run(self, user_message: str) -> AsyncGenerator[dict[str, Any], None]:
        """Run one user turn and yield SSE event dicts until done."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message}
        ]

        for turn in range(self.MAX_TURNS):
            logger.debug("Agent turn %d", turn + 1)
            yield {"type": "thinking", "content": f"분석 중... (턴 {turn + 1})"}

            response = await self._llm.complete(messages, _TOOLS, _SYSTEM)

            assistant_blocks: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []

            for block in response.content:
                if block.type == "text":
                    yield {"type": "text", "content": block.text}
                    assistant_blocks.append({"type": "text", "text": block.text})

                elif block.type == "tool_use":
                    yield {"type": "tool_call", "tool": block.name, "content": block.input}
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

                    try:
                        result = await self._call_tool(block.name, block.input)
                    except Exception as exc:
                        logger.warning("Tool %s failed: %s", block.name, exc)
                        result = {"error": str(exc)}

                    yield {"type": "tool_result", "tool": block.name, "content": result}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })

            messages.append({"role": "assistant", "content": assistant_blocks})

            if response.stop_reason == "end_turn":
                break

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

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
