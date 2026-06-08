"""Multi-agent ontorag baseline — wraps :class:`MultiAgentLoop` as a
:class:`RAGBaseline`.

This is the v1.2 evaluator-optimizer head-to-head against
``ontorag_native``: same store, same LLM, same schema context, same
cited-triple recovery — only the chat loop changes. Running both
baselines through the same ``BenchRunner`` lets ``ontorag eval bench``
attribute any score delta to the multi-agent loop itself, holding
everything else constant.

Beyond the BaselineAnswer fields shared with ``ontorag_native``, the
``extra`` dict surfaces the three v1.2-specific signals:

* ``route`` — the router decision (complexity tier + matched classes
  + linguistic signals).
* ``iterations`` — count of evaluator-optimizer iterations actually
  consumed.
* ``evaluations`` — list of per-iteration verdicts and the three
  reflection-axis scores.

Eval reports can slice on these to answer questions like *"on the
multi-hop subset, how often did MULTI_STEP route fire and how often
did persistence find sufficient context within 2 iterations?"*.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from rdflib import Graph

from ontorag.chat.agent import _format_schema_for_prompt
from ontorag.chat.multi_agent.loop import MultiAgentLoop
from ontorag.eval.baselines.ontorag_native import _collect_uris, _triples_for_uris
from ontorag.eval.baselines.protocol import BaselineAnswer
from ontorag.llm.factory import LLMProvider
from ontorag.stores.base import GraphStore

logger = logging.getLogger(__name__)


class OntoragMultiagentBaseline:
    """Real ontorag v1.2 multi-agent loop wrapped as a :class:`RAGBaseline`.

    Construction mirrors :class:`OntoragNativeBaseline` so the two can
    swap into the same ``BenchRunner`` invocation without other
    configuration changes.
    """

    name = "ontorag_multiagent"
    version = "0.1.0"

    def __init__(
        self,
        store: GraphStore,
        llm: LLMProvider,
        graph: Graph,
        *,
        schema_context: str | None = None,
        has_ontology_data: bool | None = None,
        bayes_engine: Any | None = None,
        max_iterations: int | None = None,
    ) -> None:
        """Sync constructor — schema is fetched lazily on first answer().

        Defers ``store.get_schema()`` until inside the BenchRunner's
        event loop so the store's HTTP client is bound to the right
        loop (same rationale as :class:`OntoragNativeBaseline`).
        """
        self._store = store
        self._llm = llm
        self._graph = graph
        self._schema_context = schema_context
        self._has_ontology_data = has_ontology_data
        self._bayes_engine = bayes_engine
        self._max_iterations = max_iterations
        self._initialised = (
            schema_context is not None and has_ontology_data is not None
        )

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

        loop_kwargs: dict[str, Any] = {
            "store": self._store,
            "llm": self._llm,
            "schema_context": self._schema_context,
            "has_ontology_data": bool(self._has_ontology_data),
            "bayes_engine": self._bayes_engine,
        }
        if self._max_iterations is not None:
            loop_kwargs["max_iterations"] = self._max_iterations
        agent = MultiAgentLoop(**loop_kwargs)

        text_parts: list[str] = []
        tool_calls = 0
        tool_call_sequence: list[str] = []
        uri_pool: list[str] = []
        rate_limit_hits = 0
        error: str | None = None

        route_event: dict[str, Any] | None = None
        iterations_seen = 0
        evaluations: list[dict[str, Any]] = []

        start = time.perf_counter()
        try:
            async for event in agent.run(question):
                t = event.get("type")
                if t == "text":
                    text_parts.append(event.get("content", ""))
                elif t == "tool_call":
                    tool_calls += 1
                    tool_call_sequence.append(event.get("tool", ""))
                elif t == "tool_result":
                    _collect_uris(event.get("content"), uri_pool)
                elif t == "rate_limit":
                    rate_limit_hits += 1
                elif t == "error":
                    error = event.get("content")
                elif t == "route":
                    # Single event per run — capture for the extra dict.
                    route_event = {
                        "complexity": event.get("complexity"),
                        "rationale": event.get("rationale"),
                        "matched_classes": event.get("matched_classes", []),
                        "hop_signals": event.get("hop_signals", []),
                        "reasoning_signals": event.get("reasoning_signals", []),
                    }
                elif t == "iteration":
                    iterations_seen = max(iterations_seen, event.get("iteration", 0))
                elif t == "evaluate":
                    evaluations.append(
                        {
                            "verdict": event.get("verdict"),
                            "axes": event.get("axes"),
                            "rationale": event.get("rationale"),
                        }
                    )
        except Exception as exc:  # pragma: no cover
            logger.exception("OntoragMultiagentBaseline.answer crashed")
            error = str(exc)

        latency_ms = (time.perf_counter() - start) * 1000
        cited_triples = _triples_for_uris(self._graph, uri_pool)

        return BaselineAnswer(
            text="".join(text_parts).strip(),
            cited_triples=cited_triples,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            extra={
                "baseline_kind": "ontology_multiagent",
                "tool_call_sequence": tool_call_sequence,
                "rate_limit_hits": rate_limit_hits,
                "error": error,
                "route": route_event,
                "iterations": iterations_seen,
                "evaluations": evaluations,
            },
        )

    async def close(self) -> None:
        closer = getattr(self._store, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception as exc:  # pragma: no cover
                logger.warning("store.aclose failed: %s", exc)
