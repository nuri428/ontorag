"""Tests for ontorag.eval.metrics — inference, hallucination, citation."""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from ontorag.eval.goldset import Difficulty, Goldset
from ontorag.eval.metrics.citation import (
    citation_coverage,
    triple_supports_answer,
)
from ontorag.eval.metrics.hallucination import (
    hallucinated_triple_count,
    hallucination_rate,
)
from ontorag.eval.metrics.inference import (
    inference_utilization_score,
    system_uses_inference_features,
)

PURE_LAND_DIR = (
    Path(__file__).resolve().parent.parent / "examples" / "pure_land"
)
PL = "https://ontorag.dev/ns/pure_land#"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def pl_graph() -> Graph:
    g = Graph()
    g.parse(PURE_LAND_DIR / "schema.ttl", format="turtle")
    g.parse(PURE_LAND_DIR / "data.ttl", format="turtle")
    return g


@pytest.fixture()
def pl_goldset() -> Goldset:
    return Goldset.load(PURE_LAND_DIR / "goldset.jsonl")


# ── Inference Utilization ─────────────────────────────────────────────────────


class TestInferenceUtilization:
    def test_non_inference_question_returns_none(self, pl_goldset, pl_graph):
        easy = pl_goldset.by_difficulty(Difficulty.easy)[0]
        assert easy.uses_inference is False
        # Same graph for both — but expected behaviour is "not applicable".
        score = inference_utilization_score(easy, pl_graph, pl_graph)
        assert score is None

    def test_inference_question_with_identical_graphs_scores_zero(
        self, pl_goldset, pl_graph
    ):
        """If reasoning-on and reasoning-off graphs are identical, no row
        depends on reasoning, so the score is 0.0."""
        transitive_q = next(
            q for q in pl_goldset if q.category == "transitive_inference"
        )
        score = inference_utilization_score(transitive_q, pl_graph, pl_graph)
        assert score == 0.0

    def test_inference_question_with_reduced_graph_scores_above_zero(
        self, pl_goldset, pl_graph
    ):
        """Remove the transitive link's intermediate edge from the
        'without inference' graph: now the property-path query finds
        rows in 'with inference' that don't appear without it."""
        transitive_q = next(
            q for q in pl_goldset if q.category == "transitive_inference"
        )
        # Make a copy and strip the JeweledTree->Sukhavati link
        from rdflib import Graph as G2

        reduced = G2()
        for s, p, o in pl_graph:
            if str(s).endswith("JeweledTree_Canonical") and str(p).endswith(
                "locatedIn"
            ):
                continue
            reduced.add((s, p, o))
        score = inference_utilization_score(transitive_q, pl_graph, reduced)
        assert score is not None and score > 0.0

    def test_system_uses_property_paths(self):
        with_path = "SELECT ?x WHERE { :a :p+ ?x . }"
        without_path = "SELECT ?x WHERE { :a :p ?x . }"
        assert system_uses_inference_features(with_path) is True
        assert system_uses_inference_features(without_path) is False

    def test_system_uses_property_paths_star(self):
        q = "SELECT ?x WHERE { :a rdfs:subClassOf* ?x . }"
        assert system_uses_inference_features(q) is True


# ── Hallucination ─────────────────────────────────────────────────────────────


class TestHallucination:
    def test_empty_claims_zero_rate(self, pl_graph):
        assert hallucination_rate([], pl_graph) == 0.0
        counts = hallucinated_triple_count([], pl_graph)
        assert counts == {"total": 0, "hallucinated": 0, "grounded": 0}

    def test_real_triple_not_hallucinated(self, pl_graph):
        # Amitabha rdf:type pl:Buddha — exists in the data
        claimed = [
            (URIRef(PL + "Amitabha"), RDF.type, URIRef(PL + "Buddha"))
        ]
        assert hallucination_rate(claimed, pl_graph) == 0.0

    def test_fake_triple_is_hallucinated(self, pl_graph):
        # Sakyamuni hasVow Vow_99 — Vow_99 does not exist
        claimed = [
            (
                URIRef(PL + "Sakyamuni"),
                URIRef(PL + "hasVow"),
                URIRef(PL + "Vow_99"),
            )
        ]
        assert hallucination_rate(claimed, pl_graph) == 1.0

    def test_mixed_set_partial_rate(self, pl_graph):
        real = (URIRef(PL + "Amitabha"), RDF.type, URIRef(PL + "Buddha"))
        fake = (
            URIRef(PL + "Sakyamuni"),
            URIRef(PL + "hasVow"),
            URIRef(PL + "Vow_99"),
        )
        rate = hallucination_rate([real, fake], pl_graph)
        assert rate == 0.5

    def test_string_coercion_for_uris(self, pl_graph):
        # Strings should auto-coerce to URIRef for hashable URIs
        claimed = [
            (
                PL + "Amitabha",
                str(RDF.type),
                PL + "Buddha",
            )
        ]
        assert hallucination_rate(claimed, pl_graph) == 0.0

    def test_duplicate_claims_counted_once(self, pl_graph):
        real = (URIRef(PL + "Amitabha"), RDF.type, URIRef(PL + "Buddha"))
        counts = hallucinated_triple_count([real, real, real], pl_graph)
        assert counts["total"] == 1
        assert counts["grounded"] == 1
        assert counts["hallucinated"] == 0


# ── Citation Coverage ─────────────────────────────────────────────────────────


class TestCitationCoverage:
    def test_empty_citations_vacuous_one(self):
        assert citation_coverage("Some answer text.", []) == 1.0

    def test_perfect_overlap_one(self):
        # Triple mentions Amitabha and Buddha; answer mentions both.
        triple = (
            URIRef(PL + "Amitabha"),
            RDF.type,
            URIRef(PL + "Buddha"),
        )
        answer = "Amitabha is a Buddha residing in Sukhavati."
        assert citation_coverage(answer, [triple]) == 1.0

    def test_no_overlap_zero(self):
        triple = (
            URIRef(PL + "Avalokitesvara"),
            URIRef(PL + "assists"),
            URIRef(PL + "Amitabha"),
        )
        answer = "The weather today is pleasant."
        assert citation_coverage(answer, [triple]) == 0.0

    def test_partial_coverage(self):
        # Two triples, only one supported by the answer
        t1 = (
            URIRef(PL + "Amitabha"),
            RDF.type,
            URIRef(PL + "Buddha"),
        )
        t2 = (
            URIRef(PL + "Peacock"),
            URIRef(PL + "locatedIn"),
            URIRef(PL + "JeweledTree_Canonical"),
        )
        answer = "Amitabha is a Buddha; nothing else is mentioned."
        score = citation_coverage(answer, [t1, t2])
        assert 0.0 < score < 1.0

    def test_triple_supports_answer_threshold(self):
        triple = (
            URIRef(PL + "Amitabha"),
            RDFS.label,
            Literal("아미타불", lang="ko"),
        )
        # Answer mentions both subject local name and the Korean label
        assert (
            triple_supports_answer(triple, "아미타불 (Amitabha)", min_overlap=0.3)
            is True
        )

    def test_literal_value_matched(self):
        triple = (
            URIRef(PL + "Vow_18"),
            RDFS.label,
            Literal("Birth through ten recitations", lang="en"),
        )
        answer = "The eighteenth vow describes birth through ten recitations of the name."
        assert triple_supports_answer(triple, answer, min_overlap=0.3) is True
