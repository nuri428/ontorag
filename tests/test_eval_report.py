"""Tests for ontorag.eval.report — Markdown report generation."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from ontorag.cli import app
from ontorag.eval.report import compare_reports, generate_markdown_report

REPO_ROOT = Path(__file__).resolve().parent.parent
PURE_LAND = REPO_ROOT / "examples" / "pure_land"

runner = CliRunner()


def _sample_report() -> dict:
    """Minimal fake JSON report payload with one of each difficulty."""
    return {
        "goldset": "examples/pure_land/goldset.jsonl",
        "schema": "examples/pure_land/schema.ttl",
        "data": "examples/pure_land/data.ttl",
        "graph_triples": 948,
        "total_questions": 4,
        "failures": 0,
        "results": [
            {
                "id": "Q001",
                "difficulty": "easy",
                "category": "single_entity",
                "uses_inference": False,
                "row_count": 1,
                "status": "ok",
            },
            {
                "id": "Q005",
                "difficulty": "medium",
                "category": "multi_hop",
                "uses_inference": False,
                "row_count": 5,
                "status": "ok",
            },
            {
                "id": "Q008",
                "difficulty": "hard",
                "category": "transitive_inference",
                "uses_inference": True,
                "row_count": 2,
                "status": "ok",
            },
            {
                "id": "Q010",
                "difficulty": "trap",
                "category": "hallucination_trap",
                "uses_inference": False,
                "row_count": 1,
                "status": "ok",
            },
        ],
    }


# ── generate_markdown_report (pure function) ──────────────────────────────────


class TestGenerateMarkdownReport:
    def test_includes_summary_section(self):
        md = generate_markdown_report(_sample_report())
        assert "# Evaluation Report" in md
        assert "## Summary" in md
        assert "948" in md  # graph_triples with comma formatting

    def test_difficulty_table_present(self):
        md = generate_markdown_report(_sample_report())
        assert "## Difficulty Distribution" in md
        for diff in ("easy", "medium", "hard", "trap"):
            assert f"| {diff} |" in md

    def test_difficulty_order_is_easy_medium_hard_trap(self):
        md = generate_markdown_report(_sample_report())
        easy_idx = md.index("| easy |")
        medium_idx = md.index("| medium |")
        hard_idx = md.index("| hard |")
        trap_idx = md.index("| trap |")
        assert easy_idx < medium_idx < hard_idx < trap_idx

    def test_category_breakdown_present(self):
        md = generate_markdown_report(_sample_report())
        assert "## Category Breakdown" in md
        assert "transitive_inference" in md

    def test_inference_section_when_used(self):
        md = generate_markdown_report(_sample_report())
        assert "## Inference Usage" in md
        # 1 of 4 uses inference => 25%
        assert "25%" in md

    def test_inference_section_omitted_when_zero(self):
        rpt = _sample_report()
        for r in rpt["results"]:
            r["uses_inference"] = False
        md = generate_markdown_report(rpt)
        assert "## Inference Usage" not in md

    def test_per_question_detail_lists_all_ids(self):
        md = generate_markdown_report(_sample_report())
        for qid in ("Q001", "Q005", "Q008", "Q010"):
            assert qid in md

    def test_failure_section_emitted_only_when_errors_exist(self):
        rpt = _sample_report()
        md_ok = generate_markdown_report(rpt)
        assert "## Failures" not in md_ok

        rpt["results"][0]["status"] = "error"
        rpt["results"][0]["error"] = "Syntax error at line 1"
        rpt["failures"] = 1
        md_err = generate_markdown_report(rpt)
        assert "## Failures" in md_err
        assert "Syntax error at line 1" in md_err
        assert "✗" in md_err

    def test_pass_marker_when_zero_failures(self):
        md = generate_markdown_report(_sample_report())
        assert "Failures**: 0 ✓" in md


# ── CLI integration: ontorag eval report ──────────────────────────────────────


class TestEvalReportCLI:
    def test_report_from_real_run(self, tmp_path):
        """End-to-end: run eval → generate report from its JSON output."""
        json_out = tmp_path / "run.json"
        run = runner.invoke(
            app,
            [
                "eval",
                "run",
                str(PURE_LAND / "goldset.jsonl"),
                "--schema",
                str(PURE_LAND / "schema.ttl"),
                "--data",
                str(PURE_LAND / "data.ttl"),
                "--output",
                str(json_out),
            ],
        )
        assert run.exit_code == 0
        assert json_out.exists()

        md_out = tmp_path / "report.md"
        rep = runner.invoke(
            app, ["eval", "report", str(json_out), "--output", str(md_out)]
        )
        assert rep.exit_code == 0
        assert md_out.exists()
        md_text = md_out.read_text(encoding="utf-8")
        assert "# Evaluation Report" in md_text
        assert "Q001" in md_text
        assert "Q050" in md_text  # all 50 in detail table

    def test_report_to_stdout(self, tmp_path):
        # Create a synthetic JSON file and ask for stdout output
        json_path = tmp_path / "fake.json"
        json_path.write_text(json.dumps(_sample_report()), encoding="utf-8")
        result = runner.invoke(app, ["eval", "report", str(json_path)])
        assert result.exit_code == 0
        assert "# Evaluation Report" in result.stdout

    def test_report_missing_input_fails(self):
        result = runner.invoke(app, ["eval", "report", "/no/such/file.json"])
        assert result.exit_code != 0


# ── compare_reports (pure function) ───────────────────────────────────────────


def _report_b_with_changes() -> dict:
    """A second sample report where one question's row count differs
    and one question is replaced with a different ID."""
    rpt = _sample_report()
    # Replace Q010 → Q011 (mismatched IDs scenario)
    rpt["results"][-1] = {
        **rpt["results"][-1],
        "id": "Q011",
    }
    # Change Q008's row count to trigger Δ
    for r in rpt["results"]:
        if r["id"] == "Q008":
            r["row_count"] = 5
    return rpt


class TestCompareReports:
    def test_compare_includes_both_names(self):
        md = compare_reports(_sample_report(), _sample_report(),
                             name_a="ontorag", name_b="LangChain")
        assert "ontorag" in md
        assert "LangChain" in md
        assert "Comparison" in md

    def test_compare_aligns_common_ids(self):
        md = compare_reports(_sample_report(), _sample_report())
        # All 4 IDs present in both, so all should appear in the per-question table
        for qid in ("Q001", "Q005", "Q008", "Q010"):
            assert qid in md

    def test_compare_marks_delta_correctly(self):
        md = compare_reports(_sample_report(), _report_b_with_changes(),
                             name_a="A", name_b="B")
        # Q008: A row_count=2, B row_count=5 → ▼ from A's perspective
        # (na < nb means delta = ▼)
        lines = [ln for ln in md.split("\n") if ln.startswith("| Q008 |")]
        assert lines, "Q008 row missing from per-question table"
        assert "▼" in lines[0]

    def test_compare_reports_mismatched_ids(self):
        md = compare_reports(_sample_report(), _report_b_with_changes(),
                             name_a="A", name_b="B")
        assert "Mismatched IDs" in md
        assert "Q010" in md  # Only in A after the swap
        assert "Q011" in md  # Only in B

    def test_compare_no_mismatch_section_when_aligned(self):
        md = compare_reports(_sample_report(), _sample_report())
        assert "Mismatched IDs" not in md

    def test_compare_handles_error_status_with_x_mark(self):
        a = _sample_report()
        b = _sample_report()
        b["results"][0]["status"] = "error"
        b["results"][0]["error"] = "syntax"
        md = compare_reports(a, b, name_a="A", name_b="B")
        # Look for ✗ in the per-question row for Q001
        lines = [ln for ln in md.split("\n") if ln.startswith("| Q001 |")]
        assert lines and "✗" in lines[0]


class TestEvalCompareCLI:
    def test_compare_end_to_end(self, tmp_path):
        a_path = tmp_path / "a.json"
        b_path = tmp_path / "b.json"
        out_path = tmp_path / "compare.md"
        a_path.write_text(json.dumps(_sample_report()), encoding="utf-8")
        b_path.write_text(json.dumps(_report_b_with_changes()), encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "eval",
                "compare",
                str(a_path),
                str(b_path),
                "--name-a",
                "ontorag",
                "--name-b",
                "langchain",
                "--output",
                str(out_path),
            ],
        )
        assert result.exit_code == 0
        assert out_path.exists()
        text = out_path.read_text(encoding="utf-8")
        assert "ontorag" in text
        assert "langchain" in text
        assert "Mismatched IDs" in text  # b_with_changes has Q011 not in A

    def test_compare_to_stdout(self, tmp_path):
        a_path = tmp_path / "a.json"
        b_path = tmp_path / "b.json"
        a_path.write_text(json.dumps(_sample_report()), encoding="utf-8")
        b_path.write_text(json.dumps(_sample_report()), encoding="utf-8")
        result = runner.invoke(
            app, ["eval", "compare", str(a_path), str(b_path)]
        )
        assert result.exit_code == 0
        assert "# Comparison" in result.stdout

    def test_compare_missing_file_fails(self, tmp_path):
        a = tmp_path / "a.json"
        a.write_text(json.dumps(_sample_report()), encoding="utf-8")
        result = runner.invoke(
            app, ["eval", "compare", str(a), "/no/such/path.json"]
        )
        assert result.exit_code != 0
