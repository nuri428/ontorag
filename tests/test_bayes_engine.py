"""v0.7.3 BayesianEngine — verified against hand-computed posteriors.

Requires the [bayes] extra (pgmpy); skipped cleanly otherwise. This is the
v0.7 quality bar: a synthetic Pokémon BN (type matchup → battle outcome) whose
posteriors are computed by hand below and asserted against the engine output.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("pgmpy", reason="Bayesian inference requires the [bayes] extra")

from ontorag.bayes.engine import BayesianEngine, BayesianEngineError
from ontorag.core.bayes import BayesNetwork, BayesVariable, CPD

PK = "https://ontorag.dev/pokemon#"
MATCHUP = f"{PK}TypeMatchup"
OUTCOME = f"{PK}Outcome"


def _network() -> BayesNetwork:
    # Prior: P(adv)=0.4, P(neu)=0.3, P(dis)=0.3
    # P(win|adv)=0.8, P(win|neu)=0.5, P(win|dis)=0.2  (lose = complement)
    return BayesNetwork(
        name="Pokemon battle",
        variables=[
            BayesVariable(
                uri=MATCHUP,
                states=["advantage", "neutral", "disadvantage"],
                label="Type matchup",
            ),
            BayesVariable(uri=OUTCOME, states=["win", "lose"], label="Outcome"),
        ],
        cpds=[
            CPD(variable=MATCHUP, values=[[0.4], [0.3], [0.3]]),
            CPD(
                variable=OUTCOME,
                evidence=[MATCHUP],
                values=[[0.8, 0.5, 0.2], [0.2, 0.5, 0.8]],
            ),
        ],
    )


@pytest.fixture
def engine() -> BayesianEngine:
    return BayesianEngine(_network())


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return math.isclose(a, b, abs_tol=tol)


# ── posterior (Rung 1) ────────────────────────────────────────────────────────


async def test_marginal_outcome_no_evidence(engine):
    # P(win) = .4*.8 + .3*.5 + .3*.2 = .53
    post = await engine.compute_posterior(evidence={}, query=[OUTCOME])
    assert _close(post[OUTCOME]["win"], 0.53)
    assert _close(post[OUTCOME]["lose"], 0.47)


async def test_outcome_given_advantage_is_direct_cpt(engine):
    post = await engine.compute_posterior(
        evidence={MATCHUP: "advantage"}, query=[OUTCOME]
    )
    assert _close(post[OUTCOME]["win"], 0.8)
    assert _close(post[OUTCOME]["lose"], 0.2)


async def test_matchup_posterior_given_win_is_bayes_inverted(engine):
    # P(matchup | win) ∝ P(win|matchup) P(matchup); normaliser = .53
    post = await engine.compute_posterior(evidence={OUTCOME: "win"}, query=[MATCHUP])
    assert _close(post[MATCHUP]["advantage"], 0.32 / 0.53, tol=1e-4)
    assert _close(post[MATCHUP]["neutral"], 0.15 / 0.53, tol=1e-4)
    assert _close(post[MATCHUP]["disadvantage"], 0.06 / 0.53, tol=1e-4)


async def test_posterior_distribution_sums_to_one(engine):
    post = await engine.compute_posterior(evidence={OUTCOME: "lose"}, query=[MATCHUP])
    assert _close(sum(post[MATCHUP].values()), 1.0, tol=1e-9)


async def test_variable_resolved_by_label(engine):
    # "Outcome" label resolves to the OUTCOME uri.
    post = await engine.compute_posterior(evidence={}, query=["Outcome"])
    assert _close(post[OUTCOME]["win"], 0.53)


# ── MPE (Rung 1, most probable explanation) ──────────────────────────────────


async def test_mpe_no_evidence_picks_highest_joint(engine):
    # Joint maxima: (advantage, win) at .4*.8 = .32
    mpe = await engine.mpe(evidence={})
    assert mpe[MATCHUP] == "advantage"
    assert mpe[OUTCOME] == "win"


async def test_mpe_given_lose_favours_disadvantage(engine):
    # P(lose|matchup)P(matchup): adv .08, neu .15, dis .24 → disadvantage
    mpe = await engine.mpe(evidence={OUTCOME: "lose"})
    assert mpe[MATCHUP] == "disadvantage"
    assert OUTCOME not in mpe  # evidence variables are not in the MPE assignment


# ── error handling ────────────────────────────────────────────────────────────


async def test_unknown_variable_raises(engine):
    with pytest.raises(BayesianEngineError, match="Unknown variable"):
        await engine.compute_posterior(evidence={}, query=[f"{PK}Nonexistent"])


async def test_invalid_state_raises(engine):
    with pytest.raises(BayesianEngineError, match="no state"):
        await engine.compute_posterior(evidence={OUTCOME: "draw"}, query=[MATCHUP])


async def test_query_equals_evidence_rejected(engine):
    with pytest.raises(BayesianEngineError, match="cannot also be evidence"):
        await engine.compute_posterior(evidence={OUTCOME: "win"}, query=[OUTCOME])


async def test_missing_cpd_rejected():
    # A variable without a CPD cannot form a complete model.
    net = BayesNetwork(
        variables=[
            BayesVariable(uri=MATCHUP, states=["a", "b"]),
            BayesVariable(uri=OUTCOME, states=["win", "lose"]),
        ],
        cpds=[CPD(variable=MATCHUP, values=[[0.5], [0.5]])],
    )
    with pytest.raises(BayesianEngineError, match="missing CPD"):
        await BayesianEngine(net).compute_posterior(evidence={}, query=[MATCHUP])
