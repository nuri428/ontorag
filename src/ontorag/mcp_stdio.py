"""Standalone stdio MCP server (v1.1).

Lets ontorag's ontology tools drop into any MCP client (Claude Desktop, Cursor,
Claude Code) via a one-line stdio config — the client spawns this process, no
running ontorag HTTP server required. The backend is selected by ``GRAPH_STORE``
exactly like everywhere else, so the same stdio entrypoint serves Fuseki / Neo4j
/ FalkorDB.

This is *separate* from the in-process HTTP ``/mcp`` endpoint (fastapi-mcp):
that one is for clients talking to a live API; this one is client-spawned over
stdio. Both expose the same GraphStore protocol — this server just calls
``create_store()`` and the store methods directly, serializing dataclass results
to JSON ``TextContent``.

Entry point (``[mcp]`` extra):  ``ontorag-mcp``  →  ``ontorag.mcp_stdio:main``

Example client config (Claude Desktop / Cursor):

    {
      "mcpServers": {
        "ontorag": {
          "command": "ontorag-mcp",
          "env": {"GRAPH_STORE": "fuseki", "FUSEKI_URL": "http://localhost:3030"}
        }
      }
    }
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of store result types to JSON-serializable data."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if hasattr(obj, "model_dump"):  # pydantic
        return obj.model_dump()
    if isinstance(obj, list):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


# Tool name → (description, JSON-schema properties, required). Kept minimal: the
# highest-value read tools + two reasoning tools. raw SPARQL is never exposed
# (same policy as the HTTP /mcp surface).
_TOOLS: dict[str, dict[str, Any]] = {
    "get_schema": {
        "description": "Ontology schema overview: classes, properties, hierarchy.",
        "properties": {"ontology": {"type": "string", "description": "Ontology id; omit for all."}},
        "required": [],
    },
    "get_class_detail": {
        "description": "Full detail for one class: properties, parents, children, sample instances.",
        "properties": {"class_uri": {"type": "string"}, "ontology": {"type": "string"}},
        "required": ["class_uri"],
    },
    "find_entities": {
        "description": "Instances of a class (subClassOf-inferred). Optional limit.",
        "properties": {
            "class_uri": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
            "ontology": {"type": "string"},
        },
        "required": ["class_uri"],
    },
    "describe_entity": {
        "description": "All properties + relationships of one entity (inverseOf included).",
        "properties": {"uri": {"type": "string"}, "ontology": {"type": "string"}},
        "required": ["uri"],
    },
    "count_entities": {
        "description": "Count instances of a class (subClassOf-inferred).",
        "properties": {"class_uri": {"type": "string"}, "ontology": {"type": "string"}},
        "required": ["class_uri"],
    },
    "aggregate": {
        "description": "Group instances of a class by a property and aggregate (count/sum/avg/min/max).",
        "properties": {
            "class_uri": {"type": "string"},
            "group_by": {"type": "string", "description": "Property URI to group by."},
            "agg": {"type": "string", "enum": ["count", "sum", "avg", "min", "max"], "default": "count"},
            "ontology": {"type": "string"},
        },
        "required": ["class_uri", "group_by"],
    },
    "traverse_graph": {
        "description": "BFS from a node (TransitiveProperty closure when a predicate is given).",
        "properties": {
            "start_uri": {"type": "string"},
            "predicate": {"type": "string"},
            "max_depth": {"type": "integer", "default": 2},
        },
        "required": ["start_uri"],
    },
    "find_path": {
        "description": "Shortest path between two entities.",
        "properties": {
            "uri_a": {"type": "string"},
            "uri_b": {"type": "string"},
            "max_depth": {"type": "integer", "default": 5},
        },
        "required": ["uri_a", "uri_b"],
    },
    "assert_triple": {
        "description": (
            "Insert a single (subject, predicate, object) triple into the knowledge graph. "
            "Use this to store facts, decisions, relationships, and agent memory. "
            "Subject and predicate must be URIs (full or prefixed). "
            "Object is a plain string literal by default; set object_is_uri=true for URI objects."
        ),
        "properties": {
            "subject": {"type": "string", "description": "Subject URI (e.g. 'urn:agent:memory:task1')."},
            "predicate": {"type": "string", "description": "Predicate URI (e.g. 'rdfs:label', 'urn:agent:hasDecision')."},
            "object": {"type": "string", "description": "Object — literal string or URI (see object_is_uri)."},
            "object_is_uri": {"type": "boolean", "default": False, "description": "Set true when object is a URI reference."},
            "ontology": {"type": "string", "description": "Ontology/graph id to write into (omit for default ABox)."},
        },
        "required": ["subject", "predicate", "object"],
    },
    "retract_triple": {
        "description": (
            "Remove a specific triple from the knowledge graph (no-op if absent). "
            "All three terms must match the stored triple exactly."
        ),
        "properties": {
            "subject": {"type": "string"},
            "predicate": {"type": "string"},
            "object": {"type": "string"},
            "object_is_uri": {"type": "boolean", "default": False},
            "ontology": {"type": "string"},
        },
        "required": ["subject", "predicate", "object"],
    },
    "assert_triples": {
        "description": (
            "Insert multiple triples in one batch operation. "
            "More efficient than repeated assert_triple calls when storing structured records."
        ),
        "properties": {
            "triples": {
                "type": "array",
                "description": "List of triples to insert.",
                "items": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "predicate": {"type": "string"},
                        "object": {"type": "string"},
                        "object_is_uri": {"type": "boolean", "default": False},
                    },
                    "required": ["subject", "predicate", "object"],
                },
            },
            "ontology": {"type": "string"},
        },
        "required": ["triples"],
    },
    "compute_posterior": {
        "description": "Bayesian P(query | evidence) over the stored network ([bayes] extra).",
        "properties": {
            "query": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "object", "additionalProperties": {"type": "string"}},
            "ontology": {"type": "string"},
        },
        "required": ["query"],
    },
    "do_query": {
        "description": "Causal P(query | do(intervention)) — interventional, back-door adjusted ([bayes] extra).",
        "properties": {
            "query": {"type": "array", "items": {"type": "string"}},
            "do": {"type": "object", "additionalProperties": {"type": "string"}},
            "evidence": {"type": "object", "additionalProperties": {"type": "string"}},
            "ontology": {"type": "string"},
        },
        "required": ["query", "do"],
    },
}


async def _dispatch(store: Any, name: str, args: dict[str, Any]) -> Any:
    """Route an MCP tool call to the store / reasoning engine and return data."""
    from ontorag.stores.base import AggFunc, TraversalDirection  # noqa: PLC0415

    if name == "get_schema":
        return await store.get_schema(ontology=args.get("ontology"))
    if name == "get_class_detail":
        return await store.get_class_detail(args["class_uri"], ontology=args.get("ontology"))
    if name == "find_entities":
        return await store.find_entities(
            args["class_uri"], limit=args.get("limit", 50), ontology=args.get("ontology")
        )
    if name == "describe_entity":
        return await store.describe_entity(args["uri"], ontology=args.get("ontology"))
    if name == "count_entities":
        return await store.count_entities(args["class_uri"], ontology=args.get("ontology"))
    if name == "aggregate":
        return await store.aggregate(
            args["class_uri"], args["group_by"],
            AggFunc(args.get("agg", "count")), ontology=args.get("ontology"),
        )
    if name == "traverse_graph":
        return await store.traverse(
            args["start_uri"], predicate=args.get("predicate"),
            max_depth=args.get("max_depth", 2), direction=TraversalDirection.both,
        )
    if name == "find_path":
        return await store.find_path(args["uri_a"], args["uri_b"], max_depth=args.get("max_depth", 5))
    if name == "assert_triple":
        await store.assert_triple(
            args["subject"], args["predicate"], args["object"],
            object_is_uri=args.get("object_is_uri", False),
            ontology=args.get("ontology"),
        )
        return {"status": "asserted", "triple": {"s": args["subject"], "p": args["predicate"], "o": args["object"]}}
    if name == "retract_triple":
        await store.retract_triple(
            args["subject"], args["predicate"], args["object"],
            object_is_uri=args.get("object_is_uri", False),
            ontology=args.get("ontology"),
        )
        return {"status": "retracted", "triple": {"s": args["subject"], "p": args["predicate"], "o": args["object"]}}
    if name == "assert_triples":
        raw = args["triples"]
        triples = [(t["subject"], t["predicate"], t["object"], t.get("object_is_uri", False)) for t in raw]
        count = await store.assert_triples(triples, ontology=args.get("ontology"))
        return {"status": "asserted", "count": count}
    if name in ("compute_posterior", "do_query"):
        bn_getter = getattr(store, "get_bayes_network", None)
        if bn_getter is None:
            raise ValueError("This backend does not support the reasoning layer.")
        bn = await bn_getter(ontology=args.get("ontology"))
        if bn is None:
            raise ValueError("No Bayesian network stored — load one with `ontorag bayes load`.")
        if name == "compute_posterior":
            from ontorag.bayes.engine import BayesianEngine  # noqa: PLC0415

            return await BayesianEngine(bn).compute_posterior(
                args.get("evidence", {}), list(args["query"])
            )
        # do_query
        from ontorag.causal.engine import CausalEngine  # noqa: PLC0415

        causal = None
        cgetter = getattr(store, "get_causal_model", None)
        if cgetter is not None:
            causal = await cgetter(ontology=args.get("ontology"))
        return await CausalEngine(bn, causal).do_query(
            args.get("do", {}), list(args["query"]), args.get("evidence", {})
        )
    raise ValueError(f"Unknown tool: {name}")


def build_server():
    """Construct the low-level MCP Server with ontorag's tools registered."""
    from mcp.server import Server  # noqa: PLC0415
    import mcp.types as types  # noqa: PLC0415

    server: Server = Server("ontorag")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=spec["description"],
                inputSchema={
                    "type": "object",
                    "properties": spec["properties"],
                    "required": spec["required"],
                },
            )
            for name, spec in _TOOLS.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        from ontorag.stores.factory import create_store  # noqa: PLC0415

        store = create_store()
        try:
            result = await _dispatch(store, name, arguments or {})
            payload = _to_jsonable(result)
            return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))]
        except Exception as exc:  # surface a clean error to the MCP client
            logger.exception("ontorag MCP tool %s failed", name)
            detail: dict[str, Any] = {"error": str(exc), "type": exc.__class__.__name__}
            # Include HTTP response body for 4xx/5xx debugging
            resp = getattr(exc, "response", None)
            if resp is not None:
                detail["http_status"] = resp.status_code
                detail["http_body"] = resp.text[:1000]
            return [types.TextContent(type="text", text=json.dumps(detail, ensure_ascii=False, default=str))]
        finally:
            try:
                await store.aclose()
            except Exception:  # noqa: BLE001
                pass

    return server


def main() -> None:
    """Console entry point: run the stdio MCP server (blocks)."""
    import asyncio  # noqa: PLC0415

    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv()

    async def _run() -> None:
        from mcp.server.stdio import stdio_server  # noqa: PLC0415

        server = build_server()
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
