"""v0.8 causal inference MCP tools: do_query, identify (+ counterfactual v0.8.2).

Loads the quantified BN (BayesianStore) and the causal DAG (CausalStore) from
the active store, builds a CausalEngine, and answers interventional /
identification queries.

Over-claim guard: the causal DAG is user-supplied; results assume it is correctly
specified. ontorag does not validate causal semantics.

Capability guard: a backend without get_bayes_network → 501; no stored BN → 404;
invalid query / unidentifiable / missing pgmpy → 400 / 501.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ontorag.api.deps import get_store
from ontorag.causal.engine import CausalEngine, CausalEngineError
from ontorag.stores.base import GraphStore

router = APIRouter(prefix="/tools", tags=["tools"])


class DoQueryRequest(BaseModel):
    """Request body for an interventional query P(query | do(intervention))."""

    do: dict[str, str] = Field(
        description="Interventions as {variable: state}. Sets the variable by "
        "graph surgery (cutting incoming edges)."
    )
    query: list[str] = Field(
        min_length=1, description="Variables (URI or label) to get distributions for."
    )
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="Optional observed {variable: state} conditioned post-intervention.",
    )
    ontology: str | None = Field(default=None, description="Ontology scope; None=default.")


class DoQueryResponse(BaseModel):
    """P(query | do(intervention)): per-variable distribution over states."""

    result: dict[str, dict[str, float]] = Field(
        description="{variable_uri: {state: probability}} under the intervention."
    )


class IdentifyRequest(BaseModel):
    """Request body for adjustment-set / identifiability reporting."""

    treatment: str = Field(description="Treatment variable (URI or label).")
    outcome: str = Field(description="Outcome variable (URI or label).")
    ontology: str | None = Field(default=None, description="Ontology scope; None=default.")


class IdentifyResponse(BaseModel):
    """Adjustment sets and identifiability for treatment → outcome."""

    treatment: str
    outcome: str
    identifiable: bool
    backdoor_adjustment_set: list[str]
    frontdoor_adjustment_sets: list[list[str]]


class CounterfactualRequest(BaseModel):
    """Request body for a counterfactual query (Pearl Rung 3)."""

    observed: dict[str, str] = Field(
        default_factory=dict,
        description="The factual evidence actually observed {variable: state}.",
    )
    intervention: dict[str, str] = Field(
        description="The counterfactual antecedent {variable: state} — "
        "'had these variables been …'."
    )
    query: list[str] = Field(
        min_length=1,
        description="Variables (URI or label) to compute in the counterfactual world.",
    )
    ontology: str | None = Field(default=None, description="Ontology scope; None=default.")


class CounterfactualResponse(BaseModel):
    """P(query | observed, had intervention held): per-variable distribution."""

    result: dict[str, dict[str, float]] = Field(
        description="{variable_uri: {state: probability}} in the counterfactual world."
    )


async def _load_engine(store: GraphStore, ontology: str | None) -> CausalEngine:
    """Build a CausalEngine from the stored BN (+ optional causal DAG)."""
    bn_getter = getattr(store, "get_bayes_network", None)
    if bn_getter is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Causal inference is not supported by the active graph store "
                f"({type(store).__name__})."
            ),
        )
    bn = await bn_getter(ontology=ontology)
    if bn is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No Bayesian network is stored for this scope. The causal layer "
                "is quantified by the BN — define one with `ontorag bayes load`."
            ),
        )
    causal = None
    causal_getter = getattr(store, "get_causal_model", None)
    if causal_getter is not None:
        causal = await causal_getter(ontology=ontology)
    return CausalEngine(bn, causal)


def _status(exc: CausalEngineError) -> int:
    return 501 if "pgmpy" in str(exc).lower() else 400


@router.post(
    "/causal/do",
    operation_id="do_query",
    summary="개입 추론 — P(query | do(X)) 그래프 수술 기반 (Pearl Rung 2)",
    response_model=DoQueryResponse,
)
async def do_query(
    body: DoQueryRequest,
    store: GraphStore = Depends(get_store),
) -> DoQueryResponse:
    """Compute P(query | do(intervention), evidence) over the stored model.

    Unlike conditioning (``see``), ``do`` cuts incoming edges to the
    intervened variables, removing confounding. ontorag assumes the stored DAG
    is causally correct.

    Raises:
        HTTPException: 501 (no backend / pgmpy missing), 404 (no BN), 400
            (invalid do/query/evidence or unidentifiable).
    """
    engine = await _load_engine(store, body.ontology)
    try:
        result = await engine.do_query(body.do, body.query, body.evidence)
    except CausalEngineError as exc:
        raise HTTPException(status_code=_status(exc), detail=str(exc))
    return DoQueryResponse(result=result)


@router.post(
    "/causal/identify",
    operation_id="identify_effect",
    summary="식별 가능성 — treatment→outcome 의 backdoor/frontdoor 조정집합",
    response_model=IdentifyResponse,
)
async def identify_effect(
    body: IdentifyRequest,
    store: GraphStore = Depends(get_store),
) -> IdentifyResponse:
    """Report valid back-door / front-door adjustment sets and whether the
    causal effect treatment → outcome is identifiable from the causal DAG
    (latent confounders respected).

    Raises:
        HTTPException: 501 (no backend / pgmpy missing), 404 (no BN), 400.
    """
    engine = await _load_engine(store, body.ontology)
    try:
        info = await engine.identify(body.treatment, body.outcome)
    except CausalEngineError as exc:
        raise HTTPException(status_code=_status(exc), detail=str(exc))
    return IdentifyResponse(**info)


@router.post(
    "/causal/counterfactual",
    operation_id="counterfactual",
    summary="반사실 추론 — 관측이 주어졌을 때 '만약 X였다면' Y (Pearl Rung 3)",
    response_model=CounterfactualResponse,
)
async def counterfactual(
    body: CounterfactualRequest,
    store: GraphStore = Depends(get_store),
) -> CounterfactualResponse:
    """Counterfactual query: P(query | observed, had(intervention)).

    Computed via abduction-action-prediction under the canonical
    independent-noise SCM consistent with the CPTs. Counterfactuals are not
    uniquely identified by the CPTs alone; this is one standard SCM choice.

    Raises:
        HTTPException: 501 (no backend / pgmpy missing), 404 (no BN), 400
            (invalid inputs, impossible observation, or response space too large).
    """
    engine = await _load_engine(store, body.ontology)
    try:
        result = await engine.counterfactual(
            body.observed, body.intervention, body.query
        )
    except CausalEngineError as exc:
        raise HTTPException(status_code=_status(exc), detail=str(exc))
    return CounterfactualResponse(result=result)
