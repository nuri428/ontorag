"""Tests for learn/structured_reader.py — CSV/JSON/JSONL → flat row dicts."""

from __future__ import annotations

import json

import pytest

from ontorag.learn.structured_reader import (
    UnsupportedFormatError,
    read_structured,
    flatten_dict,
)


# ── flatten_dict ─────────────────────────────────────────────────────────────


class TestFlattenDict:
    def test_flat_dict_unchanged(self):
        assert flatten_dict({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}

    def test_nested_one_level(self):
        result = flatten_dict({"stats": {"hp": 45, "atk": 49}})
        assert result == {"stats.hp": 45, "stats.atk": 49}

    def test_nested_two_levels(self):
        result = flatten_dict({"a": {"b": {"c": 3}}})
        assert result == {"a.b.c": 3}

    def test_mixed_flat_and_nested(self):
        result = flatten_dict({"name": "Pikachu", "stats": {"hp": 35}})
        assert result == {"name": "Pikachu", "stats.hp": 35}

    def test_list_values_kept_as_is(self):
        result = flatten_dict({"types": ["Electric", "Fire"]})
        assert result == {"types": ["Electric", "Fire"]}

    def test_empty_dict(self):
        assert flatten_dict({}) == {}

    def test_custom_separator(self):
        result = flatten_dict({"a": {"b": 1}}, sep="/")
        assert result == {"a/b": 1}


# ── read_structured (CSV) ────────────────────────────────────────────────────


class TestReadCSV:
    def test_basic_csv(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,type,hp\nPikachu,Electric,35\nCharmander,Fire,39\n")
        rows = read_structured(f)
        assert len(rows) == 2
        assert rows[0] == {"name": "Pikachu", "type": "Electric", "hp": "35"}
        assert rows[1] == {"name": "Charmander", "type": "Fire", "hp": "39"}

    def test_csv_with_empty_rows_skipped(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,type\nPikachu,Electric\n\nCharmander,Fire\n")
        rows = read_structured(f)
        assert len(rows) == 2

    def test_empty_csv_returns_empty_list(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,type\n")
        rows = read_structured(f)
        assert rows == []

    def test_csv_with_quoted_fields(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text('name,desc\nPikachu,"cute, yellow"\n')
        rows = read_structured(f)
        assert rows[0]["desc"] == "cute, yellow"

    def test_csv_string_path_accepted(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a\n1\n")
        rows = read_structured(str(f))
        assert rows == [{"a": "1"}]


# ── read_structured (JSON) ───────────────────────────────────────────────────


class TestReadJSON:
    def test_json_array(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"name": "Pikachu"}, {"name": "Mewtwo"}]))
        rows = read_structured(f)
        assert len(rows) == 2
        assert rows[0]["name"] == "Pikachu"

    def test_nested_json_flattened(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"name": "Pikachu", "stats": {"hp": 35, "atk": 55}}]))
        rows = read_structured(f)
        assert rows[0]["stats.hp"] == 35
        assert rows[0]["stats.atk"] == 55
        assert "stats" not in rows[0]

    def test_json_object_wrapped_in_list(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"name": "Pikachu"}))
        rows = read_structured(f)
        assert rows == [{"name": "Pikachu"}]

    def test_empty_json_array(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text("[]")
        rows = read_structured(f)
        assert rows == []


# ── read_structured (JSONL) ──────────────────────────────────────────────────


class TestReadJSONL:
    def test_basic_jsonl(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text(
            '{"name": "Pikachu"}\n{"name": "Mewtwo"}\n'
        )
        rows = read_structured(f)
        assert len(rows) == 2

    def test_jsonl_skips_blank_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a": 1}\n\n{"a": 2}\n')
        rows = read_structured(f)
        assert len(rows) == 2

    def test_jsonl_nested_flattened(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"x": {"y": 1}}\n')
        rows = read_structured(f)
        assert rows[0] == {"x.y": 1}


# ── unsupported format ───────────────────────────────────────────────────────


class TestUnsupportedFormat:
    def test_txt_raises(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello")
        with pytest.raises(UnsupportedFormatError):
            read_structured(f)

    def test_rdf_raises(self, tmp_path):
        f = tmp_path / "data.ttl"
        f.write_text("")
        with pytest.raises(UnsupportedFormatError):
            read_structured(f)
