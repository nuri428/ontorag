"""v0.8.1 CausalEngine — Rung 2 do-query + identification, vs hand-computed.

Requires the [bayes] extra (pgmpy); skipped otherwise. Quality bar: a confounded
model where do(X) != see(X), with hand-verified interventional values.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pgmpy", reason="causal inference requires the [bayes] extra")

from ontorag.bayes.engine import BayesianEngine
from ontorag.causal.engine import CausalEngine, CausalEngineError
from ontorag.core.bayes import BayesNetwork, BayesVariable, CPD
from ontorag.core.causal import CausalModel, CausalVariable

SM = "https://ontorag.dev/smoking#"
G, S, T, C = SM + "Genotype", SM + "Smoking", SM + "Tar", SM + "Cancer"


def _bn_gsc() -> BayesNetwork:
    """G -> S, G -> C, S -> C (genotype is an OBSERVED confounder)."""
    return BayesNetwork(
        name="smoking",
        variables=[
            BayesVariable(uri=G, states=["g0", "g1"], label="Genotype"),
            BayesVariable(uri=S, states=["no", "yes"], label="Smoking"),
            BayesVariable(uri=C, states=["no", "yes"], label="Cancer"),
        ],
        cpds=[
            CPD(variable=G, values=[[0.5], [0.5]]),
            CPD(variable=S, evidence=[G], values=[[0.8, 0.2], [0.2, 0.8]]),
            # evidence [S, G]; columns: (no,g0)(no,g1)(yes,g0)(yes,g1)
            CPD(
                variable=C,
                evidence=[S, G],
                values=[[0.9, 0.7, 0.6, 0.2], [0.1, 0.3, 0.4, 0.8]],
            ),
        ],
    )


def _causal_gsc(genotype_observed: bool) -> CausalModel:
    return CausalModel(
        name="smoking",
        based_on="https://ontorag.dev/bn#network",
        variables=[
            CausalVariable(uri=G, observed=genotype_observed),
            CausalVariable(uri=S),
            CausalVariable(uri=C),
        ],
        edges=[(G, S), (G, C), (S, C)],
    )


# ── Rung 2: do(X) != see(X) ───────────────────────────────────────────────────


async def test_do_intervention_value_is_hand_computed():
    # P(C=yes | do(S=yes)) = .4*.5 + .8*.5 = 0.60  (back-door adjusted over G)
    eng = CausalEngine(_bn_gsc(), _causal_gsc(genotype_observed=True))
    res = await eng.do_query(do={S: "yes"}, query=[C])
    assert abs(res[C]["yes"] - 0.60) < 1e-6


async def test_do_differs_from_observation():
    bn = _bn_gsc()
    do = await CausalEngine(bn).do_query(do={S: "yes"}, query=[C])
    see = await BayesianEngine(bn).compute_posterior(evidence={S: "yes"}, query=[C])
    # observation is confounded upward (0.72) vs intervention (0.60)
    assert abs(see[C]["yes"] - 0.72) < 1e-6
    assert abs(do[C]["yes"] - 0.60) < 1e-6
    assert abs(do[C]["yes"] - see[C]["yes"]) > 0.1


async def test_do_query_resolves_labels():
    eng = CausalEngine(_bn_gsc())
    res = await eng.do_query(do={"Smoking": "yes"}, query=["Cancer"])
    assert abs(res[C]["yes"] - 0.60) < 1e-6


# ── identification ────────────────────────────────────────────────────────────


async def test_identify_backdoor_with_observed_confounder():
    eng = CausalEngine(_bn_gsc(), _causal_gsc(genotype_observed=True))
    info = await eng.identify(S, C)
    assert info["identifiable"] is True
    assert info["backdoor_adjustment_set"] == [G]


async def test_identify_unidentifiable_with_latent_confounder_no_mediator():
    # G latent, no mediator between S and C → effect not identifiable.
    eng = CausalEngine(_bn_gsc(), _causal_gsc(genotype_observed=False))
    info = await eng.identify(S, C)
    assert info["identifiable"] is False


async def test_identify_frontdoor_via_mediator_with_latent_confounder():
    # G latent; S -> Tar -> C with G -> S, G -> C → front-door through Tar.
    bn_fd = BayesNetwork(
        variables=[
            BayesVariable(uri=S, states=["no", "yes"]),
            BayesVariable(uri=T, states=["no", "yes"]),
            BayesVariable(uri=C, states=["no", "yes"]),
        ],
        cpds=[
            CPD(variable=S, values=[[0.5], [0.5]]),
            CPD(variable=T, evidence=[S], values=[[0.9, 0.1], [0.1, 0.9]]),
            CPD(variable=C, evidence=[T], values=[[0.8, 0.2], [0.2, 0.8]]),
        ],
    )
    causal_fd = CausalModel(
        variables=[
            CausalVariable(uri=G, observed=False),
            CausalVariable(uri=S),
            CausalVariable(uri=T),
            CausalVariable(uri=C),
        ],
        edges=[(G, S), (G, C), (S, T), (T, C)],
    )
    info = await CausalEngine(bn_fd, causal_fd).identify(S, C)
    assert info["identifiable"] is True
    assert [T] in info["frontdoor_adjustment_sets"]


# ── error handling ────────────────────────────────────────────────────────────


async def test_empty_do_rejected():
    with pytest.raises(CausalEngineError, match="do must name"):
        await CausalEngine(_bn_gsc()).do_query(do={}, query=[C])


async def test_overlapping_do_and_query_rejected():
    with pytest.raises(CausalEngineError, match="disjoint"):
        await CausalEngine(_bn_gsc()).do_query(do={S: "yes"}, query=[S])


async def test_invalid_state_rejected():
    with pytest.raises(CausalEngineError, match="no state"):
        await CausalEngine(_bn_gsc()).do_query(do={S: "maybe"}, query=[C])


# ── Rung 3: counterfactuals (canonical-SCM abduction-action-prediction) ───────


def _bn_xy() -> BayesNetwork:
    """X -> Y; P(X=1)=.5, P(Y=1|X=0)=.2, P(Y=1|X=1)=.9. Hand-computable."""
    X, Y = SM + "X", SM + "Y"
    return BayesNetwork(
        variables=[
            BayesVariable(uri=X, states=["0", "1"]),
            BayesVariable(uri=Y, states=["0", "1"]),
        ],
        cpds=[
            CPD(variable=X, values=[[0.5], [0.5]]),
            CPD(variable=Y, evidence=[X], values=[[0.8, 0.1], [0.2, 0.9]]),
        ],
    )


async def test_counterfactual_hand_computed():
    # Observed X=1, Y=1. Counterfactual: had X been 0, what is Y?
    # Under the canonical independent SCM, Y's noise for the X=0 branch is
    # independent of the observed X=1 branch → P(Y_cf=1) = P(Y=1|X=0) = 0.2.
    X, Y = SM + "X", SM + "Y"
    eng = CausalEngine(_bn_xy())
    res = await eng.counterfactual(
        observed={X: "1", Y: "1"}, intervention={X: "0"}, query=[Y]
    )
    assert abs(res[Y]["1"] - 0.2) < 1e-9
    assert abs(res[Y]["0"] - 0.8) < 1e-9


async def test_counterfactual_consistency_axiom():
    # Intervening at the observed value reproduces the observed outcome (P=1).
    X, Y = SM + "X", SM + "Y"
    eng = CausalEngine(_bn_xy())
    res = await eng.counterfactual(
        observed={X: "1", Y: "1"}, intervention={X: "1"}, query=[Y]
    )
    assert abs(res[Y]["1"] - 1.0) < 1e-9


async def test_counterfactual_impossible_observation_rejected():
    # Y is deterministic =0 given X=0; observing Y=1 with X=0 is impossible.
    X, Y = SM + "X2", SM + "Y2"
    bn = BayesNetwork(
        variables=[
            BayesVariable(uri=X, states=["0", "1"]),
            BayesVariable(uri=Y, states=["0", "1"]),
        ],
        cpds=[
            CPD(variable=X, values=[[0.5], [0.5]]),
            CPD(variable=Y, evidence=[X], values=[[1.0, 0.5], [0.0, 0.5]]),
        ],
    )
    with pytest.raises(CausalEngineError, match="probability 0|impossible"):
        await CausalEngine(bn).counterfactual(
            observed={X: "0", Y: "1"}, intervention={X: "1"}, query=[Y]
        )


async def test_counterfactual_empty_intervention_rejected():
    X, Y = SM + "X", SM + "Y"
    with pytest.raises(CausalEngineError, match="intervention must name"):
        await CausalEngine(_bn_xy()).counterfactual(
            observed={X: "1"}, intervention={}, query=[Y]
        )
