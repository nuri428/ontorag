"""Markdown report generator for ``ontorag eval run`` JSON output.

Turns the JSON report (written by ``ontorag eval run --output FILE``)
into a human-readable Markdown document suitable for:

* attaching to a CI run as an artifact / PR comment,
* embedding in a benchmark blog post,
* archiving as the dated record of a run.

Pure function: no I/O. Callers handle file paths and writing.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any


def _difficulty_order(diff: str) -> int:
    return {"easy": 0, "medium": 1, "hard": 2, "trap": 3}.get(diff, 99)


def generate_markdown_report(report: dict[str, Any]) -> str:
    """Convert an ``eval run`` JSON report dict into Markdown.

    Args:
        report: The dict loaded from the JSON file produced by
            ``ontorag eval run --output``. Expected keys: goldset,
            schema, data, graph_triples, total_questions, failures,
            results (list of per-question entries).

    Returns:
        A Markdown string. Sections: Summary, Difficulty Distribution,
        Category Breakdown, Per-question Detail, Failures (if any).
    """
    lines: list[str] = []

    goldset_name = Path(report["goldset"]).stem
    lines.append(f"# Evaluation Report — {goldset_name}")
    lines.append("")

    # ── Summary ──
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Goldset**: `{report['goldset']}`")
    lines.append(f"- **Schema**: `{report['schema']}`")
    lines.append(f"- **Data**: `{report['data']}`")
    lines.append(f"- **Graph triples**: {report['graph_triples']:,}")
    lines.append(f"- **Total questions**: {report['total_questions']}")
    failures = report["failures"]
    status_marker = "✓" if failures == 0 else "✗"
    lines.append(f"- **Failures**: {failures} {status_marker}")
    lines.append("")

    results = report["results"]

    # ── Difficulty Distribution ──
    lines.append("## Difficulty Distribution")
    lines.append("")
    lines.append("| Difficulty | Total | OK | Errors | Empty rows |")
    lines.append("|---|---:|---:|---:|---:|")

    by_diff: dict[str, list[dict]] = {}
    for r in results:
        by_diff.setdefault(r["difficulty"], []).append(r)

    for diff in sorted(by_diff.keys(), key=_difficulty_order):
        subset = by_diff[diff]
        total = len(subset)
        ok = sum(1 for r in subset if r["status"] == "ok")
        err = sum(1 for r in subset if r["status"] == "error")
        empty = sum(
            1 for r in subset if r["status"] == "ok" and r["row_count"] == 0
        )
        lines.append(f"| {diff} | {total} | {ok} | {err} | {empty} |")
    lines.append("")

    # ── Category Breakdown ──
    cats = Counter(r["category"] for r in results)
    lines.append("## Category Breakdown")
    lines.append("")
    lines.append("| Category | Count |")
    lines.append("|---|---:|")
    for cat, count in sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {cat} | {count} |")
    lines.append("")

    # ── Inference Usage ──
    inf_total = sum(1 for r in results if r["uses_inference"])
    if inf_total:
        lines.append("## Inference Usage")
        lines.append("")
        lines.append(
            f"{inf_total} of {len(results)} questions exercise OWL/RDFS "
            f"inference ({inf_total / len(results):.0%})."
        )
        lines.append("")

    # ── Per-question Detail ──
    lines.append("## Per-question Detail")
    lines.append("")
    lines.append("| ID | Difficulty | Category | Status | Rows | Inference |")
    lines.append("|---|---|---|:---:|---:|:---:|")
    for r in results:
        status = "✓" if r["status"] == "ok" else "✗"
        infer = "✓" if r["uses_inference"] else ""
        lines.append(
            f"| {r['id']} | {r['difficulty']} | {r['category']} | "
            f"{status} | {r['row_count']} | {infer} |"
        )
    lines.append("")

    # ── Failures (if any) ──
    error_results = [r for r in results if r["status"] == "error"]
    if error_results:
        lines.append("## Failures")
        lines.append("")
        for r in error_results:
            err = r.get("error", "(no detail)")
            lines.append(f"### {r['id']} — {r['category']}")
            lines.append("")
            lines.append("```")
            lines.append(err)
            lines.append("```")
            lines.append("")

    return "\n".join(lines)
