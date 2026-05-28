"""Causal-graph vocabulary, models, and RDF (de)serialization (v0.8.0).

The causal layer (Pearl Rung 2/3) sits on top of the probabilistic layer: the
*quantified* model (CPTs) is the Bayesian network in ``urn:ontorag:probabilistic``
(v0.7); this module stores only the **causal metadata** in a dedicated
``urn:ontorag:causal`` named graph:

- the causal DAG (directed cause → effect edges), which asserts a *causal*
  reading of the structure;
- which variables are *observed* vs *latent* (unobserved confounders) — this
  decides which interventional queries are identifiable and which adjustment
  set is valid;
- a link (``causal:basedOn``) to the BN that quantifies the observed variables.

**Over-claim guard (CLAUDE.md):** the causal DAG is *user-supplied*. ontorag
computes interventional / counterfactual queries *assuming the DAG is correctly
specified*; it does **not** validate causal semantics or discover causation.

This module is pgmpy-agnostic: vocabulary + pydantic models + Graph ↔ model
mapping only. The inference wrapper lives in ``causal/engine.py`` (v0.8.1+).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD

from ontorag.core.ontology import validate_ontology_id

# ── Vocabulary ────────────────────────────────────────────────────────────────

#: The ``causal:`` vocabulary namespace (stable IRI; need not resolve).
CAUSAL = Namespace("https://ontorag.dev/causal#")

#: Singleton causal-model node within a causal graph.
CAUSAL_MODEL_NODE = URIRef(CAUSAL["model"])

#: Default (ontology=None) causal named-graph URI. Causal metadata goes here only.
CAUSAL_GRAPH = "urn:ontorag:causal"


def causal_graph_uri(ontology: str | None) -> str:
    """Named-graph URI for the causal-metadata layer.

    Kept separate from :data:`ontorag.core.ontology.OntologyLayer` (a document
    layer model) and from the probabilistic graph — it is reasoning-stack
    storage. Reuses only the ontology-id validation for consistent scoping.
    """
    ontology = validate_ontology_id(ontology)
    if ontology is None:
        return CAUSAL_GRAPH
    return f"urn:ontorag:{ontology}:causal"


# ── Spec models ───────────────────────────────────────────────────────────────


class CausalVariable(BaseModel):
    """A node in the causal DAG.

    ``observed=False`` marks a latent / unobserved confounder — it has no CPT in
    the BN and cannot be conditioned on; it only constrains which adjustment
    sets are valid for identification.
    """

    uri: str
    observed: bool = True
    label: str | None = None


class CausalModel(BaseModel):
    """A causal DAG over a set of variables, linked to a quantifying BN.

    Edges are ``(cause_uri, effect_uri)`` directed arcs. The observed variables
    should correspond to BN variables (same URIs) so the engine can pull CPTs;
    latent variables exist only in this DAG.
    """

    variables: list[CausalVariable] = Field(min_length=1)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    based_on: str | None = Field(
        default=None,
        description="URI of the bn:network this causal model is quantified by.",
    )
    name: str | None = None

    @model_validator(mode="after")
    def _check(self) -> CausalModel:
        uris = {v.uri for v in self.variables}
        if len(uris) != len(self.variables):
            raise ValueError("Duplicate causal variable uri.")
        for cause, effect in self.edges:
            if cause not in uris:
                raise ValueError(f"Edge cause {cause!r} is not a declared variable.")
            if effect not in uris:
                raise ValueError(f"Edge effect {effect!r} is not a declared variable.")
            if cause == effect:
                raise ValueError(f"Self-loop edge on {cause!r} is not allowed.")
        self._assert_acyclic(uris)
        return self

    def _assert_acyclic(self, uris: set[str]) -> None:
        """Reject cyclic DAGs early (a causal DAG must be acyclic)."""
        adj: dict[str, list[str]] = {u: [] for u in uris}
        for cause, effect in self.edges:
            adj[cause].append(effect)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(uris, WHITE)

        def visit(node: str) -> None:
            color[node] = GRAY
            for nxt in adj[node]:
                if color[nxt] == GRAY:
                    raise ValueError("Causal graph must be acyclic (cycle detected).")
                if color[nxt] == WHITE:
                    visit(nxt)
            color[node] = BLACK

        for u in uris:
            if color[u] == WHITE:
                visit(u)

    @property
    def observed_uris(self) -> list[str]:
        return [v.uri for v in self.variables if v.observed]

    @property
    def latent_uris(self) -> list[str]:
        return [v.uri for v in self.variables if not v.observed]

    def parents_of(self, uri: str) -> list[str]:
        return [c for c, e in self.edges if e == uri]


# ── RDF serialization ───────────────────────────────────────────────────────


def model_to_graph(model: CausalModel) -> Graph:
    """Serialize a CausalModel to ``causal:`` triples."""
    g = Graph()
    g.bind("causal", CAUSAL)
    g.bind("rdfs", RDFS)

    g.add((CAUSAL_MODEL_NODE, RDF.type, CAUSAL.CausalModel))
    if model.name:
        g.add((CAUSAL_MODEL_NODE, RDFS.label, Literal(model.name)))
    if model.based_on:
        g.add((CAUSAL_MODEL_NODE, CAUSAL.basedOn, URIRef(model.based_on)))

    for var in model.variables:
        vu = URIRef(var.uri)
        g.add((vu, RDF.type, CAUSAL.Variable))
        g.add((CAUSAL_MODEL_NODE, CAUSAL.hasVariable, vu))
        g.add((vu, CAUSAL.observed, Literal(var.observed, datatype=XSD.boolean)))
        if var.label:
            g.add((vu, RDFS.label, Literal(var.label)))

    for cause, effect in model.edges:
        # cause causal:influences effect
        g.add((URIRef(cause), CAUSAL.influences, URIRef(effect)))

    return g


def graph_to_model(g: Graph) -> CausalModel | None:
    """Reconstruct a CausalModel from ``causal:`` triples (None if empty)."""
    variables: list[CausalVariable] = []
    for vu in g.subjects(RDF.type, CAUSAL.Variable):
        obs_lit = g.value(vu, CAUSAL.observed)
        observed = True if obs_lit is None else bool(obs_lit.toPython())
        label = g.value(vu, RDFS.label)
        variables.append(
            CausalVariable(
                uri=str(vu),
                observed=observed,
                label=str(label) if label is not None else None,
            )
        )
    if not variables:
        return None

    edges: list[tuple[str, str]] = []
    for cause, effect in g.subject_objects(CAUSAL.influences):
        edges.append((str(cause), str(effect)))

    based_on = g.value(CAUSAL_MODEL_NODE, CAUSAL.basedOn)
    name = g.value(CAUSAL_MODEL_NODE, RDFS.label)
    variables.sort(key=lambda v: v.uri)
    edges.sort()
    return CausalModel(
        variables=variables,
        edges=edges,
        based_on=str(based_on) if based_on is not None else None,
        name=str(name) if name is not None else None,
    )
