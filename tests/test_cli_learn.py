"""Tests for `ontorag learn` CLI subcommands (cli_learn.py).

Covers: type-term, taxonomy, extract, populate, populate-structured.
All external dependencies (LLM, Fuseki) are mocked via _get_learner patch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from ontorag.cli import app
from ontorag.learn.base import (
    ExtractedTriple,
    PopulationResult,
    TaxonomyRelation,
    TermTypingResult,
)
from ontorag.stores.base import ClassSummary, PropertySummary, SchemaResult

runner = CliRunner()

# ── Shared fixtures ────────────────────────────────────────────────────────────

POKEMON_SCHEMA = SchemaResult(
    total_classes=2,
    total_properties=1,
    namespaces={"pk": "http://example.org/pokemon#"},
    classes=[
        ClassSummary(
            uri="http://example.org/pokemon#Pokemon",
            label="Pokemon",
            property_count=3,
            instance_count=5,
        ),
    ],
    properties=[
        PropertySummary(
            uri="http://example.org/pokemon#hasType",
            label="hasType",
            prop_type="object",
        ),
    ],
)

SAMPLE_TYPINGS = [
    TermTypingResult(
        term="Pikachu",
        class_uri="http://example.org/pokemon#Pokemon",
        label="Pokemon",
        confidence=0.97,
        reasoning="Pikachu is clearly a Pokemon entity.",
    ),
]

SAMPLE_TAXONOMY = [
    TaxonomyRelation(
        child_term="LegendaryPokemon",
        parent_uri="http://example.org/pokemon#Pokemon",
        confidence=0.85,
    ),
]

SAMPLE_TRIPLES = [
    ExtractedTriple(
        subject_label="Pikachu",
        subject_uri="http://example.org/pokemon#Pikachu",
        predicate_uri="http://example.org/pokemon#hasType",
        object_uri="http://example.org/pokemon#Electric",
        confidence=0.92,
    ),
]


def make_mock_learner(
    typings=None,
    taxonomy=None,
    triples=None,
    population_result=None,
    schema=None,
    load_count=3,
):
    """Build a MagicMock learner with fully configured async methods."""
    learner = MagicMock()
    learner._store = MagicMock()
    learner._store.aclose = AsyncMock()
    learner._store.get_schema = AsyncMock(return_value=schema or POKEMON_SCHEMA)

    learner.type_term = AsyncMock(return_value=typings if typings is not None else SAMPLE_TYPINGS)
    learner.discover_taxonomy = AsyncMock(return_value=taxonomy if taxonomy is not None else SAMPLE_TAXONOMY)
    learner.extract_relations = AsyncMock(return_value=triples if triples is not None else SAMPLE_TRIPLES)

    default_result = population_result or PopulationResult(
        term_typings=SAMPLE_TYPINGS,
        taxonomy_proposals=SAMPLE_TAXONOMY,
        triples=SAMPLE_TRIPLES,
    )
    learner.populate_from_text = AsyncMock(return_value=default_result)
    learner.populate_from_structured = AsyncMock(return_value=PopulationResult(triples=SAMPLE_TRIPLES))
    learner._load_triples = AsyncMock(return_value=load_count)

    return learner


# ── type-term ──────────────────────────────────────────────────────────────────


class TestLearnTypeTerm:
    """Tests for `ontorag learn type-term` (Task A)."""

    def test_happy_path_returns_results(self):
        """type-term with results prints a classification table."""
        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "type-term", "Pikachu"])

        assert result.exit_code == 0
        assert "Pikachu" in result.output
        assert "Pokemon" in result.output

    def test_happy_path_with_context_and_top_k(self):
        """type-term passes --context and --top-k to learner.type_term."""
        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app,
                ["learn", "type-term", "Pikachu", "--context", "evolved Pokemon", "--top-k", "5"],
            )

        assert result.exit_code == 0
        call_kwargs = mock_learner.type_term.call_args
        assert call_kwargs.kwargs.get("context") == "evolved Pokemon" or call_kwargs.args[1] == "evolved Pokemon"

    def test_empty_results_prints_warning(self):
        """type-term with no results shows a yellow warning."""
        mock_learner = make_mock_learner(typings=[])
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "type-term", "Unknown"])

        assert result.exit_code == 0
        assert "결과 없음" in result.output

    def test_get_learner_value_error_exits_nonzero(self):
        """When _get_learner raises ValueError (bad config), exit code must be != 0."""
        with patch(
            "ontorag.cli_learn._get_learner",
            side_effect=SystemExit(1),
        ):
            result = runner.invoke(app, ["learn", "type-term", "React"])

        assert result.exit_code != 0

    def test_confidence_bar_displayed(self):
        """High-confidence result should show a non-empty progress bar."""
        mock_learner = make_mock_learner(
            typings=[
                TermTypingResult(
                    term="Bulbasaur",
                    class_uri="http://example.org/pokemon#Pokemon",
                    label="Pokemon",
                    confidence=0.9,
                    reasoning="Clearly a Pokemon.",
                )
            ]
        )
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "type-term", "Bulbasaur"])

        assert result.exit_code == 0
        assert "0.90" in result.output

    def test_reasoning_truncated_to_60_chars(self):
        """Reasoning text longer than 60 characters is trimmed in the table."""
        long_reason = "X" * 100
        mock_learner = make_mock_learner(
            typings=[
                TermTypingResult(
                    term="A",
                    class_uri="http://example.org/pokemon#Pokemon",
                    label="Pokemon",
                    confidence=0.8,
                    reasoning=long_reason,
                )
            ]
        )
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "type-term", "A"])

        assert result.exit_code == 0
        # Full 100-char string must not appear verbatim
        assert long_reason not in result.output

    def test_store_aclose_called_after_success(self):
        """_store.aclose() must be called even when type_term succeeds."""
        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            runner.invoke(app, ["learn", "type-term", "Pikachu"])

        mock_learner._store.aclose.assert_awaited_once()


# ── taxonomy ───────────────────────────────────────────────────────────────────


class TestLearnTaxonomy:
    """Tests for `ontorag learn taxonomy` (Task B)."""

    def test_happy_path_with_text_file(self, tmp_path):
        """taxonomy reads file and prints subClassOf proposals."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("Pikachu is an Electric-type Pokemon.", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "taxonomy", str(corpus)])

        assert result.exit_code == 0
        assert "LegendaryPokemon" in result.output or "Pokemon" in result.output

    def test_missing_file_exits_nonzero(self):
        """taxonomy with a non-existent file exits with code 1."""
        result = runner.invoke(app, ["learn", "taxonomy", "/nonexistent/corpus.txt"])

        assert result.exit_code != 0
        assert "Error" in result.output or "찾을 수 없습니다" in result.output

    def test_empty_results_prints_warning(self, tmp_path):
        """taxonomy with no proposals above threshold shows a warning."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("Nothing relevant.", encoding="utf-8")

        mock_learner = make_mock_learner(taxonomy=[])
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "taxonomy", str(corpus)])

        assert result.exit_code == 0
        assert "없음" in result.output

    def test_min_confidence_filters_low_quality(self, tmp_path):
        """--min-confidence 0.9 filters out a relation with confidence 0.85."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("Some text.", encoding="utf-8")

        # The fixture relation has confidence=0.85; raising threshold to 0.9 hides it.
        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "taxonomy", str(corpus), "--min-confidence", "0.9"]
            )

        assert result.exit_code == 0
        # At 0.9 threshold, the 0.85-confidence result is filtered out → warning shown
        assert "없음" in result.output

    def test_store_aclose_called_after_success(self, tmp_path):
        """_store.aclose() must be called after taxonomy discovery."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            runner.invoke(app, ["learn", "taxonomy", str(corpus)])

        mock_learner._store.aclose.assert_awaited_once()


# ── extract ────────────────────────────────────────────────────────────────────


class TestLearnExtract:
    """Tests for `ontorag learn extract` (Task C)."""

    def test_happy_path_prints_triples_table(self, tmp_path):
        """extract prints the triples table for a valid file."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("Pikachu has Electric type.", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "extract", str(corpus)])

        assert result.exit_code == 0
        assert "Pikachu" in result.output
        assert "트리플" in result.output

    def test_empty_results_prints_warning(self, tmp_path):
        """extract with no triples above threshold prints a warning."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("Irrelevant text.", encoding="utf-8")

        mock_learner = make_mock_learner(triples=[])
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "extract", str(corpus)])

        assert result.exit_code == 0
        assert "없음" in result.output

    def test_missing_file_exits_nonzero(self):
        """extract with a missing file exits with non-zero code."""
        result = runner.invoke(app, ["learn", "extract", "/no/such/file.txt"])

        assert result.exit_code != 0

    def test_triple_with_object_value_displayed(self, tmp_path):
        """Data-property triple with object_value (no object_uri) is displayed."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        data_triple = ExtractedTriple(
            subject_label="Charmander",
            subject_uri="http://example.org/pokemon#Charmander",
            predicate_uri="http://example.org/pokemon#hasName",
            object_value="Charmander",
            confidence=0.88,
        )
        mock_learner = make_mock_learner(triples=[data_triple])
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "extract", str(corpus)])

        assert result.exit_code == 0
        assert "Charmander" in result.output

    def test_store_aclose_called(self, tmp_path):
        """_store.aclose() is always called after extract."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")
        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            runner.invoke(app, ["learn", "extract", str(corpus)])

        mock_learner._store.aclose.assert_awaited_once()


# ── populate ───────────────────────────────────────────────────────────────────


class TestLearnPopulate:
    """Tests for `ontorag learn populate` (A+B+C pipeline)."""

    def test_happy_path_with_yes_flag_loads(self, tmp_path):
        """--yes skips confirmation and loads triples to Fuseki."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("Pikachu is an Electric-type Pokemon.", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "populate", str(corpus), "--yes"])

        assert result.exit_code == 0
        assert "로드했습니다" in result.output
        mock_learner._load_triples.assert_awaited_once()

    def test_confirmation_denied_cancels_load(self, tmp_path):
        """When user answers 'n' to confirmation, loading is cancelled."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            # provide 'n' as stdin
            result = runner.invoke(
                app, ["learn", "populate", str(corpus)], input="n\n"
            )

        assert result.exit_code == 0
        assert "취소" in result.output
        mock_learner._load_triples.assert_not_awaited()

    def test_confirmation_accepted_loads(self, tmp_path):
        """When user answers 'y', triples are loaded."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate", str(corpus)], input="y\n"
            )

        assert result.exit_code == 0
        assert "로드했습니다" in result.output

    def test_empty_population_result_exits_early(self, tmp_path):
        """When A+B+C returns nothing, command exits without prompting."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        empty_result = PopulationResult()
        mock_learner = make_mock_learner(population_result=empty_result)
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "populate", str(corpus), "--yes"])

        assert result.exit_code == 0
        assert "없음" in result.output
        mock_learner._load_triples.assert_not_awaited()

    def test_missing_file_exits_nonzero(self):
        """populate with a missing file exits with code 1."""
        result = runner.invoke(app, ["learn", "populate", "/no/such/file.txt"])

        assert result.exit_code != 0

    def test_only_taxonomy_no_triples_or_typings_skips_load(self, tmp_path):
        """If only taxonomy proposals exist (no triples, no typings), skip load."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        result_with_only_taxonomy = PopulationResult(
            taxonomy_proposals=SAMPLE_TAXONOMY,
        )
        mock_learner = make_mock_learner(population_result=result_with_only_taxonomy)
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "populate", str(corpus), "--yes"])

        assert result.exit_code == 0
        # The guard "if not result.triples and not result.term_typings" should fire
        assert "로드할 트리플이 없습니다" in result.output
        mock_learner._load_triples.assert_not_awaited()

    def test_task_sections_are_printed(self, tmp_path):
        """All three Task sections (A, B, C) are shown when data exists."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(app, ["learn", "populate", str(corpus), "--yes"])

        assert "Task A" in result.output
        assert "Task B" in result.output
        assert "Task C" in result.output

    def test_store_aclose_called_on_pipeline_and_load(self, tmp_path):
        """_store.aclose() is awaited for both pipeline and load phases."""
        corpus = tmp_path / "corpus.txt"
        corpus.write_text("text", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            runner.invoke(app, ["learn", "populate", str(corpus), "--yes"])

        # Called twice: once after pipeline, once after load
        assert mock_learner._store.aclose.await_count >= 2


# ── populate-structured ────────────────────────────────────────────────────────


class TestLearnPopulateStructured:
    """Tests for `ontorag learn populate-structured` (v0.3.1 structured input)."""

    def test_csv_file_with_yes_loads_triples(self, tmp_path):
        """CSV file + --yes creates triples and loads them."""
        csv_file = tmp_path / "pokemon.csv"
        csv_file.write_text("name,type\nPikachu,Electric\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(csv_file), "--yes"]
            )

        assert result.exit_code == 0
        assert "로드했습니다" in result.output
        mock_learner._load_triples.assert_awaited_once()

    def test_json_file_with_yes_loads_triples(self, tmp_path):
        """JSON file is accepted and processed."""
        json_file = tmp_path / "data.json"
        json_file.write_text('[{"name":"Pikachu","type":"Electric"}]', encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(json_file), "--yes"]
            )

        assert result.exit_code == 0

    def test_jsonl_file_with_yes_loads_triples(self, tmp_path):
        """JSONL file is accepted and processed."""
        jsonl_file = tmp_path / "data.jsonl"
        jsonl_file.write_text('{"name":"Pikachu"}\n', encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(jsonl_file), "--yes"]
            )

        assert result.exit_code == 0

    def test_nonexistent_file_exits_with_code_1(self):
        """Non-existent file path exits with code 1."""
        result = runner.invoke(
            app, ["learn", "populate-structured", "/no/such/file.csv"]
        )

        assert result.exit_code == 1
        assert "Error" in result.output or "찾을 수 없습니다" in result.output

    def test_unsupported_extension_exits_with_code_1(self, tmp_path):
        """Unsupported extension (.txt) exits with code 1."""
        txt_file = tmp_path / "corpus.txt"
        txt_file.write_text("data", encoding="utf-8")

        result = runner.invoke(
            app, ["learn", "populate-structured", str(txt_file)]
        )

        assert result.exit_code == 1
        assert "지원하지 않는 형식" in result.output or "Error" in result.output

    def test_value_error_from_pipeline_shows_yellow_warning(self, tmp_path):
        """ValueError (e.g., empty TBox) yields a yellow warning and exit code 1."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        mock_learner.populate_from_structured = AsyncMock(
            side_effect=ValueError("TBox가 비어 있습니다.")
        )
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(csv_file), "--yes"]
            )

        assert result.exit_code == 1
        assert "TBox가 비어 있습니다" in result.output

    def test_generic_exception_from_pipeline_shows_red_error(self, tmp_path):
        """Generic Exception yields a red error message and exit code 1."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        mock_learner.populate_from_structured = AsyncMock(
            side_effect=RuntimeError("LLM timeout")
        )
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(csv_file), "--yes"]
            )

        assert result.exit_code == 1
        assert "Error" in result.output or "실패" in result.output

    def test_zero_triples_result_exits_early(self, tmp_path):
        """When populate_from_structured returns no triples, exits early."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        mock_learner.populate_from_structured = AsyncMock(
            return_value=PopulationResult(triples=[])
        )
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(csv_file), "--yes"]
            )

        assert result.exit_code == 0
        assert "없음" in result.output
        mock_learner._load_triples.assert_not_awaited()

    def test_confirmation_denied_cancels_load(self, tmp_path):
        """Answering 'n' to the confirmation prompt cancels the load."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(csv_file)], input="n\n"
            )

        assert result.exit_code == 0
        assert "취소" in result.output
        mock_learner._load_triples.assert_not_awaited()

    def test_class_uri_and_id_column_options_accepted(self, tmp_path):
        """--class-uri and --id-column options are forwarded to populate_from_structured."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app,
                [
                    "learn",
                    "populate-structured",
                    str(csv_file),
                    "--class-uri", "pk:Pokemon",
                    "--id-column", "name",
                    "--yes",
                ],
            )

        assert result.exit_code == 0
        call_kwargs = mock_learner.populate_from_structured.call_args
        # Verify class_uri and id_column were forwarded
        assert call_kwargs.kwargs.get("class_uri") == "pk:Pokemon"
        assert call_kwargs.kwargs.get("id_column") == "name"

    def test_batch_size_option_forwarded(self, tmp_path):
        """--batch-size is forwarded to populate_from_structured."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            runner.invoke(
                app,
                ["learn", "populate-structured", str(csv_file), "--batch-size", "25", "--yes"],
            )

        call_kwargs = mock_learner.populate_from_structured.call_args
        assert call_kwargs.kwargs.get("batch_size") == 25

    def test_preview_table_shows_up_to_10_triples(self, tmp_path):
        """Preview table is capped at 10 triples when more exist."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        many_triples = [
            ExtractedTriple(
                subject_label=f"Pokemon{i}",
                subject_uri=f"http://example.org/pokemon#Pokemon{i}",
                predicate_uri="http://example.org/pokemon#hasType",
                object_uri="http://example.org/pokemon#Electric",
                confidence=0.9,
            )
            for i in range(15)
        ]
        mock_learner = make_mock_learner()
        mock_learner.populate_from_structured = AsyncMock(
            return_value=PopulationResult(triples=many_triples)
        )
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(csv_file), "--yes"]
            )

        assert result.exit_code == 0
        # Should mention 15 total and "상위 10건만 표시"
        assert "15" in result.output
        assert "10" in result.output

    def test_store_aclose_called_on_pipeline_and_load(self, tmp_path):
        """_store.aclose() is awaited for both pipeline and load phases."""
        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        mock_learner = make_mock_learner()
        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner):
            runner.invoke(
                app, ["learn", "populate-structured", str(csv_file), "--yes"]
            )

        assert mock_learner._store.aclose.await_count >= 2

    def test_mapping_file_display_when_exists(self, tmp_path):
        """When a .mapping.json exists, its content is displayed before the preview."""
        import json

        csv_file = tmp_path / "data.csv"
        csv_file.write_text("name\nPikachu\n", encoding="utf-8")

        # Create a mapping file alongside the CSV
        mapping_data = {
            "schema_hash": "abc123",
            "columns": [
                {
                    "column_name": "name",
                    "predicate_uri": "http://example.org/pokemon#hasName",
                    "confidence": 0.95,
                }
            ],
        }
        mapping_path = tmp_path / "data.csv.mapping.json"
        mapping_path.write_text(json.dumps(mapping_data), encoding="utf-8")

        mock_learner = make_mock_learner()
        # Mock load_mapping to avoid importing actual implementation
        mock_mapping = MagicMock()
        mock_col = MagicMock()
        mock_col.column_name = "name"
        mock_col.predicate_uri = "http://example.org/pokemon#hasName"
        mock_col.confidence = 0.95
        mock_mapping.columns = [mock_col]

        with patch("ontorag.cli_learn._get_learner", return_value=mock_learner), \
             patch("ontorag.learn.column_mapper.load_mapping", return_value=mock_mapping):
            result = runner.invoke(
                app, ["learn", "populate-structured", str(csv_file), "--yes"]
            )

        assert result.exit_code == 0
        assert "컬럼" in result.output or "매핑" in result.output


# ── _get_learner error path ────────────────────────────────────────────────────


class TestGetLearnerErrorPath:
    """Tests for the _get_learner() config-error path in the module."""

    def test_get_learner_raises_on_bad_llm_config(self, monkeypatch):
        """_get_learner() raises SystemExit(1) when LLM config is invalid."""
        from ontorag import cli_learn

        monkeypatch.setattr(
            "ontorag.llm.factory.get_llm_provider",
            lambda: (_ for _ in ()).throw(ValueError("No LLM provider configured")),
        )

        with pytest.raises((SystemExit, Exception)):
            cli_learn._get_learner()

    def test_type_term_exits_nonzero_when_get_learner_raises(self):
        """type-term exits non-zero when _get_learner raises SystemExit."""
        with patch("ontorag.cli_learn._get_learner", side_effect=SystemExit(1)):
            result = runner.invoke(app, ["learn", "type-term", "React"])

        assert result.exit_code != 0
