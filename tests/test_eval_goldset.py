"""Tests for ontorag.eval.goldset — Pydantic validation + JSONL loading.

Uses the live Pure Land goldset (examples/pure_land/goldset.jsonl) as a
fixture so structural changes to the file are caught by CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ontorag.eval.goldset import (
    Difficulty,
    Goldset,
    GoldsetQuestion,
    GoldsetValidationError,
)

PURE_LAND_GOLDSET = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "pure_land"
    / "goldset.jsonl"
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _valid_record(**overrides) -> dict:
    """Return a minimal valid GoldsetQuestion payload, with optional overrides."""
    base = {
        "id": "Q001",
        "difficulty": "easy",
        "category": "single_entity",
        "question_ko": "Pikachu의 타입은?",
        "question_en": "What is Pikachu's type?",
        "gold_sparql": "SELECT ?t WHERE { <urn:Pikachu> <urn:type> ?t . }",
        "gold_answer_ko": "Electric",
        "gold_answer_en": "Electric",
        "gold_triples": ["urn:Pikachu urn:type urn:Electric"],
        "uses_inference": False,
    }
    base.update(overrides)
    return base


# ── GoldsetQuestion validation ────────────────────────────────────────────────


class TestGoldsetQuestion:
    def test_valid_record_parses(self):
        q = GoldsetQuestion(**_valid_record())
        assert q.id == "Q001"
        assert q.difficulty is Difficulty.easy

    def test_id_pattern_rejects_invalid(self):
        with pytest.raises(ValidationError, match="pattern"):
            GoldsetQuestion(**_valid_record(id="bad-id"))

    def test_id_pattern_requires_three_digits(self):
        with pytest.raises(ValidationError):
            GoldsetQuestion(**_valid_record(id="Q1"))

    def test_unknown_difficulty_rejected(self):
        with pytest.raises(ValidationError):
            GoldsetQuestion(**_valid_record(difficulty="impossible"))

    def test_all_difficulty_values_accepted(self):
        for d in ("easy", "medium", "hard", "trap"):
            payload = _valid_record(difficulty=d)
            # trap requires trap_note; supply it
            if d == "trap":
                payload["trap_note"] = "explained"
            q = GoldsetQuestion(**payload)
            assert q.difficulty.value == d

    def test_invalid_sparql_rejected_at_construction(self):
        with pytest.raises(ValidationError, match="Invalid SPARQL syntax"):
            GoldsetQuestion(
                **_valid_record(gold_sparql="this is not sparql at all")
            )

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError, match="extra"):
            GoldsetQuestion(**_valid_record(unknown_key="x"))

    def test_inference_type_without_uses_inference_rejected(self):
        with pytest.raises(
            ValidationError, match="inference_type is set but uses_inference"
        ):
            GoldsetQuestion(
                **_valid_record(
                    uses_inference=False, inference_type="owl:TransitiveProperty"
                )
            )

    def test_inference_type_with_flag_accepted(self):
        q = GoldsetQuestion(
            **_valid_record(
                uses_inference=True, inference_type="owl:TransitiveProperty"
            )
        )
        assert q.inference_type == "owl:TransitiveProperty"

    def test_trap_difficulty_requires_trap_note(self):
        with pytest.raises(ValidationError, match="difficulty=trap requires"):
            GoldsetQuestion(**_valid_record(difficulty="trap"))

    def test_trap_with_note_accepted(self):
        q = GoldsetQuestion(
            **_valid_record(difficulty="trap", trap_note="probes hallucination")
        )
        assert q.trap_note == "probes hallucination"

    def test_empty_gold_triples_allowed_for_trap(self):
        q = GoldsetQuestion(
            **_valid_record(
                difficulty="trap",
                trap_note="absent in KG",
                gold_triples=[],
            )
        )
        assert q.gold_triples == []

    def test_prepared_query_returns_object(self):
        q = GoldsetQuestion(**_valid_record())
        prepared = q.prepared_query()
        assert prepared is not None


# ── Goldset (JSONL container) ─────────────────────────────────────────────────


class TestGoldsetLoad:
    def test_load_pure_land_goldset(self):
        gs = Goldset.load(PURE_LAND_GOLDSET)
        assert len(gs) == 10
        assert all(isinstance(q, GoldsetQuestion) for q in gs)

    def test_pure_land_difficulty_distribution(self):
        gs = Goldset.load(PURE_LAND_GOLDSET)
        dist = gs.distribution()
        assert dist[Difficulty.easy] == 3
        assert dist[Difficulty.medium] == 4
        assert dist[Difficulty.hard] == 2
        assert dist[Difficulty.trap] == 1

    def test_by_difficulty_slice(self):
        gs = Goldset.load(PURE_LAND_GOLDSET)
        traps = gs.by_difficulty("trap")
        assert len(traps) == 1
        assert traps[0].trap_note  # non-empty

    def test_by_category(self):
        gs = Goldset.load(PURE_LAND_GOLDSET)
        transitive = gs.by_category("transitive_inference")
        assert len(transitive) == 1
        assert transitive[0].uses_inference is True

    def test_missing_file_raises(self):
        with pytest.raises(GoldsetValidationError, match="not found"):
            Goldset.load("/no/such/path.jsonl")

    def test_malformed_json_line_includes_line_number(self, tmp_path):
        bad_file = tmp_path / "bad.jsonl"
        bad_file.write_text(
            json.dumps(_valid_record()) + "\n"
            "{ this is not json }\n",
            encoding="utf-8",
        )
        with pytest.raises(GoldsetValidationError, match=r"bad\.jsonl:2 invalid JSON"):
            Goldset.load(bad_file)

    def test_invalid_record_line_includes_line_number(self, tmp_path):
        bad_file = tmp_path / "bad.jsonl"
        bad = _valid_record(id="badid")
        bad_file.write_text(json.dumps(bad) + "\n", encoding="utf-8")
        with pytest.raises(GoldsetValidationError, match=r"bad\.jsonl:1 validation failed"):
            Goldset.load(bad_file)

    def test_blank_lines_ignored(self, tmp_path):
        f = tmp_path / "ok.jsonl"
        f.write_text(
            json.dumps(_valid_record()) + "\n\n   \n"
            + json.dumps(_valid_record(id="Q002")) + "\n",
            encoding="utf-8",
        )
        gs = Goldset.load(f)
        assert len(gs) == 2

    def test_duplicate_ids_rejected(self, tmp_path):
        f = tmp_path / "dup.jsonl"
        f.write_text(
            json.dumps(_valid_record()) + "\n"
            + json.dumps(_valid_record()) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(GoldsetValidationError, match="Duplicate question IDs"):
            Goldset.load(f)


# ── Pure Land goldset semantic spot-checks ────────────────────────────────────


class TestPureLandGoldsetIntegrity:
    """Checks that ground-truth structure matches what the benchmark needs."""

    def test_all_questions_have_both_languages(self):
        gs = Goldset.load(PURE_LAND_GOLDSET)
        for q in gs:
            assert q.question_ko and q.question_en
            assert q.gold_answer_ko and q.gold_answer_en

    def test_trap_question_has_empty_gold_triples(self):
        gs = Goldset.load(PURE_LAND_GOLDSET)
        traps = gs.by_difficulty("trap")
        assert len(traps) == 1
        assert traps[0].gold_triples == []

    def test_inference_questions_flag_type(self):
        gs = Goldset.load(PURE_LAND_GOLDSET)
        for q in gs:
            if q.uses_inference:
                assert q.inference_type is not None, (
                    f"{q.id} uses_inference=True but inference_type missing"
                )
