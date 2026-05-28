"""v0.7.3 Bayesian inference MCP tools: compute_posterior, mpe.

Both load the stored network from the active store (BayesianStore capability),
build a :class:`~ontorag.bayes.engine.BayesianEngine`, and answer the query.

Capability guard: a backend without ``get_bayes_network`` returns 501. A scope
with no stored network returns 404. Invalid evidence/query or a missing pgmpy
install returns 400 / 501 respectively (mapped from BayesianEngineError).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ontorag.api.deps import get_store
from ontorag.bayes.engine import BayesianEngine, BayesianEngineError
from ontorag.stores.base import GraphStore

router = APIRouter(prefix="/tools", tags=["tools"])


class PosteriorRequest(BaseModel):
    """Request body for P(query | evidence)."""

    query: list[str] = Field(
        min_length=1,
        description="Variables (URI or rdfs:label) to compute marginals for.",
    )
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="Observed variables as {variable: state_label}. Variable as "
        "URI or label; state as one of the variable's declared state labels.",
    )
    ontology: str | None = Field(
        default=None,
        description="Ontology id scoping the stored network; None = default.",
    )


class PosteriorResponse(BaseModel):
    """P(query | evidence): per-variable distribution over states."""

    posterior: dict[str, dict[str, float]] = Field(
        description="{variable_uri: {state_label: probability}}."
    )


class MpeRequest(BaseModel):
    """Request body for the most probable explanation."""

    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="Observed variables as {variable: state_label}.",
    )
    ontology: str | None = Field(
        default=None,
        description="Ontology id scoping the stored network; None = default.",
    )


class MpeResponse(BaseModel):
    """Most probable joint assignment to all non-evidence variables."""

    assignment: dict[str, str] = Field(
        description="{variable_uri: most_likely_state_label}."
    )


async def _load_engine(store: GraphStore, ontology: str | None) -> BayesianEngine:
    """Build an engine from the stored network, mapping failures to HTTP errors."""
    getter = getattr(store, "get_bayes_network", None)
    if getter is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "Bayesian inference is not supported by the active graph store "
                f"({type(store).__name__})."
            ),
        )
    network = await getter(ontology=ontology)
    if network is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No Bayesian network is stored for this scope. Define one with "
                "`ontorag bayes load <network.ttl>` (or PUT via BayesianStore)."
            ),
        )
    return BayesianEngine(network)


def _engine_error_status(exc: BayesianEngineError) -> int:
    """Missing pgmpy → 501 (capability unavailable); everything else → 400."""
    return 501 if "pgmpy" in str(exc).lower() else 400


@router.post(
    "/bayes/posterior",
    operation_id="compute_posterior",
    summary="확률 추론 — 증거가 주어졌을 때 질의 변수의 사후확률 P(query | evidence)",
    response_model=PosteriorResponse,
)
async def compute_posterior(
    body: PosteriorRequest,
    store: GraphStore = Depends(get_store),
) -> PosteriorResponse:
    """Compute posterior marginals P(query | evidence) over the stored BN.

    Args:
        body.query: variables (URI or label) to get distributions for.
        body.evidence: observed {variable: state} conditioning the query.
        body.ontology: scope of the stored network.

    Returns:
        Per-variable distributions over their states.

    Raises:
        HTTPException: 501 (no Bayesian backend / pgmpy not installed),
            404 (no stored network), 400 (invalid evidence/query/model).
    """
    engine = await _load_engine(store, body.ontology)
    try:
        posterior = await engine.compute_posterior(body.evidence, body.query)
    except BayesianEngineError as exc:
        raise HTTPException(status_code=_engine_error_status(exc), detail=str(exc))
    return PosteriorResponse(posterior=posterior)


@router.post(
    "/bayes/mpe",
    operation_id="mpe",
    summary="최대확률설명(MPE) — 증거가 주어졌을 때 가장 가능성 높은 전체 변수 할당",
    response_model=MpeResponse,
)
async def mpe(
    body: MpeRequest,
    store: GraphStore = Depends(get_store),
) -> MpeResponse:
    """Most probable explanation: the single most likely assignment to all
    non-evidence variables, given the evidence.

    Args:
        body.evidence: observed {variable: state}.
        body.ontology: scope of the stored network.

    Returns:
        The MAP assignment {variable_uri: state_label}.

    Raises:
        HTTPException: 501 (no Bayesian backend / pgmpy not installed),
            404 (no stored network), 400 (invalid evidence/model).
    """
    engine = await _load_engine(store, body.ontology)
    try:
        assignment = await engine.mpe(body.evidence)
    except BayesianEngineError as exc:
        raise HTTPException(status_code=_engine_error_status(exc), detail=str(exc))
    return MpeResponse(assignment=assignment)
