"""Tests for the ontorag.yaml manifest parser (core.manifest) and its
integration with load_directory (core.batch_loader).

All tests use tmp_path with real mini-TTL files so parse_rdf / detect_mode
run for real when called from load_directory.  No live backend needed —
_SpyStore from test_batch_loader is re-used via a local copy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ontorag.core.manifest import (
    MANIFEST_FILENAME,
    ManifestLoadPlan,
    load_manifest,
)
from ontorag.core.batch_loader import load_directory
from ontorag.stores.base import LoadResult

# ── shared TTL snippets ────────────────────────────────────────────────────────

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


def _write_manifest(root: Path, data: dict) -> None:
    (root / MANIFEST_FILENAME).write_text(
        yaml.dump(data, allow_unicode=True), encoding="utf-8"
    )


class _SpyStore:
    """Fake GraphStore recording load_rdf calls (identical to test_batch_loader)."""

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


# ── manifest parser unit tests ─────────────────────────────────────────────────


def test_load_manifest_returns_none_when_absent(tmp_path: Path) -> None:
    assert load_manifest(tmp_path) is None


def test_load_manifest_parses_basic_structure(tmp_path: Path) -> None:
    _write(tmp_path / "foaf" / "foaf.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "people.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "foaf",
                "schema": ["foaf/foaf.ttl"],
                "data": ["foaf/people.ttl"],
            }
        ]
    })
    plan = load_manifest(tmp_path)
    assert isinstance(plan, ManifestLoadPlan)
    assert len(plan.entries) == 2
    schema_entry = plan.entries[0]
    data_entry = plan.entries[1]
    assert schema_entry.mode == "schema"
    assert schema_entry.ontology_id == "foaf"
    assert data_entry.mode == "data"
    assert data_entry.ontology_id == "foaf"


def test_load_manifest_schema_before_data_within_ontology(tmp_path: Path) -> None:
    """Manifest entries: schema list is always emitted before data list."""
    _write(tmp_path / "foaf" / "foaf.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "people.ttl", _DATA_TTL)
    _write(tmp_path / "foaf" / "orgs.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "foaf",
                "schema": ["foaf/foaf.ttl"],
                "data": ["foaf/people.ttl", "foaf/orgs.ttl"],
            }
        ]
    })
    plan = load_manifest(tmp_path)
    assert plan is not None
    modes = [e.mode for e in plan.entries]
    assert modes == ["schema", "data", "data"]


def test_load_manifest_data_order_preserved(tmp_path: Path) -> None:
    """Listed order of data files is preserved exactly."""
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "people.ttl", _DATA_TTL)
    _write(tmp_path / "foaf" / "orgs.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "foaf",
                "schema": ["foaf/schema.ttl"],
                "data": ["foaf/orgs.ttl", "foaf/people.ttl"],  # reversed order
            }
        ]
    })
    plan = load_manifest(tmp_path)
    assert plan is not None
    data_entries = [e for e in plan.entries if e.mode == "data"]
    names = [e.path.name for e in data_entries]
    assert names == ["orgs.ttl", "people.ttl"]  # exact listed order preserved


def test_load_manifest_multiple_ontologies(tmp_path: Path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "pokemon" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "pokemon" / "data.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {"id": "foaf", "schema": ["foaf/schema.ttl"], "data": []},
            {"id": "pokemon", "schema": ["pokemon/schema.ttl"], "data": ["pokemon/data.ttl"]},
        ]
    })
    plan = load_manifest(tmp_path)
    assert plan is not None
    ids = [e.ontology_id for e in plan.entries]
    # foaf entries come before pokemon entries (manifest list order)
    assert ids.index("foaf") < ids.index("pokemon")


def test_load_manifest_glob_expansion(tmp_path: Path) -> None:
    """Glob patterns in data: expand to all matching files."""
    _write(tmp_path / "pokemon" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "pokemon" / "bulbasaur.ttl", _DATA_TTL)
    _write(tmp_path / "pokemon" / "charmander.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "pokemon",
                "schema": ["pokemon/schema.ttl"],
                "data": ["pokemon/*.ttl"],
            }
        ]
    })
    plan = load_manifest(tmp_path)
    assert plan is not None
    data_entries = [e for e in plan.entries if e.mode == "data"]
    # schema.ttl is already in schema; glob matches bulbasaur + charmander
    # (schema.ttl is also matched by *.ttl — glob expands greedily)
    data_names = {e.path.name for e in data_entries}
    assert "bulbasaur.ttl" in data_names
    assert "charmander.ttl" in data_names


def test_load_manifest_extra_ignore_parsed(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {
        "ontologies": [],
        "ignore": ["drafts/**", "tmp/"],
    })
    plan = load_manifest(tmp_path)
    assert plan is not None
    assert plan.extra_ignore == ["drafts/**", "tmp/"]


def test_load_manifest_no_ignore_key(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {"ontologies": []})
    plan = load_manifest(tmp_path)
    assert plan is not None
    assert plan.extra_ignore == []


# ── manifest validation: fail-fast on bad id ──────────────────────────────────


def test_load_manifest_invalid_id_raises(tmp_path: Path) -> None:
    _write(tmp_path / "bad id" / "schema.ttl", _SCHEMA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {"id": "bad id", "schema": ["bad id/schema.ttl"], "data": []},
        ]
    })
    with pytest.raises(ValueError, match="Invalid ontology id"):
        load_manifest(tmp_path)


def test_load_manifest_missing_referenced_file_raises(tmp_path: Path) -> None:
    # No actual file written — reference is dangling.
    _write_manifest(tmp_path, {
        "ontologies": [
            {"id": "foaf", "schema": ["foaf/nonexistent.ttl"], "data": []},
        ]
    })
    with pytest.raises(ValueError, match="does not exist"):
        load_manifest(tmp_path)


def test_load_manifest_missing_glob_match_raises(tmp_path: Path) -> None:
    # Directory exists but nothing matches the glob.
    _write_manifest(tmp_path, {
        "ontologies": [
            {"id": "pokemon", "schema": [], "data": ["pokemon/*.ttl"]},
        ]
    })
    with pytest.raises(ValueError, match="matched no files"):
        load_manifest(tmp_path)


def test_load_manifest_missing_id_key_raises(tmp_path: Path) -> None:
    _write_manifest(tmp_path, {
        "ontologies": [
            {"schema": [], "data": []},  # no 'id' key
        ]
    })
    with pytest.raises(ValueError, match="'id' is required"):
        load_manifest(tmp_path)


def test_load_manifest_top_level_not_mapping_raises(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_FILENAME).write_text("- just a list item\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_manifest(tmp_path)


# ── integration: manifest overrides default sub-dir mapping ───────────────────


async def test_manifest_overrides_subdir_mapping(tmp_path: Path) -> None:
    """When ontorag.yaml exists, sub-directory names are NOT used as ids."""
    # Files live under 'data/' but the manifest assigns them to 'myonto'.
    _write(tmp_path / "data" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "data" / "instances.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "myonto",
                "schema": ["data/schema.ttl"],
                "data": ["data/instances.ttl"],
            }
        ]
    })
    store = _SpyStore()
    result = await load_directory(store, tmp_path)
    # All files attributed to 'myonto', not 'data'
    assert {c["ontology"] for c in store.calls} == {"myonto"}
    assert result.loaded == 2


async def test_manifest_load_order_schema_before_data(tmp_path: Path) -> None:
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "people.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "foaf",
                "schema": ["foaf/schema.ttl"],
                "data": ["foaf/people.ttl"],
            }
        ]
    })
    store = _SpyStore()
    await load_directory(store, tmp_path)
    modes = [c["mode"] for c in store.calls]
    assert modes == ["schema", "data"]


async def test_manifest_load_order_data_files_listed_order(tmp_path: Path) -> None:
    """The manifest data list order is respected, not alphabetical."""
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "z_last.ttl", _DATA_TTL)
    _write(tmp_path / "foaf" / "a_first.ttl", _DATA_TTL)
    # Reverse-alpha order in manifest
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "foaf",
                "schema": ["foaf/schema.ttl"],
                "data": ["foaf/z_last.ttl", "foaf/a_first.ttl"],
            }
        ]
    })
    store = _SpyStore()
    await load_directory(store, tmp_path)
    data_calls = [c for c in store.calls if c["mode"] == "data"]
    names = [Path(c["path"]).name for c in data_calls]
    assert names == ["z_last.ttl", "a_first.ttl"]


async def test_manifest_and_ontology_together_raise_before_load(tmp_path: Path) -> None:
    """manifest + --ontology is a conflict → ValueError before any load."""
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {"id": "foaf", "schema": ["foaf/schema.ttl"], "data": []},
        ]
    })
    store = _SpyStore()
    with pytest.raises(ValueError, match="manifest"):
        await load_directory(store, tmp_path, ontology="flatmerge")
    assert store.calls == []  # no load must have been attempted


async def test_manifest_glob_in_load_directory(tmp_path: Path) -> None:
    """Glob expansion in the manifest works through load_directory."""
    _write(tmp_path / "pokemon" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "pokemon" / "pikachu.ttl", _DATA_TTL)
    _write(tmp_path / "pokemon" / "eevee.ttl", _DATA_TTL)
    # Glob expands all .ttl in data section (includes schema.ttl too)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "pokemon",
                "schema": ["pokemon/schema.ttl"],
                "data": ["pokemon/pikachu.ttl", "pokemon/eevee.ttl"],
            }
        ]
    })
    store = _SpyStore()
    result = await load_directory(store, tmp_path)
    assert result.loaded == 3
    assert {c["ontology"] for c in store.calls} == {"pokemon"}


async def test_manifest_missing_file_raises_before_load(tmp_path: Path) -> None:
    """Missing referenced file in manifest raises ValueError before any load."""
    _write_manifest(tmp_path, {
        "ontologies": [
            {"id": "foaf", "schema": ["foaf/ghost.ttl"], "data": []},
        ]
    })
    store = _SpyStore()
    with pytest.raises(ValueError, match="does not exist"):
        await load_directory(store, tmp_path)
    assert store.calls == []


async def test_manifest_invalid_id_raises_before_load(tmp_path: Path) -> None:
    """Invalid ontology id in manifest raises ValueError before any load."""
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {"id": "bad/id!", "schema": ["foaf/schema.ttl"], "data": []},
        ]
    })
    store = _SpyStore()
    with pytest.raises(ValueError, match="Invalid ontology id"):
        await load_directory(store, tmp_path)
    assert store.calls == []


async def test_manifest_schema_failure_skips_scope_data(tmp_path: Path) -> None:
    """Schema load failure in manifest path also skips that scope's data files."""
    _write(tmp_path / "foaf" / "schema.ttl", _SCHEMA_TTL)
    _write(tmp_path / "foaf" / "people.ttl", _DATA_TTL)
    _write_manifest(tmp_path, {
        "ontologies": [
            {
                "id": "foaf",
                "schema": ["foaf/schema.ttl"],
                "data": ["foaf/people.ttl"],
            }
        ]
    })
    store = _SpyStore(fail_substrings={"foaf/schema"})
    result = await load_directory(store, tmp_path)
    data_outcome = next(o for o in result.outcomes if "people" in o.source)
    assert data_outcome.status == "skipped"
    assert "schema" in (data_outcome.reason or "")
