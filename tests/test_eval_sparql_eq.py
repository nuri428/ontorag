"""Tests for ontorag.eval.metrics.sparql_eq — SPARQL result-set equivalence."""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph

from ontorag.eval.metrics.sparql_eq import (
    sparql_result_equivalent,
    sparql_result_jaccard,
)

PURE_LAND_DIR = (
    Path(__file__).resolve().parent.parent / "examples" / "pure_land"
)


@pytest.fixture()
def pl_graph() -> Graph:
    """Pure Land schema + data loaded into a single rdflib Graph."""
    g = Graph()
    g.parse(PURE_LAND_DIR / "schema.ttl", format="turtle")
    g.parse(PURE_LAND_DIR / "data.ttl", format="turtle")
    return g


# ── Equivalence ───────────────────────────────────────────────────────────────


class TestSparqlResultEquivalent:
    def test_identical_query_is_equivalent(self, pl_graph):
        q = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Bodhisattva ; pl:residesIn pl:Sukhavati . }"
        )
        assert sparql_result_equivalent(q, q, pl_graph) is True

    def test_variable_rename_is_equivalent(self, pl_graph):
        q_gold = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Bodhisattva ; pl:residesIn pl:Sukhavati . }"
        )
        q_system = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?bodhisattva WHERE { ?bodhisattva a pl:Bodhisattva ; pl:residesIn pl:Sukhavati . }"
        )
        assert sparql_result_equivalent(q_gold, q_system, pl_graph) is True

    def test_where_clause_reordering_is_equivalent(self, pl_graph):
        q_gold = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Bodhisattva ; pl:residesIn pl:Sukhavati . }"
        )
        q_reordered = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b pl:residesIn pl:Sukhavati . ?b a pl:Bodhisattva . }"
        )
        assert sparql_result_equivalent(q_gold, q_reordered, pl_graph) is True

    def test_different_result_sets_not_equivalent(self, pl_graph):
        q_bodhisattvas = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Bodhisattva ; pl:residesIn pl:Sukhavati . }"
        )
        q_buddhas = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Buddha ; pl:residesIn pl:Sukhavati . }"
        )
        assert sparql_result_equivalent(q_bodhisattvas, q_buddhas, pl_graph) is False

    def test_invalid_sparql_yields_empty_set(self, pl_graph):
        bad = "this is not sparql"
        good = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Buddha . }"
        )
        # both invalid → both empty → equivalent
        assert sparql_result_equivalent(bad, bad, pl_graph) is True
        # one invalid (empty), one valid (non-empty) → not equivalent
        assert sparql_result_equivalent(bad, good, pl_graph) is False


# ── Jaccard similarity ────────────────────────────────────────────────────────


class TestSparqlResultJaccard:
    def test_identical_queries_score_one(self, pl_graph):
        q = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Buddha . }"
        )
        assert sparql_result_jaccard(q, q, pl_graph) == 1.0

    def test_disjoint_queries_score_zero(self, pl_graph):
        q_buddhas = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?x WHERE { ?x a pl:Buddha . }"
        )
        q_colours = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?x WHERE { ?x a pl:LotusColor . }"
        )
        assert sparql_result_jaccard(q_buddhas, q_colours, pl_graph) == 0.0

    def test_partial_overlap_scores_between_zero_and_one(self, pl_graph):
        q_all_beings = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Buddha . } "
            "ORDER BY ?b LIMIT 5"
        )
        # superset: includes bodhisattvas too
        q_super = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { { ?b a pl:Buddha . } UNION { ?b a pl:Bodhisattva . } }"
        )
        score = sparql_result_jaccard(q_all_beings, q_super, pl_graph)
        assert 0.0 < score < 1.0

    def test_both_empty_scores_one(self, pl_graph):
        q_empty = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?x WHERE { pl:Sakyamuni pl:hasVow ?x . }"
        )
        assert sparql_result_jaccard(q_empty, q_empty, pl_graph) == 1.0

    def test_empty_vs_non_empty_scores_zero(self, pl_graph):
        q_empty = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?x WHERE { pl:Sakyamuni pl:hasVow ?x . }"
        )
        q_non_empty = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?x WHERE { pl:Amitabha pl:hasVow ?x . }"
        )
        assert sparql_result_jaccard(q_empty, q_non_empty, pl_graph) == 0.0

    def test_invalid_sparql_returns_zero_against_valid(self, pl_graph):
        bad = "garbage"
        good = (
            "PREFIX pl: <https://ontorag.dev/ns/pure_land#>\n"
            "SELECT ?b WHERE { ?b a pl:Buddha . }"
        )
        assert sparql_result_jaccard(bad, good, pl_graph) == 0.0


# ── Integration with the live Pure Land goldset ───────────────────────────────


class TestGoldsetIntegration:
    """Every goldset gold_sparql should be self-equivalent on the real graph."""

    def test_all_goldset_queries_self_equivalent(self, pl_graph):
        from ontorag.eval.goldset import Goldset

        gs = Goldset.load(PURE_LAND_DIR / "goldset.jsonl")
        for q in gs:
            assert sparql_result_equivalent(
                q.gold_sparql, q.gold_sparql, pl_graph
            ), f"Self-equivalence failed for {q.id}"
