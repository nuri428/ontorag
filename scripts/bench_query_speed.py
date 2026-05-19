"""Measure ontorag agent-loop phase timings against a goldset.

Usage:
    uv run python scripts/bench_query_speed.py \
        --goldset examples/pure_land/goldset.jsonl \
        --n 5 \
        --out bench_speed_baseline.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from ontorag.chat.agent import AgentLoop, _format_schema_for_prompt
from ontorag.llm.factory import get_llm_provider
from ontorag.stores.fuseki import FusekiStore

load_dotenv()


async def run_one(store: FusekiStore, llm, schema_ctx, has_data: bool, question: str) -> dict:
    agent = AgentLoop(
        store=store,
        llm=llm,
        schema_context=schema_ctx,
        initial_history=None,
        has_ontology_data=has_data,
    )

    wall_t0 = time.perf_counter()
    summary = None
    tool_calls: list[str] = []
    answer_chunks: list[str] = []

    async for event in agent.run(question):
        if event.get("type") == "tool_call":
            tool_calls.append(event.get("tool", ""))
        elif event.get("type") == "text":
            answer_chunks.append(event.get("content", ""))
        elif event.get("type") == "phase_summary":
            summary = event

    wall_ms = (time.perf_counter() - wall_t0) * 1000

    if summary is None:
        return {"error": "no phase_summary", "wall_ms": wall_ms}

    per_tool: dict[str, list[float]] = defaultdict(list)
    for p in summary["phases"]:
        if p["phase"] == "tool_call":
            per_tool[p["tool"]].append(p["ms"])

    return {
        "question": question,
        "wall_ms": wall_ms,
        "total_ms": summary["total_ms"],
        "llm_total_ms": summary["llm_total_ms"],
        "tool_total_ms": summary["tool_total_ms"],
        "overhead_ms": summary["overhead_ms"],
        "n_llm_calls": sum(1 for p in summary["phases"] if p["phase"] == "llm_call"),
        "n_tool_calls": sum(1 for p in summary["phases"] if p["phase"] == "tool_call"),
        "tools_used": tool_calls,
        "tool_ms_breakdown": {k: sum(v) for k, v in per_tool.items()},
        "answer_preview": "".join(answer_chunks)[:120],
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goldset", required=True, type=Path)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--question-field", default="question_ko")
    args = parser.parse_args()

    questions = []
    with args.goldset.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            q = obj.get(args.question_field)
            if q:
                questions.append({"id": obj.get("id"), "difficulty": obj.get("difficulty"), "q": q})
            if len(questions) >= args.n:
                break

    print(f"running {len(questions)} questions through ontorag agent...")

    store = FusekiStore.from_env()
    llm = get_llm_provider()
    schema = await store.get_schema()
    schema_ctx = _format_schema_for_prompt(schema)
    has_data = any(c.instance_count > 0 for c in schema.classes)

    results = []
    for i, q in enumerate(questions, 1):
        print(f"  [{i}/{len(questions)}] {q['difficulty']:6} {q['q'][:60]}")
        try:
            r = await run_one(store, llm, schema_ctx, has_data, q["q"])
        except Exception as exc:
            r = {"error": str(exc), "question": q["q"]}
        r["id"] = q["id"]
        r["difficulty"] = q["difficulty"]
        results.append(r)
        wall = r.get("wall_ms", 0)
        llm_t = r.get("llm_total_ms", 0)
        tool_t = r.get("tool_total_ms", 0)
        print(
            f"      wall={wall:7.0f}ms  llm={llm_t:7.0f}ms ({llm_t/wall*100 if wall else 0:4.1f}%)  "
            f"tools={tool_t:7.0f}ms ({tool_t/wall*100 if wall else 0:4.1f}%)  "
            f"n_llm={r.get('n_llm_calls', 0)} n_tools={r.get('n_tool_calls', 0)}"
        )

    await store.aclose()

    # Aggregate
    ok = [r for r in results if "error" not in r]
    if ok:
        print("\n=== summary ===")
        for field, label in [
            ("wall_ms", "wall total"),
            ("llm_total_ms", "LLM sum"),
            ("tool_total_ms", "tool sum"),
            ("overhead_ms", "overhead"),
        ]:
            vals = [r[field] for r in ok]
            print(
                f"  {label:12} mean={statistics.mean(vals):7.0f}ms  "
                f"median={statistics.median(vals):7.0f}ms  "
                f"min={min(vals):7.0f}ms  max={max(vals):7.0f}ms"
            )

        # Time share
        wall_mean = statistics.mean(r["wall_ms"] for r in ok)
        llm_mean = statistics.mean(r["llm_total_ms"] for r in ok)
        tool_mean = statistics.mean(r["tool_total_ms"] for r in ok)
        print(f"\n  share of wall time:")
        print(f"    LLM   {llm_mean / wall_mean * 100:5.1f}%")
        print(f"    tools {tool_mean / wall_mean * 100:5.1f}%")
        print(f"    other {(wall_mean - llm_mean - tool_mean) / wall_mean * 100:5.1f}%")

        # Per-tool aggregate
        per_tool: dict[str, list[float]] = defaultdict(list)
        for r in ok:
            for tool, ms in r.get("tool_ms_breakdown", {}).items():
                per_tool[tool].append(ms)
        if per_tool:
            print("\n  per-tool sum-per-question (ms):")
            for tool, vals in sorted(per_tool.items()):
                print(
                    f"    {tool:25} mean={statistics.mean(vals):6.0f}  "
                    f"n_questions_used={len(vals)}"
                )

    if args.out:
        args.out.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        print(f"\nresults → {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
