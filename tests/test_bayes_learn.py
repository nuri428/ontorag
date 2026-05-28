"""v0.7.4 CPT learning from ABox data.

Requires the [bayes] extra (pgmpy + pandas); skipped otherwise. Validates the
learn → store-format → engine pipeline end-to-end so the test does not depend
on pgmpy's internal CPT matrix layout.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pgmpy", reason="CPT learning requires the [bayes] extra")
pytest.importorskip("pandas")

from ontorag.bayes.engine import BayesianEngine, BayesianEngineError
from ontorag.bayes.learn import learn_cpts
from ontorag.core.bayes import BayesVariable, StructureSpec
from ontorag.stores.base import EntityResult

PK = "https://ontorag.dev/pokemon#"
MATCHUP = f"{PK}TypeMatchup"
OUTCOME = f"{PK}Outcome"
MATCHUP_PROP = f"{PK}matchupKind"
OUTCOME_PROP = f"{PK}battleResult"
TARGET_CLASS = f"{PK}Battle"


def _structure() -> StructureSpec:
    return StructureSpec(
        name="Pokemon battle",
        variables=[
            BayesVariable(
                uri=MATCHUP,
                states=["advantage", "neutral", "disadvantage"],
                represents=MATCHUP_PROP,
            ),
            BayesVariable(uri=OUTCOME, states=["win", "lose"], represents=OUTCOME_PROP),
        ],
        edges=[(MATCHUP, OUTCOME)],
    )


def _entity(i: int, matchup: str, outcome: str) -> EntityResult:
    return EntityResult(
        uri=f"{PK}battle{i}",
        label=None,
        class_uri=TARGET_CLASS,
        properties={MATCHUP_PROP: matchup, OUTCOME_PROP: outcome},
    )


def _deterministic_battles() -> list[EntityResult]:
    """advantage: 3 win / 1 lose (0.75); neutral: 1/1 (0.5); disadvantage: 1 win / 3 lose (0.25)."""
    rows: list[tuple[str, str]] = (
        [("advantage", "win")] * 3
        + [("advantage", "lose")] * 1
        + [("neutral", "win")] * 1
        + [("neutral", "lose")] * 1
        + [("disadvantage", "win")] * 1
        + [("disadvantage", "lose")] * 3
    )
    return [_entity(i, m, o) for i, (m, o) in enumerate(rows)]


class _MockStore:
    def __init__(self, entities: list[EntityResult]) -> None:
        self._entities = entities
        self.queried_class: str | None = None

    async def find_entities(self, class_uri, filters=None, limit=100, ontology=None):
        self.queried_class = class_uri
        return self._entities


async def test_learn_recovers_conditional_frequencies():
    store = _MockStore(_deterministic_battles())
    network, n_obs = await learn_cpts(
        store, _structure(), TARGET_CLASS, estimator="mle"
    )
    assert n_obs == 10
    assert store.queried_class == TARGET_CLASS

    # Validate via the engine (layout-agnostic): P(win | advantage) == 3/4.
    engine = BayesianEngine(network)
    post_adv = await engine.compute_posterior({MATCHUP: "advantage"}, [OUTCOME])
    assert abs(post_adv[OUTCOME]["win"] - 0.75) < 1e-6
    post_dis = await engine.compute_posterior({MATCHUP: "disadvantage"}, [OUTCOME])
    assert abs(post_dis[OUTCOME]["win"] - 0.25) < 1e-6


async def test_learn_recovers_prior_frequencies():
    store = _MockStore(_deterministic_battles())
    network, _ = await learn_cpts(store, _structure(), TARGET_CLASS, estimator="mle")
    engine = BayesianEngine(network)
    # Marginal P(TypeMatchup): advantage 4/10, neutral 2/10, disadvantage 4/10.
    prior = await engine.compute_posterior({}, [MATCHUP])
    assert abs(prior[MATCHUP]["advantage"] - 0.4) < 1e-6
    assert abs(prior[MATCHUP]["neutral"] - 0.2) < 1e-6
    assert abs(prior[MATCHUP]["disadvantage"] - 0.4) < 1e-6


async def test_learn_skips_instances_with_missing_or_invalid_values():
    entities = _deterministic_battles() + [
        EntityResult(  # missing OUTCOME_PROP → skipped
            uri=f"{PK}battleX",
            label=None,
            class_uri=TARGET_CLASS,
            properties={MATCHUP_PROP: "advantage"},
        ),
        EntityResult(  # invalid matchup state → skipped
            uri=f"{PK}battleY",
            label=None,
            class_uri=TARGET_CLASS,
            properties={MATCHUP_PROP: "super", OUTCOME_PROP: "win"},
        ),
    ]
    store = _MockStore(entities)
    _, n_obs = await learn_cpts(store, _structure(), TARGET_CLASS, estimator="mle")
    assert n_obs == 10  # the two bad rows dropped


async def test_no_usable_observations_raises():
    store = _MockStore([])
    with pytest.raises(BayesianEngineError, match="No usable observations"):
        await learn_cpts(store, _structure(), TARGET_CLASS, estimator="mle")


async def test_variable_without_represents_raises():
    bad = StructureSpec(
        variables=[BayesVariable(uri=MATCHUP, states=["advantage", "neutral"])],
        edges=[],
    )
    store = _MockStore(_deterministic_battles())
    with pytest.raises(BayesianEngineError, match="bn:represents"):
        await learn_cpts(store, bad, TARGET_CLASS)
