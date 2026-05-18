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


def compare_reports(
    report_a: dict[str, Any],
    report_b: dict[str, Any],
    *,
    name_a: str | None = None,
    name_b: str | None = None,
) -> str:
    """Generate a side-by-side Markdown comparison of two ``eval run`` reports.

    Aligns the two reports by question ID (``Q001``…). Rows present in
    one report but not the other are reported in the *Mismatched IDs*
    section — this catches accidentally comparing reports from different
    goldsets.

    Args:
        report_a: Dict loaded from JSON of the first baseline's run.
        report_b: Dict loaded from the second baseline's run.
        name_a: Display name of baseline A (default: stem of its
            ``goldset`` path or ``"A"``).
        name_b: Display name of baseline B (default: ``"B"``).

    Returns:
        Markdown string suitable for blog posts, PR comments, or
        archived comparison artifacts.
    """
    name_a = name_a or _guess_name(report_a, "A")
    name_b = name_b or _guess_name(report_b, "B")

    # Support both `eval run` JSON (id / row_count / status) and
    # `eval bench` JSON (question_id / baseline_answer / aggregate).
    def _row_id(r: dict) -> str:
        return str(r.get("id") or r.get("question_id") or "")

    results_a = {_row_id(r): r for r in report_a.get("results", []) if _row_id(r)}
    results_b = {_row_id(r): r for r in report_b.get("results", []) if _row_id(r)}
    common_ids = sorted(set(results_a) & set(results_b))
    only_a = sorted(set(results_a) - set(results_b))
    only_b = sorted(set(results_b) - set(results_a))

    lines: list[str] = []
    lines.append(f"# Comparison — {name_a} vs {name_b}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **{name_a}**: {report_a.get('total_questions', '?')} questions, {report_a.get('failures', '?')} failures")
    lines.append(f"- **{name_b}**: {report_b.get('total_questions', '?')} questions, {report_b.get('failures', '?')} failures")
    lines.append(f"- **Aligned by ID**: {len(common_ids)}")
    if only_a or only_b:
        lines.append(f"- **Mismatched IDs**: {len(only_a)} only in A, {len(only_b)} only in B")
    lines.append("")

    # Per-difficulty rollup of OK / error / empty for both sides
    lines.append("## Per-difficulty rollup")
    lines.append("")
    lines.append(
        f"| Difficulty | {name_a} OK | {name_a} err | {name_a} empty | "
        f"{name_b} OK | {name_b} err | {name_b} empty |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")

    diffs = sorted(
        {r["difficulty"] for r in (*results_a.values(), *results_b.values())},
        key=_difficulty_order,
    )
    for diff in diffs:
        a_rows = [r for r in results_a.values() if r["difficulty"] == diff]
        b_rows = [r for r in results_b.values() if r["difficulty"] == diff]
        # Treat absent status as 'ok' (eval-bench JSON has no status field).
        def _ok(r):
            return r.get("status", "ok") == "ok"
        def _empty(r):
            cnt = r.get("row_count")
            if cnt is None:
                cnt = r.get("baseline_cited_triple_count", 0)
            return _ok(r) and cnt == 0
        a_ok = sum(1 for r in a_rows if _ok(r))
        a_err = sum(1 for r in a_rows if r.get("status") == "error")
        a_empty = sum(1 for r in a_rows if _empty(r))
        b_ok = sum(1 for r in b_rows if _ok(r))
        b_err = sum(1 for r in b_rows if r.get("status") == "error")
        b_empty = sum(1 for r in b_rows if _empty(r))
        lines.append(
            f"| {diff} | {a_ok} | {a_err} | {a_empty} | {b_ok} | {b_err} | {b_empty} |"
        )
    lines.append("")

    # Per-question side-by-side
    lines.append("## Per-question side-by-side")
    lines.append("")
    lines.append(
        f"| ID | Difficulty | Category | {name_a} rows | {name_b} rows | Δ |"
    )
    lines.append("|---|---|---|---:|---:|:---:|")
    for qid in common_ids:
        ra, rb = results_a[qid], results_b[qid]
        # eval-run JSON: row_count + status; eval-bench JSON: baseline_cited_triple_count
        na = _row_metric(ra)
        nb = _row_metric(rb)
        delta = ""
        if isinstance(na, int) and isinstance(nb, int):
            if na == nb:
                delta = "="
            elif na > nb:
                delta = "▲"
            else:
                delta = "▼"
        lines.append(
            f"| {qid} | {ra.get('difficulty', '?')} | "
            f"{ra.get('category', '?')} | {na} | {nb} | {delta} |"
        )
    lines.append("")

    if only_a or only_b:
        lines.append("## Mismatched IDs")
        lines.append("")
        if only_a:
            lines.append(f"### Only in {name_a}")
            lines.append("")
            for qid in only_a:
                lines.append(f"- `{qid}`")
            lines.append("")
        if only_b:
            lines.append(f"### Only in {name_b}")
            lines.append("")
            for qid in only_b:
                lines.append(f"- `{qid}`")
            lines.append("")

    return "\n".join(lines)


def _guess_name(report: dict[str, Any], fallback: str) -> str:
    goldset = report.get("goldset") or report.get("goldset_path") or ""
    if goldset:
        return Path(goldset).stem
    baseline = report.get("baseline_name")
    if baseline:
        return str(baseline)
    return fallback


def _row_metric(row: dict[str, Any]) -> int | str:
    """Pick the best 'count' to show in a side-by-side row.

    eval-run JSON has ``row_count`` + ``status``. eval-bench JSON has
    ``baseline_cited_triple_count``. Returns the int when present,
    or ``'✗'`` for error rows, or ``'?'`` if neither field is present.
    """
    if row.get("status") == "error":
        return "✗"
    if "row_count" in row:
        return int(row["row_count"])
    if "baseline_cited_triple_count" in row:
        return int(row["baseline_cited_triple_count"])
    return "?"


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
