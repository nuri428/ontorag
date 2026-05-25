from __future__ import annotations

import pytest

from ontorag.core.ontology import (
    DEFAULT_DATA_GRAPH,
    DEFAULT_SCHEMA_GRAPH,
    data_graph_uri,
    graph_clause,
    schema_graph_uri,
    scoped_graph,
    validate_ontology_id,
)


def test_none_maps_to_legacy_default_graphs():
    assert schema_graph_uri(None) == DEFAULT_SCHEMA_GRAPH
    assert data_graph_uri(None) == DEFAULT_DATA_GRAPH


def test_named_ontology_graphs():
    assert schema_graph_uri("pokemon") == "urn:ontorag:pokemon:schema"
    assert data_graph_uri("pokemon") == "urn:ontorag:pokemon:data"


def test_validate_passthrough():
    assert validate_ontology_id(None) is None
    assert validate_ontology_id("foaf-2") == "foaf-2"
    assert validate_ontology_id("a_b") == "a_b"


@pytest.mark.parametrize(
    "bad",
    [
        "pk:Foo",            # colon
        "a b",               # space
        "x}",                # brace
        "../etc",            # path chars
        "id'; DROP",         # injection chars
        "",                  # empty
    ],
)
def test_invalid_ids_raise(bad):
    with pytest.raises(ValueError, match="Invalid ontology id"):
        validate_ontology_id(bad)


@pytest.mark.parametrize("fn", [schema_graph_uri, data_graph_uri])
def test_graph_uri_rejects_unsafe_id(fn):
    with pytest.raises(ValueError):
        fn("evil} GRAPH <x>")


# ── scoped_graph (single source of truth for the scoping decision) ────────────


def test_scoped_graph_none_is_union():
    assert scoped_graph(None, "schema") is None
    assert scoped_graph(None, "data") is None


def test_scoped_graph_named():
    assert scoped_graph("pokemon", "schema") == "urn:ontorag:pokemon:schema"
    assert scoped_graph("pokemon", "data") == "urn:ontorag:pokemon:data"


def test_scoped_graph_bad_kind_raises():
    with pytest.raises(ValueError, match="kind must be"):
        scoped_graph("pokemon", "tbox")


def test_scoped_graph_bad_id_raises():
    with pytest.raises(ValueError, match="Invalid ontology id"):
        scoped_graph("evil} GRAPH", "data")


# ── graph_clause (single source of truth for SPARQL fragment emission) ────────


def test_graph_clause_union_no_graph_keyword():
    out = graph_clause(None, "?s ?p ?o .")
    assert out == "{ ?s ?p ?o . }"
    assert "GRAPH" not in out


def test_graph_clause_scoped_wraps_in_graph():
    out = graph_clause("urn:ontorag:pokemon:data", "?s ?p ?o .")
    assert out == "GRAPH <urn:ontorag:pokemon:data> { ?s ?p ?o . }"
