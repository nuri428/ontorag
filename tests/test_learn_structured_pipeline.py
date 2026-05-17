"""Tests for structured ABox population pipeline (v0.3.1)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontorag.learn.base import PopulationResult
from ontorag.learn.column_mapper import ColumnMapping, MappingFile, compute_schema_hash
from ontorag.learn.pipeline import LLMOntologyLearner
from ontorag.stores.base import ClassSummary, PropertySummary, SchemaResult
from tests.conftest import MockGraphStore


# ── helpers ───────────────────────────────────────────────────────────────────


def make_tool_response(text: str):
    """Create a mock LLM response with a text block (used by propose_mapping)."""
    return type("_R", (), {"content": [type("_T", (), {"text": text})()]})()


def make_schema() -> SchemaResult:
    classes = [ClassSummary(uri="pk:Pokemon", label="Pokemon", instance_count=2)]
    properties = [
        PropertySummary(uri="pk:hasType", label="hasType", domain=None, range=None),
        PropertySummary(uri="pk:hasHP", label="hasHP", domain=None, range=None),
        PropertySummary(uri="rdfs:label", label="label", domain=None, range=None),
    ]
    return SchemaResult(
        total_classes=len(classes),
        total_properties=len(properties),
        classes=classes,
        properties=properties,
        namespaces={"pk": "http://example.org/pokemon/"},
    )


def make_learner(
    schema: SchemaResult | None = None,
) -> tuple[LLMOntologyLearner, MockGraphStore, AsyncMock]:
    _schema = schema or make_schema()
    store = MockGraphStore(_schema)
    store.get_schema = AsyncMock(return_value=_schema)
    store.load_rdf = AsyncMock(return_value=MagicMock(triples_loaded=5))
    llm = AsyncMock()
    learner = LLMOntologyLearner(store, llm)
    return learner, store, llm


def make_csv(tmp_path: Path, content: str, name: str = "data.csv") -> Path:
    f = tmp_path / name
    f.write_text(content)
    return f


def mapping_llm_response(mappings: list[dict]) -> str:
    return json.dumps({"mappings": mappings})


# ── populate_from_structured — basic CSV ─────────────────────────────────────


class TestPopulateFromStructuredCSV:
    @pytest.mark.asyncio
    async def test_returns_population_result(self, tmp_path):
        csv = make_csv(tmp_path, "name,type,hp\nPikachu,Electric,35\n")
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [
                        {
                            "column": "type",
                            "predicate_uri": "pk:hasType",
                            "confidence": 0.9,
                        },
                        {
                            "column": "hp",
                            "predicate_uri": "pk:hasHP",
                            "confidence": 0.85,
                        },
                        {
                            "column": "name",
                            "predicate_uri": "rdfs:label",
                            "confidence": 0.95,
                        },
                    ]
                )
            )
        )

        result = await learner.populate_from_structured(
            csv, class_uri="pk:Pokemon", id_column="name"
        )
        assert isinstance(result, PopulationResult)
        assert len(result.triples) > 0

    @pytest.mark.asyncio
    async def test_triples_use_id_column_in_subject_uri(self, tmp_path):
        csv = make_csv(tmp_path, "name,hp\nPikachu,35\n")
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )
        )

        result = await learner.populate_from_structured(csv, id_column="name")
        assert any(
            "Pikachu" in t.subject_uri or "Pikachu" in t.subject_label
            for t in result.triples
        )

    @pytest.mark.asyncio
    async def test_uuid5_used_when_no_id_column(self, tmp_path):
        csv = make_csv(tmp_path, "hp\n35\n")
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )
        )

        result = await learner.populate_from_structured(csv, id_column=None)
        assert len(result.triples) == 1
        assert result.triples[0].subject_uri is not None

    @pytest.mark.asyncio
    async def test_idempotent_uuid_same_file(self, tmp_path):
        """Loading same CSV twice produces same subject URIs (idempotent)."""
        csv = make_csv(tmp_path, "hp\n35\n")
        learner, store, llm = make_learner()

        response = make_tool_response(
            mapping_llm_response(
                [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
            )
        )
        llm.complete = AsyncMock(return_value=response)

        result1 = await learner.populate_from_structured(csv, id_column=None)
        llm.complete = AsyncMock(return_value=response)
        result2 = await learner.populate_from_structured(csv, id_column=None)

        assert result1.triples[0].subject_uri == result2.triples[0].subject_uri

    @pytest.mark.asyncio
    async def test_below_min_confidence_filtered(self, tmp_path):
        csv = make_csv(tmp_path, "name,secret\nPikachu,xyz\n")
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [
                        {
                            "column": "name",
                            "predicate_uri": "rdfs:label",
                            "confidence": 0.95,
                        },
                        {
                            "column": "secret",
                            "predicate_uri": "pk:hasType",
                            "confidence": 0.3,
                        },
                    ]
                )
            )
        )

        result = await learner.populate_from_structured(csv, min_confidence=0.7)
        pred_uris = [t.predicate_uri for t in result.triples]
        assert "pk:hasType" not in pred_uris

    @pytest.mark.asyncio
    async def test_auto_load_calls_store(self, tmp_path):
        csv = make_csv(tmp_path, "name,hp\nPikachu,35\n")
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )
        )

        result = await learner.populate_from_structured(csv, auto_load=True)
        store.load_rdf.assert_awaited_once()
        assert result.triples_loaded is not None


# ── mapping file save / reuse ─────────────────────────────────────────────────


class TestMappingFilePersistence:
    @pytest.mark.asyncio
    async def test_mapping_saved_after_first_run(self, tmp_path):
        csv = make_csv(tmp_path, "name,hp\nPikachu,35\n")
        learner, store, llm = make_learner()
        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )
        )
        await learner.populate_from_structured(csv)
        assert (tmp_path / "data.csv.mapping.json").exists()

    @pytest.mark.asyncio
    async def test_mapping_reused_skips_llm_mapping_call(self, tmp_path):
        """Second run uses saved mapping — LLM called for triples only, not mapping."""
        csv = make_csv(tmp_path, "hp\n35\n40\n")
        learner, store, llm = make_learner()
        schema = make_schema()
        store.get_schema = AsyncMock(return_value=schema)

        # Pre-write mapping file
        mf = MappingFile(
            schema_hash=compute_schema_hash(schema),
            class_uri=None,
            id_column=None,
            columns=[
                ColumnMapping(
                    column_name="hp", predicate_uri="pk:hasHP", confidence=0.9
                )
            ],
            last_row=0,
        )
        from ontorag.learn.column_mapper import save_mapping

        save_mapping(mf, tmp_path / "data.csv.mapping.json")

        llm.complete = AsyncMock(
            side_effect=AssertionError("LLM mapping should not be called")
        )

        # Should not raise — mapping reused from file, LLM not called for mapping
        result = await learner.populate_from_structured(csv)
        assert isinstance(result, PopulationResult)

    @pytest.mark.asyncio
    async def test_stale_mapping_triggers_remap(self, tmp_path):
        """Stale schema hash → mapping re-proposed from LLM."""
        csv = make_csv(tmp_path, "hp\n35\n")
        learner, store, llm = make_learner()

        # Write mapping with wrong hash
        mf = MappingFile(
            schema_hash="stale_hash",
            class_uri=None,
            id_column=None,
            columns=[
                ColumnMapping(
                    column_name="hp", predicate_uri="pk:hasHP", confidence=0.9
                )
            ],
            last_row=0,
        )
        from ontorag.learn.column_mapper import save_mapping

        save_mapping(mf, tmp_path / "data.csv.mapping.json")

        llm_called = []

        async def tracking_complete(*args, **kwargs):
            llm_called.append(True)
            return make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )

        llm.complete = tracking_complete
        await learner.populate_from_structured(csv)
        assert len(llm_called) > 0  # LLM was called to re-map


# ── JSON / JSONL ──────────────────────────────────────────────────────────────


class TestPopulateFromStructuredJSON:
    @pytest.mark.asyncio
    async def test_json_file_works(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"name": "Pikachu", "hp": 35}]))
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )
        )

        result = await learner.populate_from_structured(f)
        assert len(result.triples) == 1

    @pytest.mark.asyncio
    async def test_jsonl_file_works(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"hp": 35}\n{"hp": 45}\n')
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )
        )

        result = await learner.populate_from_structured(f)
        assert len(result.triples) == 2

    @pytest.mark.asyncio
    async def test_nested_json_flattened_before_mapping(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps([{"stats": {"hp": 35}}]))
        learner, store, llm = make_learner()

        called_with_columns: list[list[str]] = []

        async def capture_complete(*args, **kwargs):
            # capture what columns were proposed
            prompt_text = str(args) + str(kwargs)
            if "stats.hp" in prompt_text:
                called_with_columns.append(["stats.hp"])
            return make_tool_response(
                mapping_llm_response(
                    [
                        {
                            "column": "stats.hp",
                            "predicate_uri": "pk:hasHP",
                            "confidence": 0.9,
                        }
                    ]
                )
            )

        llm.complete = capture_complete
        await learner.populate_from_structured(f)
        assert called_with_columns  # dotted key was passed to LLM


# ── batch processing ──────────────────────────────────────────────────────────


class TestBatchProcessing:
    @pytest.mark.asyncio
    async def test_large_file_processed_in_batches(self, tmp_path):
        rows = "\n".join(f"pokemon_{i},Electric,{30 + i}" for i in range(120))
        csv = make_csv(tmp_path, f"name,type,hp\n{rows}\n")
        learner, store, llm = make_learner()

        call_count = 0

        async def counting_complete(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )

        llm.complete = counting_complete
        await learner.populate_from_structured(csv, batch_size=50)
        # 1 mapping call + ceil(120/50)=3 batch calls = 4 total (or similar)
        # At minimum more than 1 call
        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_checkpoint_last_row_updated(self, tmp_path):
        rows = "\n".join(f"pokemon_{i},{i}" for i in range(60))
        csv = make_csv(tmp_path, f"name,hp\n{rows}\n")
        learner, store, llm = make_learner()

        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [{"column": "hp", "predicate_uri": "pk:hasHP", "confidence": 0.9}]
                )
            )
        )

        await learner.populate_from_structured(csv, batch_size=50)

        from ontorag.learn.column_mapper import load_mapping

        mf = load_mapping(tmp_path / "data.csv.mapping.json")
        assert mf.last_row == 60


# ── null / empty value handling (HIGH-3 fix) ──────────────────────────────────


class TestNullValueHandling:
    @pytest.mark.asyncio
    async def test_null_json_value_skipped(self, tmp_path):
        """JSON null values must not produce 'None'^^xsd:string literals."""
        import json as _json

        f = tmp_path / "data.json"
        f.write_text(_json.dumps([{"hp": None, "name": "Pikachu"}]))
        learner, store, llm = make_learner()
        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [
                        {
                            "column": "hp",
                            "predicate_uri": "pk:hasHP",
                            "confidence": 0.9,
                        },
                        {
                            "column": "name",
                            "predicate_uri": "rdfs:label",
                            "confidence": 0.95,
                        },
                    ]
                )
            )
        )
        result = await learner.populate_from_structured(f)
        # hp=null must be skipped; only the name triple is produced
        hp_triples = [t for t in result.triples if t.predicate_uri == "pk:hasHP"]
        assert len(hp_triples) == 0
        assert any("None" not in (t.object_value or "") for t in result.triples)

    @pytest.mark.asyncio
    async def test_empty_string_value_skipped(self, tmp_path):
        """Empty string values must not produce empty RDF literals."""
        csv = make_csv(tmp_path, "name,hp\nPikachu,\n")
        learner, store, llm = make_learner()
        llm.complete = AsyncMock(
            return_value=make_tool_response(
                mapping_llm_response(
                    [
                        {
                            "column": "hp",
                            "predicate_uri": "pk:hasHP",
                            "confidence": 0.9,
                        },
                    ]
                )
            )
        )
        result = await learner.populate_from_structured(csv)
        hp_triples = [t for t in result.triples if t.predicate_uri == "pk:hasHP"]
        assert len(hp_triples) == 0


# ── empty mapping cache guard (HIGH-2 fix) ────────────────────────────────────


class TestEmptyMappingCacheGuard:
    @pytest.mark.asyncio
    async def test_empty_mapping_not_written_to_cache(self, tmp_path):
        """If LLM returns no column mappings, the sidecar file must not be created."""
        csv = make_csv(tmp_path, "hp\n35\n")
        learner, store, llm = make_learner()
        llm.complete = AsyncMock(
            return_value=make_tool_response(mapping_llm_response([]))
        )
        await learner.populate_from_structured(csv)
        assert not (tmp_path / "data.csv.mapping.json").exists()
