"""Tests for `ontorag eval` CLI — validate + run subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ontorag.cli import app

REPO_ROOT = Path(__file__).resolve().parent.parent
PURE_LAND = REPO_ROOT / "examples" / "pure_land"

runner = CliRunner()


@pytest.fixture()
def valid_goldset() -> Path:
    return PURE_LAND / "goldset.jsonl"


@pytest.fixture()
def schema() -> Path:
    return PURE_LAND / "schema.ttl"


@pytest.fixture()
def data() -> Path:
    return PURE_LAND / "data.ttl"


# ── eval validate ─────────────────────────────────────────────────────────────


class TestEvalValidate:
    def test_validate_pure_land_goldset_succeeds(self, valid_goldset):
        result = runner.invoke(app, ["eval", "validate", str(valid_goldset)])
        assert result.exit_code == 0
        assert "50 questions" in result.stdout

    def test_validate_missing_file_exits_nonzero(self):
        result = runner.invoke(
            app, ["eval", "validate", "/no/such/path.jsonl"]
        )
        assert result.exit_code != 0

    def test_validate_invalid_goldset_exits_nonzero(self, tmp_path):
        bad = tmp_path / "bad.jsonl"
        bad.write_text("{not json}\n", encoding="utf-8")
        result = runner.invoke(app, ["eval", "validate", str(bad)])
        assert result.exit_code == 1
        # Error printed to stderr; combined output via mix_stderr default
        # may capture it differently — check exit code at minimum.


# ── eval run ──────────────────────────────────────────────────────────────────


class TestEvalRun:
    def test_run_pure_land_succeeds(self, valid_goldset, schema, data):
        result = runner.invoke(
            app,
            [
                "eval",
                "run",
                str(valid_goldset),
                "--schema",
                str(schema),
                "--data",
                str(data),
            ],
        )
        assert result.exit_code == 0
        # Summary should mention difficulty tiers
        for tier in ("easy", "medium", "hard", "trap"):
            assert tier in result.stdout

    def test_run_writes_json_report(self, valid_goldset, schema, data, tmp_path):
        output = tmp_path / "report.json"
        result = runner.invoke(
            app,
            [
                "eval",
                "run",
                str(valid_goldset),
                "--schema",
                str(schema),
                "--data",
                str(data),
                "--output",
                str(output),
            ],
        )
        assert result.exit_code == 0
        assert output.exists()
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["total_questions"] == 50
        assert report["failures"] == 0
        assert len(report["results"]) == 50
        assert all("status" in r for r in report["results"])

    def test_run_report_includes_difficulty_for_each_result(
        self, valid_goldset, schema, data, tmp_path
    ):
        output = tmp_path / "report.json"
        runner.invoke(
            app,
            [
                "eval",
                "run",
                str(valid_goldset),
                "--schema",
                str(schema),
                "--data",
                str(data),
                "--output",
                str(output),
            ],
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        difficulties = {r["difficulty"] for r in report["results"]}
        assert difficulties == {"easy", "medium", "hard", "trap"}

    def test_run_missing_schema_file_fails(self, valid_goldset, data, tmp_path):
        result = runner.invoke(
            app,
            [
                "eval",
                "run",
                str(valid_goldset),
                "--schema",
                "/no/such/schema.ttl",
                "--data",
                str(data),
            ],
        )
        assert result.exit_code != 0

    def test_run_verbose_flag_accepted(self, valid_goldset, schema, data):
        result = runner.invoke(
            app,
            [
                "eval",
                "run",
                str(valid_goldset),
                "--schema",
                str(schema),
                "--data",
                str(data),
                "--verbose",
            ],
        )
        assert result.exit_code == 0
        # Verbose output includes per-question lines (e.g. Q001)
        assert "Q001" in result.stdout
