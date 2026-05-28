"""v0.7.1 bn: vocabulary — spec validation + RDF round-trip (pure, no backend)."""

from __future__ import annotations

import pytest
from rdflib import Graph

from ontorag.core.bayes import (
    BN,
    PROBABILISTIC_GRAPH,
    BayesNetwork,
    BayesVariable,
    CPD,
    StructureSpec,
    graph_to_network,
    graph_to_structure,
    network_to_graph,
    probabilistic_graph_uri,
    structure_to_graph,
)

PK = "https://ontorag.dev/pokemon#"


def _matchup_network() -> BayesNetwork:
    """Tiny 2-node BN: type matchup (prior) → battle outcome (conditional)."""
    return BayesNetwork(
        name="Pokemon battle",
        variables=[
            BayesVariable(
                uri=f"{PK}TypeMatchup",
                states=["advantage", "neutral", "disadvantage"],
                label="Type matchup",
                represents=f"{PK}hasType",
            ),
            BayesVariable(
                uri=f"{PK}Outcome",
                states=["win", "lose"],
                label="Battle outcome",
            ),
        ],
        cpds=[
            CPD(variable=f"{PK}TypeMatchup", values=[[0.4], [0.3], [0.3]]),
            CPD(
                variable=f"{PK}Outcome",
                evidence=[f"{PK}TypeMatchup"],
                # cols enumerate TypeMatchup states: advantage, neutral, disadvantage
                values=[
                    [0.8, 0.5, 0.2],  # win
                    [0.2, 0.5, 0.8],  # lose
                ],
            ),
        ],
    )


# ── probabilistic_graph_uri ───────────────────────────────────────────────────


def test_probabilistic_graph_uri_default_and_scoped():
    assert probabilistic_graph_uri(None) == "urn:ontorag:probabilistic"
    assert probabilistic_graph_uri("pokemon") == "urn:ontorag:pokemon:probabilistic"
    assert PROBABILISTIC_GRAPH == "urn:ontorag:probabilistic"


def test_probabilistic_graph_uri_rejects_unsafe_id():
    with pytest.raises(ValueError, match="Invalid ontology id"):
        probabilistic_graph_uri("evil} GRAPH")


# ── spec validation ───────────────────────────────────────────────────────────


def test_valid_network_constructs():
    net = _matchup_network()
    assert len(net.variables) == 2
    assert net.variable(f"{PK}Outcome").cardinality == 2


def test_unknown_evidence_rejected():
    with pytest.raises(ValueError, match="unknown evidence"):
        BayesNetwork(
            variables=[BayesVariable(uri=f"{PK}A", states=["t", "f"])],
            cpds=[
                CPD(
                    variable=f"{PK}A",
                    evidence=[f"{PK}Missing"],
                    values=[[0.5, 0.5], [0.5, 0.5]],
                )
            ],
        )


def test_wrong_column_count_rejected():
    # Outcome has 1 evidence var of cardinality 3 → 3 columns required, give 2.
    with pytest.raises(ValueError, match="columns"):
        BayesNetwork(
            variables=[
                BayesVariable(uri=f"{PK}M", states=["a", "n", "d"]),
                BayesVariable(uri=f"{PK}O", states=["win", "lose"]),
            ],
            cpds=[
                CPD(
                    variable=f"{PK}O",
                    evidence=[f"{PK}M"],
                    values=[[0.8, 0.2], [0.2, 0.8]],
                )
            ],
        )


def test_column_not_summing_to_one_rejected():
    with pytest.raises(ValueError, match="sums to"):
        BayesNetwork(
            variables=[BayesVariable(uri=f"{PK}A", states=["t", "f"])],
            cpds=[CPD(variable=f"{PK}A", values=[[0.6], [0.6]])],
        )


def test_duplicate_variable_rejected():
    with pytest.raises(ValueError, match="Duplicate variable"):
        BayesNetwork(
            variables=[
                BayesVariable(uri=f"{PK}A", states=["t", "f"]),
                BayesVariable(uri=f"{PK}A", states=["x", "y"]),
            ]
        )


def test_non_unique_states_rejected():
    with pytest.raises(ValueError, match="unique"):
        BayesVariable(uri=f"{PK}A", states=["t", "t"])


# ── RDF round-trip ─────────────────────────────────────────────────────────────


def _assert_same_network(a: BayesNetwork, b: BayesNetwork) -> None:
    """Compare order-insensitively — graph_to_network sorts for determinism,
    so element order may differ from the authored fixture (set semantics)."""
    assert a.name == b.name
    assert sorted(a.variables, key=lambda v: v.uri) == sorted(
        b.variables, key=lambda v: v.uri
    )
    assert sorted(a.cpds, key=lambda c: c.variable) == sorted(
        b.cpds, key=lambda c: c.variable
    )


def test_round_trip_through_graph():
    net = _matchup_network()
    restored = graph_to_network(network_to_graph(net))
    _assert_same_network(restored, net)


def test_round_trip_through_turtle_serialization():
    # The whole point of the GSP path: serialize to turtle, parse back.
    net = _matchup_network()
    ttl = network_to_graph(net).serialize(format="turtle")
    g2 = Graph()
    g2.parse(data=ttl, format="turtle")
    restored = graph_to_network(g2)
    _assert_same_network(restored, net)


def test_graph_to_network_output_is_sorted_deterministic():
    # Determinism guarantee: two parses of the same content compare equal.
    net = _matchup_network()
    g = network_to_graph(net)
    assert graph_to_network(g) == graph_to_network(g)


def test_state_and_evidence_order_preserved():
    net = _matchup_network()
    restored = graph_to_network(network_to_graph(net))
    matchup = restored.variable(f"{PK}TypeMatchup")
    assert matchup.states == ["advantage", "neutral", "disadvantage"]
    outcome_cpd = next(c for c in restored.cpds if c.variable == f"{PK}Outcome")
    assert outcome_cpd.evidence == [f"{PK}TypeMatchup"]
    assert outcome_cpd.values == [[0.8, 0.5, 0.2], [0.2, 0.5, 0.8]]


def test_empty_graph_returns_none():
    assert graph_to_network(Graph()) is None


def test_graph_uses_bn_namespace():
    g = network_to_graph(_matchup_network())
    assert (None, None, BN.Variable) in g  # at least one bn:Variable typed node


# ── StructureSpec (CPT-learning input) ────────────────────────────────────────


def _structure() -> StructureSpec:
    return StructureSpec(
        name="Pokemon battle",
        variables=[
            BayesVariable(
                uri=f"{PK}TypeMatchup",
                states=["advantage", "neutral", "disadvantage"],
                represents=f"{PK}matchupKind",
            ),
            BayesVariable(
                uri=f"{PK}Outcome", states=["win", "lose"], represents=f"{PK}result"
            ),
        ],
        edges=[(f"{PK}TypeMatchup", f"{PK}Outcome")],
    )


def test_structure_round_trip_through_turtle():
    spec = _structure()
    ttl = structure_to_graph(spec).serialize(format="turtle")
    g = Graph()
    g.parse(data=ttl, format="turtle")
    restored = graph_to_structure(g)
    assert restored is not None
    assert restored.name == spec.name
    assert {v.uri for v in restored.variables} == {v.uri for v in spec.variables}
    assert restored.edges == [(f"{PK}TypeMatchup", f"{PK}Outcome")]


def test_structure_parents_of():
    assert _structure().parents_of(f"{PK}Outcome") == [f"{PK}TypeMatchup"]
    assert _structure().parents_of(f"{PK}TypeMatchup") == []


def test_structure_edge_to_unknown_variable_rejected():
    with pytest.raises(ValueError, match="not a declared variable"):
        StructureSpec(
            variables=[BayesVariable(uri=f"{PK}A", states=["t", "f"])],
            edges=[(f"{PK}A", f"{PK}Ghost")],
        )


def test_empty_graph_returns_none_structure():
    assert graph_to_structure(Graph()) is None
