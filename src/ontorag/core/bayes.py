"""Bayesian-network vocabulary, models, and RDF (de)serialization (v0.7.1).

ontorag's probabilistic layer (Layer 2 of the 4-layer reasoning stack) stores a
*discrete* Bayesian network layered over the OWL graph. Per the v0.7 design,
the network lives **exclusively** in the ``urn:ontorag:probabilistic`` named
graph — never in the schema or data graphs.

The network is a faithful, round-trippable serialization of what pgmpy needs to
build a ``DiscreteBayesianNetwork`` (the engine wrapper lives in
``bayes/engine.py``, v0.7.3). This module is pgmpy-agnostic: it only defines the
``bn:`` vocabulary, the pydantic spec models, and the Graph ↔ spec mapping.

Serialization layout (see ``docs/design/bayesian-layer.md``):

- ordered, small structural arrays (`bn:states`, `bn:evidence`) → RDF lists
  (turtle collections) so they are order-preserving and hand-authorable;
- the dense numeric CPT (`bn:values`) → a JSON literal, because a multi-
  dimensional probability table as nested RDF lists is unworkable.

The ``values`` matrix uses pgmpy's ``TabularCPD`` layout: shape
``(variable_card, prod(evidence_card))``; columns enumerate the evidence
state-combinations with the **last** evidence variable varying fastest.
"""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
from rdflib import RDF, RDFS, XSD, BNode, Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection

from ontorag.core.ontology import validate_ontology_id

# ── Vocabulary ────────────────────────────────────────────────────────────────

#: The ``bn:`` vocabulary namespace. A stable IRI; it does not need to resolve.
BN = Namespace("https://ontorag.dev/bn#")

#: Singleton network resource node within a probabilistic graph (carries the
#: optional network name and links to its variables / CPDs).
BN_NETWORK_NODE = URIRef(BN["network"])

#: Default (ontology=None) probabilistic named-graph URI. CPTs go here only.
PROBABILISTIC_GRAPH = "urn:ontorag:probabilistic"

_FLOAT_TOL = 1e-6


def probabilistic_graph_uri(ontology: str | None) -> str:
    """Named-graph URI for the probabilistic (Bayesian) layer.

    Kept separate from :data:`ontorag.core.ontology.OntologyLayer` on purpose:
    the probabilistic graph is reasoning-stack storage (Layer 2), not part of
    the document layer model (semantic/policy/state/provenance). It reuses only
    the ontology-id validation so per-ontology scoping is consistent.

    Args:
        ontology: Validated ontology id, or None for the default graph.

    Returns:
        ``urn:ontorag:probabilistic`` (default) or
        ``urn:ontorag:{id}:probabilistic`` (scoped).
    """
    ontology = validate_ontology_id(ontology)
    if ontology is None:
        return PROBABILISTIC_GRAPH
    return f"urn:ontorag:{ontology}:probabilistic"


# ── Spec models ───────────────────────────────────────────────────────────────


class BayesVariable(BaseModel):
    """A discrete random variable (node) in the Bayesian network."""

    uri: str = Field(description="Stable identifier for the variable node.")
    states: list[str] = Field(
        min_length=1,
        description="Ordered, unique state labels. Order is significant — it "
        "indexes the rows/columns of any CPT referencing this variable.",
    )
    label: str | None = None
    represents: str | None = Field(
        default=None,
        description="Optional URI of the OWL class/property/instance this "
        "variable stands for, linking the BN to the ontology graph.",
    )

    @field_validator("states")
    @classmethod
    def _states_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError(f"Variable states must be unique, got {v!r}.")
        if any(not s for s in v):
            raise ValueError("Variable state labels must be non-empty.")
        return v

    @property
    def cardinality(self) -> int:
        return len(self.states)


class CPD(BaseModel):
    """A conditional probability distribution for one variable.

    ``values`` is the dense table in pgmpy ``TabularCPD`` layout: shape
    ``(card(variable), prod(card(e) for e in evidence))``. An empty ``evidence``
    means a prior (single column).
    """

    variable: str = Field(description="URI of the variable this CPD is for.")
    evidence: list[str] = Field(
        default_factory=list,
        description="Ordered URIs of parent (conditioning) variables. Empty = prior.",
    )
    values: list[list[float]] = Field(
        description="2D probability table; rows = variable states, columns = "
        "evidence state-combinations (last evidence var varies fastest)."
    )

    @field_validator("values")
    @classmethod
    def _values_rectangular(cls, v: list[list[float]]) -> list[list[float]]:
        if not v or not v[0]:
            raise ValueError("CPD values must be a non-empty 2D array.")
        width = len(v[0])
        if any(len(row) != width for row in v):
            raise ValueError("CPD value rows must all have the same length.")
        for row in v:
            for p in row:
                if not (0.0 - _FLOAT_TOL <= p <= 1.0 + _FLOAT_TOL):
                    raise ValueError(f"CPD probability out of [0,1]: {p!r}.")
        return v


class BayesNetwork(BaseModel):
    """A complete discrete Bayesian network (structure + CPTs)."""

    variables: list[BayesVariable] = Field(min_length=1)
    cpds: list[CPD] = Field(default_factory=list)
    name: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> BayesNetwork:
        by_uri: dict[str, BayesVariable] = {}
        for var in self.variables:
            if var.uri in by_uri:
                raise ValueError(f"Duplicate variable uri: {var.uri!r}.")
            by_uri[var.uri] = var

        seen_cpd_vars: set[str] = set()
        for cpd in self.cpds:
            if cpd.variable not in by_uri:
                raise ValueError(
                    f"CPD references unknown variable {cpd.variable!r}."
                )
            if cpd.variable in seen_cpd_vars:
                raise ValueError(
                    f"More than one CPD for variable {cpd.variable!r}."
                )
            seen_cpd_vars.add(cpd.variable)

            expected_rows = by_uri[cpd.variable].cardinality
            expected_cols = 1
            for ev in cpd.evidence:
                if ev not in by_uri:
                    raise ValueError(
                        f"CPD for {cpd.variable!r} references unknown evidence "
                        f"variable {ev!r}."
                    )
                expected_cols *= by_uri[ev].cardinality
            if len(cpd.values) != expected_rows:
                raise ValueError(
                    f"CPD for {cpd.variable!r}: expected {expected_rows} rows "
                    f"(variable cardinality), got {len(cpd.values)}."
                )
            if len(cpd.values[0]) != expected_cols:
                raise ValueError(
                    f"CPD for {cpd.variable!r}: expected {expected_cols} columns "
                    f"(product of evidence cardinalities), got {len(cpd.values[0])}."
                )
            # Each column is a distribution over the variable's states → sums to 1.
            for col in range(expected_cols):
                total = math.fsum(cpd.values[row][col] for row in range(expected_rows))
                if abs(total - 1.0) > 1e-4:
                    raise ValueError(
                        f"CPD for {cpd.variable!r}: column {col} sums to {total:.4f}, "
                        "expected 1.0 (each column is a conditional distribution)."
                    )
        return self

    def variable(self, uri: str) -> BayesVariable | None:
        """Return the variable with this URI, or None."""
        return next((v for v in self.variables if v.uri == uri), None)


# ── RDF serialization ───────────────────────────────────────────────────────


def network_to_graph(net: BayesNetwork) -> Graph:
    """Serialize a BayesNetwork to an rdflib Graph of ``bn:`` triples."""
    g = Graph()
    g.bind("bn", BN)
    g.bind("rdfs", RDFS)

    g.add((BN_NETWORK_NODE, RDF.type, BN.Network))
    if net.name:
        g.add((BN_NETWORK_NODE, RDFS.label, Literal(net.name)))

    for var in net.variables:
        vu = URIRef(var.uri)
        g.add((vu, RDF.type, BN.Variable))
        g.add((BN_NETWORK_NODE, BN.hasVariable, vu))
        if var.label:
            g.add((vu, RDFS.label, Literal(var.label)))
        if var.represents:
            g.add((vu, BN.represents, URIRef(var.represents)))
        states_node = BNode()
        Collection(g, states_node, [Literal(s) for s in var.states])
        g.add((vu, BN.states, states_node))

    for cpd in net.cpds:
        cu = BNode()
        g.add((cu, RDF.type, BN.CPD))
        g.add((BN_NETWORK_NODE, BN.hasCPD, cu))
        g.add((cu, BN.forVariable, URIRef(cpd.variable)))
        if cpd.evidence:
            ev_node = BNode()
            Collection(g, ev_node, [URIRef(e) for e in cpd.evidence])
            g.add((cu, BN.evidence, ev_node))
        g.add((cu, BN.values, Literal(json.dumps(cpd.values), datatype=XSD.string)))

    return g


def graph_to_network(g: Graph) -> BayesNetwork | None:
    """Reconstruct a BayesNetwork from a graph of ``bn:`` triples.

    Returns None when the graph holds no ``bn:Variable`` (e.g. an empty
    probabilistic graph). Variables and CPDs are returned sorted by URI for
    a deterministic round-trip.
    """
    variables: list[BayesVariable] = []
    for vu in g.subjects(RDF.type, BN.Variable):
        states_node = g.value(vu, BN.states)
        states = (
            [str(s) for s in Collection(g, states_node)] if states_node else []
        )
        label = g.value(vu, RDFS.label)
        represents = g.value(vu, BN.represents)
        variables.append(
            BayesVariable(
                uri=str(vu),
                states=states,
                label=str(label) if label is not None else None,
                represents=str(represents) if represents is not None else None,
            )
        )

    if not variables:
        return None

    cpds: list[CPD] = []
    for cu in g.subjects(RDF.type, BN.CPD):
        var = g.value(cu, BN.forVariable)
        if var is None:
            continue
        ev_node = g.value(cu, BN.evidence)
        evidence = [str(e) for e in Collection(g, ev_node)] if ev_node else []
        vals_lit = g.value(cu, BN.values)
        values: Any = json.loads(str(vals_lit)) if vals_lit is not None else []
        cpds.append(CPD(variable=str(var), evidence=evidence, values=values))

    name_node = g.value(BN_NETWORK_NODE, RDFS.label)
    variables.sort(key=lambda v: v.uri)
    cpds.sort(key=lambda c: c.variable)
    return BayesNetwork(
        variables=variables,
        cpds=cpds,
        name=str(name_node) if name_node is not None else None,
    )


# ── Structure-only spec (for CPT learning, v0.7.4) ──────────────────────────


class StructureSpec(BaseModel):
    """A Bayesian-network DAG *without* CPTs — the input to CPT learning.

    Edges are ``(parent_uri, child_uri)``. Each variable carries the OWL
    property it ``represents``, so the learner can pull observations from the
    ABox. Authored in turtle with ``bn:dependsOn`` (child → parent); the CPTs
    are estimated from data, not declared.
    """

    variables: list[BayesVariable] = Field(min_length=1)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    name: str | None = None

    @model_validator(mode="after")
    def _check_edges(self) -> StructureSpec:
        uris = {v.uri for v in self.variables}
        for parent, child in self.edges:
            if parent not in uris:
                raise ValueError(f"Edge parent {parent!r} is not a declared variable.")
            if child not in uris:
                raise ValueError(f"Edge child {child!r} is not a declared variable.")
        return self

    def parents_of(self, uri: str) -> list[str]:
        """Parent variable URIs of ``uri`` in declaration order of the edges."""
        return [p for p, c in self.edges if c == uri]


def structure_to_graph(spec: StructureSpec) -> Graph:
    """Serialize a StructureSpec to ``bn:`` triples (variables + dependsOn edges)."""
    g = Graph()
    g.bind("bn", BN)
    g.bind("rdfs", RDFS)

    g.add((BN_NETWORK_NODE, RDF.type, BN.Network))
    if spec.name:
        g.add((BN_NETWORK_NODE, RDFS.label, Literal(spec.name)))

    for var in spec.variables:
        vu = URIRef(var.uri)
        g.add((vu, RDF.type, BN.Variable))
        g.add((BN_NETWORK_NODE, BN.hasVariable, vu))
        if var.label:
            g.add((vu, RDFS.label, Literal(var.label)))
        if var.represents:
            g.add((vu, BN.represents, URIRef(var.represents)))
        states_node = BNode()
        Collection(g, states_node, [Literal(s) for s in var.states])
        g.add((vu, BN.states, states_node))

    for parent, child in spec.edges:
        g.add((URIRef(child), BN.dependsOn, URIRef(parent)))

    return g


def graph_to_structure(g: Graph) -> StructureSpec | None:
    """Reconstruct a StructureSpec from ``bn:`` triples.

    Variables come from ``bn:Variable`` nodes; edges from ``child bn:dependsOn
    parent`` triples. Returns None when the graph holds no variables.
    """
    variables: list[BayesVariable] = []
    for vu in g.subjects(RDF.type, BN.Variable):
        states_node = g.value(vu, BN.states)
        states = [str(s) for s in Collection(g, states_node)] if states_node else []
        label = g.value(vu, RDFS.label)
        represents = g.value(vu, BN.represents)
        variables.append(
            BayesVariable(
                uri=str(vu),
                states=states,
                label=str(label) if label is not None else None,
                represents=str(represents) if represents is not None else None,
            )
        )
    if not variables:
        return None

    edges: list[tuple[str, str]] = []
    for child, parent in g.subject_objects(BN.dependsOn):
        edges.append((str(parent), str(child)))

    variables.sort(key=lambda v: v.uri)
    edges.sort()
    name_node = g.value(BN_NETWORK_NODE, RDFS.label)
    return StructureSpec(
        variables=variables,
        edges=edges,
        name=str(name_node) if name_node is not None else None,
    )
