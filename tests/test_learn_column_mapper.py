"""Tests for learn/column_mapper.py — LLM-based column → TBox property mapping."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from ontorag.learn.column_mapper import (
    ColumnMapping,
    MappingFile,
    compute_schema_hash,
    load_mapping,
    propose_mapping,
    save_mapping,
    validate_mapping_hash,
    mint_subject_uri,
)
from ontorag.stores.base import ClassSummary, PropertySummary, SchemaResult


# ── fixtures ─────────────────────────────────────────────────────────────────


def make_schema(properties: list[str] | None = None) -> SchemaResult:
    props = [
        PropertySummary(uri=p, label=p.split(":")[-1], domain=None, range=None)
        for p in (properties or ["pk:hasType", "pk:hasHP", "pk:name"])
    ]
    classes = [ClassSummary(uri="pk:Pokemon", label="Pokemon", instance_count=0)]
    return SchemaResult(
        total_classes=len(classes),
        total_properties=len(props),
        classes=classes,
        properties=props,
        namespaces={"pk": "http://example.org/pokemon/"},
    )


def make_llm(mapping_response: list[dict]) -> AsyncMock:
    llm = AsyncMock()
    llm.complete = AsyncMock(
        return_value=type(
            "_R",
            (),
            {
                "content": [
                    type(
                        "_T",
                        (),
                        {"text": json.dumps({"mappings": mapping_response})},
                    )()
                ]
            },
        )()
    )
    return llm


# ── compute_schema_hash ───────────────────────────────────────────────────────


class TestComputeSchemaHash:
    def test_deterministic(self):
        schema = make_schema()
        assert compute_schema_hash(schema) == compute_schema_hash(schema)

    def test_different_properties_different_hash(self):
        h1 = compute_schema_hash(make_schema(["pk:a"]))
        h2 = compute_schema_hash(make_schema(["pk:b"]))
        assert h1 != h2

    def test_same_properties_same_hash(self):
        h1 = compute_schema_hash(make_schema(["pk:a", "pk:b"]))
        h2 = compute_schema_hash(make_schema(["pk:b", "pk:a"]))
        assert h1 == h2  # order-independent


# ── propose_mapping ───────────────────────────────────────────────────────────


class TestProposeMapping:
    @pytest.mark.asyncio
    async def test_returns_column_mappings(self):
        schema = make_schema()
        llm = make_llm(
            [
                {"column": "type", "predicate_uri": "pk:hasType", "confidence": 0.95},
                {"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.88},
            ]
        )
        result = await propose_mapping(
            llm, schema, ["type", "hp"], class_uri="pk:Pokemon"
        )
        assert len(result) == 2
        assert result[0].column_name == "type"
        assert result[0].predicate_uri == "pk:hasType"

    @pytest.mark.asyncio
    async def test_filters_unknown_predicate(self):
        schema = make_schema(["pk:hasType"])
        llm = make_llm(
            [
                {"column": "type", "predicate_uri": "pk:hasType", "confidence": 0.9},
                {"column": "secret", "predicate_uri": "pk:notExist", "confidence": 0.8},
            ]
        )
        result = await propose_mapping(llm, schema, ["type", "secret"])
        uris = [m.predicate_uri for m in result if m.predicate_uri]
        assert "pk:notExist" not in uris

    @pytest.mark.asyncio
    async def test_none_predicate_allowed(self):
        """Column mapped to None means 'skip this column'."""
        schema = make_schema()
        llm = make_llm(
            [{"column": "internal_id", "predicate_uri": None, "confidence": 1.0}]
        )
        result = await propose_mapping(llm, schema, ["internal_id"])
        assert result[0].predicate_uri is None

    @pytest.mark.asyncio
    async def test_llm_failure_raises(self):
        """Network/auth errors propagate so the caller can surface them to the user."""
        schema = make_schema()
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        with pytest.raises(RuntimeError, match="LLM down"):
            await propose_mapping(llm, schema, ["type"])

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        """JSON parse errors are recoverable — return [] without raising."""
        schema = make_schema()
        llm = AsyncMock()
        llm.complete = AsyncMock(
            return_value=type(
                "_R", (), {"content": [type("_T", (), {"text": "not-json"})()]}
            )()
        )
        result = await propose_mapping(llm, schema, ["type"])
        assert result == []


# ── save / load mapping ───────────────────────────────────────────────────────


class TestSaveLoadMapping:
    def test_roundtrip(self, tmp_path):
        schema = make_schema()
        mf = MappingFile(
            schema_hash=compute_schema_hash(schema),
            class_uri="pk:Pokemon",
            id_column="name",
            columns=[
                ColumnMapping(
                    column_name="type", predicate_uri="pk:hasType", confidence=0.9
                )
            ],
            last_row=0,
        )
        path = tmp_path / "data.csv.mapping.json"
        save_mapping(mf, path)
        loaded = load_mapping(path)

        assert loaded.schema_hash == mf.schema_hash
        assert loaded.class_uri == mf.class_uri
        assert loaded.id_column == mf.id_column
        assert loaded.columns[0].predicate_uri == "pk:hasType"
        assert loaded.last_row == 0

    def test_last_row_persisted(self, tmp_path):
        schema = make_schema()
        mf = MappingFile(
            schema_hash=compute_schema_hash(schema),
            class_uri=None,
            id_column=None,
            columns=[],
            last_row=150,
        )
        path = tmp_path / "data.csv.mapping.json"
        save_mapping(mf, path)
        assert load_mapping(path).last_row == 150


# ── validate_mapping_hash ─────────────────────────────────────────────────────


class TestValidateMappingHash:
    def test_valid_hash_returns_true(self):
        schema = make_schema()
        mf = MappingFile(
            schema_hash=compute_schema_hash(schema),
            class_uri=None,
            id_column=None,
            columns=[],
        )
        assert validate_mapping_hash(mf, schema) is True

    def test_stale_hash_returns_false(self):
        schema = make_schema()
        mf = MappingFile(
            schema_hash="stale_hash_xyz",
            class_uri=None,
            id_column=None,
            columns=[],
        )
        assert validate_mapping_hash(mf, schema) is False


# ── mint_subject_uri ──────────────────────────────────────────────────────────


class TestMintSubjectUri:
    def test_id_column_used_when_specified(self):
        row = {"name": "Pikachu", "hp": "35"}
        ns = "http://example.org/pokemon/"
        uri = mint_subject_uri(
            row, id_column="name", namespace=ns, filepath="/f.csv", row_index=0
        )
        assert "Pikachu" in uri

    def test_uuid5_deterministic_without_id_column(self):
        row = {"hp": "35"}
        ns = "http://example.org/pokemon/"
        uri1 = mint_subject_uri(
            row, id_column=None, namespace=ns, filepath="/f.csv", row_index=5
        )
        uri2 = mint_subject_uri(
            row, id_column=None, namespace=ns, filepath="/f.csv", row_index=5
        )
        assert uri1 == uri2

    def test_different_rows_different_uris(self):
        ns = "http://example.org/pokemon/"
        uri0 = mint_subject_uri(
            {}, id_column=None, namespace=ns, filepath="/f.csv", row_index=0
        )
        uri1 = mint_subject_uri(
            {}, id_column=None, namespace=ns, filepath="/f.csv", row_index=1
        )
        assert uri0 != uri1

    def test_same_file_different_run_same_uri(self):
        """Reloading the same file produces the same URI — idempotent."""
        ns = "http://example.org/"
        uri_run1 = mint_subject_uri(
            {}, id_column=None, namespace=ns, filepath="/data.csv", row_index=3
        )
        uri_run2 = mint_subject_uri(
            {}, id_column=None, namespace=ns, filepath="/data.csv", row_index=3
        )
        assert uri_run1 == uri_run2

    def test_special_chars_percent_encoded(self):
        """Apostrophes, colons, hashes, spaces are percent-encoded in subject URI."""
        row = {"name": "Mr. Mime"}
        uri = mint_subject_uri(
            row, id_column="name", namespace="http://ex.org/", filepath="f", row_index=0
        )
        assert " " not in uri
        assert "%20" in uri or "Mr." in uri  # space encoded

        row2 = {"name": "Pikachu's"}
        uri2 = mint_subject_uri(
            row2,
            id_column="name",
            namespace="http://ex.org/",
            filepath="f",
            row_index=0,
        )
        assert "'" not in uri2

    def test_empty_id_value_falls_back_to_uuid(self):
        """Blank id-column value uses uuid5 fallback instead of bare namespace root."""
        row = {"name": "   "}
        uri = mint_subject_uri(
            row, id_column="name", namespace="http://ex.org/", filepath="f", row_index=0
        )
        assert "entity-" in uri  # uuid5 fallback used


# ── propose_mapping — new behaviour tests ─────────────────────────────────────


class TestProposeMapping_NewBehaviours:
    @pytest.mark.asyncio
    async def test_llm_returns_array_directly(self):
        """LLM returns the mappings array directly (without wrapper object)."""
        schema = make_schema()
        llm = AsyncMock()
        llm.complete = AsyncMock(
            return_value=type(
                "_R",
                (),
                {
                    "content": [
                        type(
                            "_T",
                            (),
                            {
                                "text": json.dumps(
                                    [
                                        {
                                            "column": "hp",
                                            "predicate_uri": "pk:hasHP",
                                            "confidence": 0.9,
                                        }
                                    ]
                                )
                            },
                        )()
                    ]
                },
            )()
        )
        result = await propose_mapping(llm, schema, ["hp"])
        assert len(result) == 1
        assert result[0].predicate_uri == "pk:hasHP"

    @pytest.mark.asyncio
    async def test_llm_returns_unexpected_type_returns_empty(self):
        """LLM returns a scalar JSON value — treated as empty, not an error."""
        schema = make_schema()
        llm = AsyncMock()
        llm.complete = AsyncMock(
            return_value=type(
                "_R", (), {"content": [type("_T", (), {"text": "42"})()]}
            )()
        )
        result = await propose_mapping(llm, schema, ["hp"])
        assert result == []


# ── load_mapping — forward compatibility ──────────────────────────────────────


class TestLoadMappingForwardCompat:
    def test_unknown_keys_ignored(self, tmp_path):
        """Mapping file with a future 'version' field loads without TypeError."""
        path = tmp_path / "data.csv.mapping.json"
        data = {
            "schema_hash": "abc",
            "class_uri": None,
            "id_column": None,
            "columns": [],
            "last_row": 0,
            "version": 2,  # future field — must not raise
            "extra_flag": True,  # another future field
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        mf = load_mapping(path)
        assert mf.schema_hash == "abc"
        assert mf.last_row == 0
