from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from ontorag.llm.factory import LLMProvider
from ontorag.stores.base import GraphStore, PatternQuery, PatternTriple

logger = logging.getLogger(__name__)

_SYSTEM_BASE = """\
당신은 RDF 온톨로지 전문 에이전트입니다.

## 툴 사용 규칙 (최우선 — 반드시 준수)

아래 '현재 온톨로지 스키마'에서 **인스턴스 수 합계**를 확인하세요.

**온톨로지에 데이터가 있을 때 (인스턴스 수 > 0인 클래스가 하나라도 있으면)**
→ 사전 학습 지식으로 직접 답변하는 것은 **금지**입니다.
→ 반드시 툴을 호출하여 온톨로지 데이터를 조회한 뒤 그 결과로 답변하세요.
→ "이미 알고 있는 내용이라도" 예외 없음. 온톨로지 데이터가 항상 우선합니다.

**예외 (아래 경우에만 툴 없이 즉시 답변)**
1. 모든 클래스의 인스턴스 수가 0 (온톨로지가 비어 있음)
2. 인사·감사·날씨 등 온톨로지 도메인과 전혀 무관한 질문 (엔티티나 관계를 묻지 않음)

## 질문 유형 → 추천 툴

| 질문 유형 | 추천 툴 |
|-----------|---------|
| "X가 진화하면?", "X의 부모는?", "X에 속한 Y" 등 관계 탐색 | traverse_graph (direction 주의) 또는 find_related |
| "X는 어떤 포켓몬이야?" 단일 엔티티 상세 | describe_entity |
| "모든 불 타입 포켓몬" 클래스 필터 검색 | find_entities (filters 사용) |
| "A와 B는 어떻게 연결돼?" 두 엔티티 경로 | find_path |
| 스키마/클래스 구조 파악 필요 시 | get_schema, get_class_detail |

## URI 처리 규칙

- URI는 반드시 툴이 반환한 값 또는 아래 '현재 스키마'에 나온 URI만 사용하세요. URI나 prefix:name을 직접 구성하거나 추측하는 것은 절대 금지입니다.
- 인스턴스 URI를 모를 때: find_entities(class_uri=<알고있는클래스URI>, filters=[{{"property": "rdfs:label", "op": "=", "value": "이름"}}])로 조회하세요.
- 조회 결과가 없으면 아래 스키마에서 정확한 클래스 URI를 확인하세요.
- 최종 답변에 URI를 절대 노출하지 마세요. 이름(label)만 사용하세요.

## 답변 스타일

- 사용자 질문에 직접 답하세요. 묻지 않은 추가 정보는 생략하거나 한 줄로만 덧붙이세요.
- 엔티티 이름이 명확한 짧은 질문은 get_schema 없이 즉시 find_entities → traverse_graph 순으로 진행하세요.
- 사용자 메시지가 한국어면 한국어로, 영어면 영어로 답변하세요."""


def _local_name(uri: str) -> str:
    """Extract local name from a URI (handles both # and / separators)."""
    if "#" in uri:
        return uri.split("#")[-1]
    return uri.rstrip("/").split("/")[-1]


def _format_schema_for_prompt(schema: Any) -> str:
    """SchemaResult를 시스템 프롬프트용 compact 텍스트로 변환."""
    lines = ["## 현재 온톨로지 스키마 (세션 고정 — get_schema 재호출 불필요)", ""]
    lines.append("### 클래스 (URI | label | 속성수 | 인스턴스수)")
    for cls in schema.classes:
        label = cls.label or "-"
        parent = f" ← {_local_name(cls.parent_uri)}" if cls.parent_uri else ""
        lines.append(f"- {cls.uri} | {label}{parent} | 속성:{cls.property_count} | 인스턴스:{cls.instance_count}")

    ns_relevant = {k: v for k, v in schema.namespaces.items() if k not in ("rdf", "rdfs", "owl", "xsd")}
    if ns_relevant:
        lines.append("")
        lines.append("### 도메인 네임스페이스")
        for prefix, uri in ns_relevant.items():
            lines.append(f"- {prefix}: {uri}")
    return "\n".join(lines)

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
            "시작 엔티티에서 특정 관계를 따라 연결된 엔티티를 탐색합니다. "
            "다음 질문 유형에 이 툴을 우선 사용하세요: "
            "(1) 'X가 진화하면?' → direction=incoming으로 evolvesFrom 역방향 탐색 "
            "(2) 'X의 전체 진화 체인?' → max_depth=3, direction=both "
            "(3) 'X의 부모/자식 관계?' → direction=outgoing 또는 incoming "
            "(4) 'X에 속한 것들?' → outgoing으로 소속 관계 탐색. "
            "direction 규칙: predicate 방향이 '결과→시작' 이면 incoming, '시작→결과' 이면 outgoing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_uri": {"type": "string", "description": "시작 엔티티 URI"},
                "predicate": {"type": "string", "description": "따라갈 predicate URI (없으면 전체 관계)"},
                "max_depth": {"type": "integer", "default": 2, "description": "최대 탐색 깊이 (전체 체인은 3 이상)"},
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
        self._history: list[dict[str, Any]] = list(initial_history) if initial_history else []

    async def run(self, user_message: str) -> AsyncGenerator[dict[str, Any], None]:
        """Run one user turn and yield SSE event dicts until done.

        History is accumulated in self._history so consecutive calls build
        on prior conversation context.
        """
        messages: list[dict[str, Any]] = list(self._history)
        messages.append({"role": "user", "content": user_message})

        for turn in range(self.MAX_TURNS):
            logger.debug("Agent turn %d", turn + 1)
            yield {"type": "thinking", "content": f"분석 중... (턴 {turn + 1})"}

            # Force a tool call on the first LLM call when ontology has data.
            # This prevents the LLM from answering from training knowledge instead
            # of querying the actual ontology graph.
            force_tool = self._has_ontology_data and turn == 0

            response = None
            for _attempt in range(4):  # up to 3 retries on rate limit
                try:
                    response = await self._llm.complete(messages, _TOOLS, self._system, force_tool_use=force_tool)
                    break
                except Exception as exc:
                    wait = _parse_rate_limit_retry(exc)
                    if wait is None or _attempt >= 3:
                        raise
                    logger.warning("Rate limit hit (attempt %d), retrying in %ds", _attempt + 1, wait)
                    yield {"type": "rate_limit", "retry_after": wait}
                    await asyncio.sleep(wait)
            assert response is not None

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
        else:
            # MAX_TURNS reached without end_turn — notify the user
            yield {
                "type": "text",
                "content": "\n\n⚠️ 분석이 최대 턴 수에 도달했습니다. 질문을 더 구체적으로 작성해 보세요.",
            }

        # Persist history for the next run() call
        self._history = messages

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
