"""v0.8.0 causal: vocabulary — spec validation + RDF round-trip (pure, no backend)."""

from __future__ import annotations

import pytest
from rdflib import Graph

from ontorag.core.causal import (
    CAUSAL,
    CAUSAL_GRAPH,
    CausalModel,
    CausalVariable,
    causal_graph_uri,
    graph_to_model,
    model_to_graph,
)

PK = "https://ontorag.dev/pokemon#"
SM = "https://ontorag.dev/smoking#"


def _confounder_model() -> CausalModel:
    """smoking → tar → cancer, with genotype (latent) confounding smoking & cancer."""
    return CausalModel(
        name="smoking-cancer",
        based_on="https://ontorag.dev/bn#network",
        variables=[
            CausalVariable(uri=SM + "Genotype", observed=False, label="Genotype"),
            CausalVariable(uri=SM + "Smoking", label="Smoking"),
            CausalVariable(uri=SM + "Tar", label="Tar"),
            CausalVariable(uri=SM + "Cancer", label="Cancer"),
        ],
        edges=[
            (SM + "Genotype", SM + "Smoking"),
            (SM + "Genotype", SM + "Cancer"),
            (SM + "Smoking", SM + "Tar"),
            (SM + "Tar", SM + "Cancer"),
        ],
    )


# ── causal_graph_uri ──────────────────────────────────────────────────────────


def test_causal_graph_uri_default_and_scoped():
    assert causal_graph_uri(None) == "urn:ontorag:causal"
    assert causal_graph_uri("pokemon") == "urn:ontorag:pokemon:causal"
    assert CAUSAL_GRAPH == "urn:ontorag:causal"


def test_causal_graph_uri_rejects_unsafe_id():
    with pytest.raises(ValueError, match="Invalid ontology id"):
        causal_graph_uri("evil} GRAPH")


# ── validation ────────────────────────────────────────────────────────────────


def test_valid_model_constructs():
    m = _confounder_model()
    assert m.latent_uris == [SM + "Genotype"]
    assert set(m.observed_uris) == {SM + "Smoking", SM + "Tar", SM + "Cancer"}
    assert m.parents_of(SM + "Cancer") == [SM + "Genotype", SM + "Tar"]


def test_edge_to_unknown_variable_rejected():
    with pytest.raises(ValueError, match="not a declared variable"):
        CausalModel(
            variables=[CausalVariable(uri=PK + "A")],
            edges=[(PK + "A", PK + "Ghost")],
        )


def test_self_loop_rejected():
    with pytest.raises(ValueError, match="Self-loop"):
        CausalModel(
            variables=[CausalVariable(uri=PK + "A")],
            edges=[(PK + "A", PK + "A")],
        )


def test_cycle_rejected():
    with pytest.raises(ValueError, match="acyclic"):
        CausalModel(
            variables=[CausalVariable(uri=PK + "A"), CausalVariable(uri=PK + "B")],
            edges=[(PK + "A", PK + "B"), (PK + "B", PK + "A")],
        )


def test_duplicate_variable_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        CausalModel(
            variables=[CausalVariable(uri=PK + "A"), CausalVariable(uri=PK + "A")],
        )


# ── RDF round-trip ─────────────────────────────────────────────────────────────


def _assert_same(a: CausalModel, b: CausalModel) -> None:
    assert a.name == b.name
    assert a.based_on == b.based_on
    assert sorted(a.variables, key=lambda v: v.uri) == sorted(
        b.variables, key=lambda v: v.uri
    )
    assert sorted(a.edges) == sorted(b.edges)


def test_round_trip_through_turtle():
    m = _confounder_model()
    ttl = model_to_graph(m).serialize(format="turtle")
    g = Graph()
    g.parse(data=ttl, format="turtle")
    _assert_same(graph_to_model(g), m)


def test_latent_flag_survives_round_trip():
    restored = graph_to_model(model_to_graph(_confounder_model()))
    geno = next(v for v in restored.variables if v.uri == SM + "Genotype")
    assert geno.observed is False
    smoking = next(v for v in restored.variables if v.uri == SM + "Smoking")
    assert smoking.observed is True


def test_based_on_link_preserved():
    restored = graph_to_model(model_to_graph(_confounder_model()))
    assert restored.based_on == "https://ontorag.dev/bn#network"


def test_empty_graph_returns_none():
    assert graph_to_model(Graph()) is None


def test_graph_uses_causal_namespace():
    g = model_to_graph(_confounder_model())
    assert (None, None, CAUSAL.Variable) in g
    assert (None, CAUSAL.influences, None) in g
