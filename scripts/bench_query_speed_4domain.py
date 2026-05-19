"""Cross-domain phase-timing benchmark.

For each of pokemon / techstack / ods / pure_land:
  1. DROP ALL on Fuseki
  2. Load schema + data
  3. Run agent loop on first N goldset questions (question_ko)
  4. Save per-domain JSON

Then print an aggregated table.

Usage:
    uv run python scripts/bench_query_speed_4domain.py --n 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx
from dotenv import load_dotenv

from ontorag.chat.agent import AgentLoop, _format_schema_for_prompt
from ontorag.llm.factory import get_llm_provider
from ontorag.stores.fuseki import FusekiStore

load_dotenv()

REPO = Path(__file__).resolve().parent.parent
FUSEKI_BASE = "http://localhost:3030/ontorag"

DOMAINS = ["pokemon", "techstack", "ods", "pure_land"]


def drop_all() -> None:
    r = httpx.post(
        f"{FUSEKI_BASE}/update",
        content="DROP ALL",
        headers={"Content-Type": "application/sparql-update"},
        timeout=10,
    )
    r.raise_for_status()


def load_domain(name: str) -> int:
    schema = REPO / "examples" / name / "schema.ttl"
    data = REPO / "examples" / name / "data.ttl"
    subprocess.run(
        ["uv", "run", "ontorag", "load", "schema", str(schema)],
        cwd=REPO, check=True, capture_output=True,
    )
    subprocess.run(
        ["uv", "run", "ontorag", "load", "data", str(data)],
        cwd=REPO, check=True, capture_output=True,
    )
    # Count triples
    r = httpx.get(
        f"{FUSEKI_BASE}/sparql",
        params={"query": "SELECT (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } }"},
        headers={"Accept": "text/csv"},
        timeout=10,
    )
    return int(r.text.strip().splitlines()[-1])


async def run_one(store, llm, schema_ctx, has_data, question: str) -> dict:
    agent = AgentLoop(
        store=store, llm=llm, schema_context=schema_ctx,
        initial_history=None, has_ontology_data=has_data,
    )
    wall_t0 = time.perf_counter()
    summary = None
    tool_calls: list[str] = []
    answer_chunks: list[str] = []
    rate_limits = 0
    async for event in agent.run(question):
        et = event.get("type")
        if et == "tool_call":
            tool_calls.append(event.get("tool", ""))
        elif et == "text":
            answer_chunks.append(event.get("content", ""))
        elif et == "phase_summary":
            summary = event
        elif et == "rate_limit":
            rate_limits += 1
    wall_ms = (time.perf_counter() - wall_t0) * 1000
    if summary is None:
        return {"error": "no phase_summary", "wall_ms": wall_ms}
    return {
        "question": question,
        "wall_ms": wall_ms,
        "total_ms": summary["total_ms"],
        "llm_total_ms": summary["llm_total_ms"],
        "tool_total_ms": summary["tool_total_ms"],
        "n_llm_calls": sum(1 for p in summary["phases"] if p["phase"] == "llm_call"),
        "n_tool_calls": sum(1 for p in summary["phases"] if p["phase"] == "tool_call"),
        "tools_used": tool_calls,
        "rate_limits": rate_limits,
        "answer_preview": "".join(answer_chunks)[:80],
        "prompt_tokens": summary.get("prompt_tokens", 0),
        "cached_tokens": summary.get("cached_tokens", 0),
        "completion_tokens": summary.get("completion_tokens", 0),
        "cache_hit_ratio": summary.get("cache_hit_ratio", 0.0),
    }


async def bench_domain(name: str, n: int) -> dict:
    goldset = REPO / "examples" / name / "goldset.jsonl"
    questions = []
    with goldset.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            q = obj.get("question_ko") or obj.get("question_en")
            if q:
                questions.append({"id": obj.get("id"), "difficulty": obj.get("difficulty"), "q": q})
            if len(questions) >= n:
                break

    store = FusekiStore.from_env()
    llm = get_llm_provider()
    schema = await store.get_schema()
    schema_ctx = _format_schema_for_prompt(schema)
    has_data = any(c.instance_count > 0 for c in schema.classes)

    print(f"  [{name}] running {len(questions)} questions...")
    results = []
    for i, q in enumerate(questions, 1):
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
        pct_llm = llm_t / wall * 100 if wall else 0
        print(
            f"    [{i:2}/{len(questions)}] {q['difficulty']:6} wall={wall:7.0f}  "
            f"llm={llm_t:7.0f} ({pct_llm:4.1f}%)  tools={tool_t:5.0f}  "
            f"nT={r.get('n_tool_calls', 0)}"
        )
    await store.aclose()
    return {"domain": name, "results": results}


def summarize(per_domain: dict[str, dict]) -> None:
    print("\n" + "=" * 110)
    print(
        f"  {'domain':12} {'n':>3} {'wall_p50':>9} {'wall_mean':>10} {'wall_p95':>9} "
        f"{'llm%':>6} {'n_tools':>8} {'prompt_tok':>11} {'cache_hit%':>11}"
    )
    print("-" * 110)
    for name in DOMAINS:
        d = per_domain.get(name)
        if not d:
            continue
        ok = [r for r in d["results"] if "error" not in r]
        if not ok:
            print(f"  {name:12} no successful results")
            continue
        walls = sorted(r["wall_ms"] for r in ok)
        llms = [r["llm_total_ms"] for r in ok]
        n_tools = [r["n_tool_calls"] for r in ok]
        prompt_toks = [r.get("prompt_tokens", 0) for r in ok]
        cache_ratios = [r.get("cache_hit_ratio", 0.0) for r in ok]
        p95_idx = max(0, int(len(walls) * 0.95) - 1)
        print(
            f"  {name:12} {len(ok):>3} "
            f"{statistics.median(walls):>9.0f} "
            f"{statistics.mean(walls):>10.0f} "
            f"{walls[p95_idx]:>9.0f} "
            f"{statistics.mean(llms) / statistics.mean(walls) * 100:>5.1f}% "
            f"{statistics.mean(n_tools):>8.2f} "
            f"{statistics.mean(prompt_toks):>11.0f} "
            f"{statistics.mean(cache_ratios) * 100:>10.1f}%"
        )
    print("=" * 110)

    # Per-tool aggregate
    print("\n  per-tool sum-per-question ms (across all domains):")
    per_tool: dict[str, list[float]] = defaultdict(list)
    for d in per_domain.values():
        for r in d["results"]:
            for t in r.get("tools_used", []):
                # NOTE: we logged tool names but not per-name ms in this version;
                # tool_total_ms is the per-question sum already
                pass
        for r in d["results"]:
            tt = r.get("tool_total_ms")
            if tt is not None:
                per_tool["__all_tools_sum__"].append(tt)
    if per_tool["__all_tools_sum__"]:
        v = per_tool["__all_tools_sum__"]
        print(f"    all-tools per-question  median={statistics.median(v):.0f}ms  mean={statistics.mean(v):.0f}ms")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--out-dir", type=Path, default=Path("bench_speed_results"))
    args = parser.parse_args()

    args.out_dir.mkdir(exist_ok=True)
    per_domain: dict[str, dict] = {}

    for name in DOMAINS:
        print(f"\n→ {name}: drop + load")
        drop_all()
        triple_count = load_domain(name)
        print(f"  loaded {triple_count} triples")
        d = await bench_domain(name, args.n)
        per_domain[name] = d
        out = args.out_dir / f"bench_{name}.json"
        out.write_text(json.dumps(d, ensure_ascii=False, indent=2))
        print(f"  → {out}")

    summarize(per_domain)


if __name__ == "__main__":
    asyncio.run(main())
