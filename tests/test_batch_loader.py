"""Tests for the directory / multi-file loader (core.batch_loader).

The GraphStore is a fake spy recording load_rdf calls — no live Fuseki/Neo4j.
RDF files are real mini-TTL trees under tmp_path so parse_rdf/detect_mode run
for real (mode detection is part of what we test).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ontorag.core.batch_loader import (
    DEFAULT_IGNORE,
    _collect_files,
    load_directory,
)
from ontorag.stores.base import BatchLoadResult, FileLoadOutcome, LoadResult

# ── fixtures: real mini-TTL content ───────────────────────────────────────────

_SCHEMA_TTL = (
    "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
    "@prefix ex: <http://ex.org/> .\n"
    "ex:Foo a owl:Class .\n"
)
_DATA_TTL = (
    "@prefix ex: <http://ex.org/> .\n"
    "ex:bar a ex:Foo .\n"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _SpyStore:
    """Fake GraphStore: records load_rdf calls, optionally fails on substrings."""

    def __init__(self, fail_substrings: set[str] | None = None, triples: int = 10):
        self.calls: list[dict] = []
        self._fail = fail_substrings or set()
        self._triples = triples

    async def load_rdf(self, path, mode="auto", replace=False, ontology=None):
        self.calls.append(
            {"path": path, "mode": mode, "replace": replace, "ontology": ontology}
        )
        if any(s in path for s in self._fail):
            raise RuntimeError("simulated load failure")
        return LoadResult(
            triples_loaded=self._triples, source=path, mode=mode, ontology=ontology
        )


# ── result types (design §4) ───────────────────────────────────────────────────


def test_result_types_construct() -> None:
    o = FileLoadOutcome(
        source="foaf/schema.ttl", ontology="foaf", status="loaded",
        mode="schema", triples_loaded=3,
    )
    b = BatchLoadResult(
        root=".", total_files=1, loaded=1, skipped=0, failed=0,
        total_triples=3, outcomes=[o],
    )
    assert b.outcomes[0].source == "foaf/schema.ttl"
    assert o.reason is None  # default
    # skipped/failed outcomes carry no mode/triples by default
    s = FileLoadOutcome(source="x.ttl", status="skipped", reason="why")
    assert s.mode is None and s.triples_loaded == 0


# ── scope mapping (§3) ──────────────────────────────────────────────────────────


async def test_subdir_becomes_ontology_id(tmp_path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "pokemon" / "schema.ttl", _SCHEMA_TTL)
    store = _SpyStore()
    result = await load_directory(store, tmp_path)
    scopes = {c["ontology"] for c in store.calls}
    assert scopes == {"foaf", "pokemon"}
    assert result.loaded == 2


async def test_root_level_file_is_none_scope(tmp_path) -> None:
    _write(tmp_path / "root.ttl", _DATA_TTL)
    _write(tmp_path / "foaf" / "data.ttl", _DATA_TTL)
    store = _SpyStore()
    await load_directory(store, tmp_path)
    by_path = {c["path"].split("/")[-1]: c["ontology"] for c in store.calls}
    assert by_path["root.ttl"] is None
    assert by_path["data.ttl"] == "foaf"


async def test_deep_nesting_collapses_to_first_dir(tmp_path) -> None:
    _write(tmp_path / "foaf" / "sub" / "deep.ttl", _DATA_TTL)
    store = _SpyStore()
    await load_directory(store, tmp_path)
    assert store.calls[0]["ontology"] == "foaf"


async def test_ontology_override_flat_merges(tmp_path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "pokemon" / "data.ttl", _DATA_TTL)
    _write(tmp_path / "root.ttl", _DATA_TTL)
    store = _SpyStore()
    await load_directory(store, tmp_path, ontology="merged")
    assert {c["ontology"] for c in store.calls} == {"merged"}


# ── schema-before-data ordering (§6) ────────────────────────────────────────────


async def test_schema_loaded_before_data_per_scope(tmp_path) -> None:
    _write(tmp_path / "foaf" / "data.ttl", _DATA_TTL)
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    store = _SpyStore()
    await load_directory(store, tmp_path)
    modes = [c["mode"] for c in store.calls]
    assert modes == ["schema", "data"]  # schema first despite alpha/file order


# ── ignore patterns (§5) ─────────────────────────────────────────────────────────


async def test_ignored_and_hidden_dirs_excluded(tmp_path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "__pycache__" / "junk.ttl", _SCHEMA_TTL)  # in DEFAULT_IGNORE
    _write(tmp_path / ".git" / "hidden.ttl", _SCHEMA_TTL)  # hidden
    store = _SpyStore()
    result = await load_directory(store, tmp_path)
    assert result.total_files == 1
    assert all("__pycache__" not in c["path"] and ".git" not in c["path"]
               for c in store.calls)


def test_collect_files_respects_recursive(tmp_path) -> None:
    _write(tmp_path / "top.ttl", _DATA_TTL)
    _write(tmp_path / "sub" / "nested.ttl", _DATA_TTL)
    flat = _collect_files(tmp_path, recursive=False, ignore=DEFAULT_IGNORE)
    deep = _collect_files(tmp_path, recursive=True, ignore=DEFAULT_IGNORE)
    assert [p.name for p in flat] == ["top.ttl"]
    assert {p.name for p in deep} == {"top.ttl", "nested.ttl"}


# ── fail-fast on bad slug (§3, §13) ──────────────────────────────────────────────


async def test_invalid_subdir_slug_raises_before_load(tmp_path) -> None:
    _write(tmp_path / "good" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "bad name" / "data.ttl", _DATA_TTL)  # space → invalid slug
    store = _SpyStore()
    with pytest.raises(ValueError):
        await load_directory(store, tmp_path)
    assert store.calls == []  # nothing loaded before the config error


async def test_not_a_directory_raises(tmp_path) -> None:
    f = tmp_path / "file.ttl"
    _write(f, _DATA_TTL)
    store = _SpyStore()
    with pytest.raises(NotADirectoryError):
        await load_directory(store, f)


# ── continue-and-report (§5, §13) ────────────────────────────────────────────────


async def test_parse_failure_recorded_and_others_continue(tmp_path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "broken" / "x.ttl", "this is not valid turtle {{{")
    store = _SpyStore()
    result = await load_directory(store, tmp_path)
    assert result.loaded == 1
    assert result.failed == 1
    broken = next(o for o in result.outcomes if o.status == "failed")
    assert "parse error" in (broken.reason or "")
    # the broken file never reached the store
    assert all("broken" not in c["path"] for c in store.calls)


async def test_schema_load_failure_skips_scope_data(tmp_path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "data.ttl", _DATA_TTL)
    _write(tmp_path / "pokemon" / "data.ttl", _DATA_TTL)  # unaffected scope
    store = _SpyStore(fail_substrings={"foaf/schema"})
    result = await load_directory(store, tmp_path)
    foaf_data = next(
        o for o in result.outcomes if o.source == "foaf/data.ttl"
    )
    assert foaf_data.status == "skipped"
    assert "schema" in (foaf_data.reason or "")
    # foaf/data must NOT have been sent to the store
    assert all("foaf/data" not in c["path"] for c in store.calls)
    # other scope still loads
    assert any(c["path"].endswith("pokemon/data.ttl") for c in store.calls)


# ── replace policy (§7) ──────────────────────────────────────────────────────────


async def test_replace_only_first_data_per_scope(tmp_path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "data1.ttl", _DATA_TTL)
    _write(tmp_path / "foaf" / "data2.ttl", _DATA_TTL)
    store = _SpyStore()
    await load_directory(store, tmp_path, replace=True)
    data_calls = [c for c in store.calls if c["mode"] == "data"]
    assert [c["replace"] for c in data_calls] == [True, False]  # first only
    schema_call = next(c for c in store.calls if c["mode"] == "schema")
    assert schema_call["replace"] is False  # schema never gets replace


async def test_replace_false_is_pure_append(tmp_path) -> None:
    _write(tmp_path / "foaf" / "data1.ttl", _DATA_TTL)
    _write(tmp_path / "foaf" / "data2.ttl", _DATA_TTL)
    store = _SpyStore()
    await load_directory(store, tmp_path, replace=False)
    assert all(c["replace"] is False for c in store.calls)


# ── empty / no-RDF directory (§11) ──────────────────────────────────────────────


async def test_empty_directory_returns_zero(tmp_path) -> None:
    store = _SpyStore()
    result = await load_directory(store, tmp_path)
    assert result.total_files == 0
    assert (result.loaded, result.skipped, result.failed) == (0, 0, 0)
    assert store.calls == []


async def test_directory_without_rdf_returns_zero(tmp_path) -> None:
    _write(tmp_path / "readme.md", "# not rdf")
    _write(tmp_path / "notes.txt", "hello")
    store = _SpyStore()
    result = await load_directory(store, tmp_path)
    assert result.total_files == 0
