"""Native ontorag baseline — wraps :class:`AgentLoop` as a :class:`RAGBaseline`.

This is the *real* ontorag head-to-head competitor for vector-RAG
baselines. Each ``answer(question)`` call spawns a fresh
:class:`~ontorag.chat.agent.AgentLoop` against the same Fuseki store the
production chat endpoint uses, so the measurement reflects what an end
user would actually get from ``ontorag chat``.

Design choices:

* **Per-question agent** — a new ``AgentLoop`` per question so
  conversation history does not leak between goldset rows.
* **Cited triples from tool results** — every tool result is walked for
  entity URIs; for each URI we read all triples ``(uri, ?, ?)`` from the
  graph passed at construction time. Returned as rdflib ``Node`` tuples
  (matching :mod:`ontorag.eval.baselines.mocks`) so the hallucination
  metric's ``(s, p, o) in graph`` check works without datatype/lang loss.
* **Schema context is fetched once** at construction and reused across
  questions — the production code does the same per request, but here
  we amortise the cost.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rdflib import Graph, URIRef

from ontorag.chat.agent import AgentLoop, _format_schema_for_prompt
from ontorag.eval.baselines.protocol import BaselineAnswer
from ontorag.llm.factory import LLMProvider
from ontorag.stores.base import GraphStore

logger = logging.getLogger(__name__)


_MAX_CITED_TRIPLES = 20
_MAX_TRIPLES_PER_URI = 5


def _collect_uris(value: Any, into: list[str]) -> None:
    """Recursively walk a tool_result and collect entity URI strings.

    Looks for: ``uri`` keys, ``start_uri`` keys, dicts inside ``edges``
    with ``s``/``p``/``o`` keys, and strings that *look like* HTTP URIs.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("uri", "start_uri", "end_uri", "s", "p", "o", "source", "target"):
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    into.append(v)
            else:
                _collect_uris(v, into)
    elif isinstance(value, list):
        for item in value:
            _collect_uris(item, into)


def _triples_for_uris(graph: Graph, uris: list[str]) -> list[tuple[Any, Any, Any]]:
    """Look up outgoing triples for each URI; cap per-URI and total."""
    seen_uris: set[str] = set()
    cited: list[tuple[Any, Any, Any]] = []
    for u in uris:
        if u in seen_uris:
            continue
        seen_uris.add(u)
        node = URIRef(u)
        count = 0
        for triple in graph.triples((node, None, None)):
            cited.append(triple)
            count += 1
            if count >= _MAX_TRIPLES_PER_URI:
                break
            if len(cited) >= _MAX_CITED_TRIPLES:
                return cited
        if len(cited) >= _MAX_CITED_TRIPLES:
            break
    return cited


class OntoragNativeBaseline:
    """Real ontorag chat agent wrapped as a :class:`RAGBaseline`.

    Args:
        store: GraphStore the agent will query (typically a
            :class:`~ontorag.stores.fuseki.FusekiStore`).
        llm: LLM provider used for each agent turn.
        graph: Local rdflib graph mirroring what the store sees. Used to
            recover cited triples from tool result URIs.
        schema_context: Pre-formatted schema string for the agent's
            system prompt. If None, fetched lazily from the store.
        has_ontology_data: If True, force a tool call on the first turn
            so the LLM cannot answer from training memory.
    """

    name = "ontorag_native"
    version = "0.1.0"

    def __init__(
        self,
        store: GraphStore,
        llm: LLMProvider,
        graph: Graph,
        *,
        schema_context: str | None = None,
        has_ontology_data: bool | None = None,
    ) -> None:
        """Synchronous constructor — does NOT await on the store.

        Schema context is fetched lazily on the first :meth:`answer` call,
        so the FusekiStore's httpx client gets created inside the same
        asyncio loop that BenchRunner uses. Pre-fetching from a separate
        ``asyncio.run`` would bind the client to a dead loop and every
        subsequent tool call would fail with "Event loop is closed".
        """
        self._store = store
        self._llm = llm
        self._graph = graph
        self._schema_context = schema_context
        self._has_ontology_data = has_ontology_data
        self._initialised = schema_context is not None and has_ontology_data is not None

    async def _ensure_schema(self) -> None:
        if self._initialised:
            return
        try:
            schema = await self._store.get_schema()
            self._schema_context = _format_schema_for_prompt(schema)
            self._has_ontology_data = any(
                cls.instance_count > 0 for cls in schema.classes
            )
        except Exception as exc:
            logger.warning("schema load failed at first answer(): %s", exc)
            self._schema_context = None
            self._has_ontology_data = False
        self._initialised = True

    async def answer(self, question: str) -> BaselineAnswer:
        await self._ensure_schema()
        agent = AgentLoop(
            store=self._store,
            llm=self._llm,
            schema_context=self._schema_context,
            initial_history=None,  # fresh history per question
            has_ontology_data=bool(self._has_ontology_data),
        )

        text_parts: list[str] = []
        tool_calls = 0
        tool_call_sequence: list[str] = []
        tool_trace: list[dict[str, Any]] = []
        uri_pool: list[str] = []
        rate_limit_hits = 0
        error: str | None = None

        start = time.perf_counter()
        try:
            async for event in agent.run(question):
                t = event.get("type")
                if t == "text":
                    text_parts.append(event.get("content", ""))
                elif t == "tool_call":
                    tool_calls += 1
                    name_ = event.get("tool", "")
                    tool_call_sequence.append(name_)
                    tool_trace.append(
                        {
                            "tool": name_,
                            "args": event.get("content"),
                            "result_summary": None,
                        }
                    )
                elif t == "tool_result":
                    content = event.get("content")
                    _collect_uris(content, uri_pool)
                    if tool_trace:
                        # Pair with most recent tool_call for goldset triage
                        if isinstance(content, list):
                            summary: Any = {"kind": "list", "len": len(content)}
                        elif isinstance(content, dict):
                            summary = {
                                "kind": "dict",
                                "keys": list(content.keys())[:8],
                                "nodes_len": len(content.get("nodes", []))
                                if isinstance(content.get("nodes"), list)
                                else None,
                            }
                        else:
                            summary = {"kind": type(content).__name__}
                        tool_trace[-1]["result_summary"] = summary
                elif t == "rate_limit":
                    rate_limit_hits += 1
                elif t == "error":
                    error = event.get("content")
        except Exception as exc:  # pragma: no cover — surfaces in extra
            logger.exception("OntoragNativeBaseline.answer crashed")
            error = str(exc)

        latency_ms = (time.perf_counter() - start) * 1000
        cited_triples = _triples_for_uris(self._graph, uri_pool)

        return BaselineAnswer(
            text="".join(text_parts).strip(),
            cited_triples=cited_triples,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            extra={
                "baseline_kind": "ontology_native",
                "tool_call_sequence": tool_call_sequence,
                "tool_trace": tool_trace,
                "rate_limit_hits": rate_limit_hits,
                "error": error,
            },
        )

    async def close(self) -> None:
        closer = getattr(self._store, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception as exc:  # pragma: no cover
                logger.warning("store.aclose failed: %s", exc)
