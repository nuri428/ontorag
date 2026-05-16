from __future__ import annotations

import pytest

from ontorag.core.loader import detect_mode, parse_rdf

_SCHEMA_TTL = """\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

foaf:Person a owl:Class .
foaf:name   a owl:DatatypeProperty ;
    rdfs:domain foaf:Person .
"""

_DATA_TTL = """\
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix ex:   <http://example.org/> .

ex:alice a foaf:Person ;
    foaf:name "Alice" .
ex:bob a foaf:Person ;
    foaf:name "Bob" .
"""


@pytest.fixture
def schema_file(tmp_path):
    f = tmp_path / "schema.ttl"
    f.write_text(_SCHEMA_TTL)
    return str(f)


@pytest.fixture
def data_file(tmp_path):
    f = tmp_path / "data.ttl"
    f.write_text(_DATA_TTL)
    return str(f)


def test_parse_rdf_returns_non_empty_graph(schema_file):
    graph = parse_rdf(schema_file)
    assert len(graph) > 0


def test_parse_rdf_counts_correct_triples(data_file):
    graph = parse_rdf(data_file)
    # ex:alice + ex:bob — each has rdf:type + foaf:name = 4 triples
    assert len(graph) == 4


def test_parse_rdf_raises_for_missing_file():
    with pytest.raises(FileNotFoundError):
        parse_rdf("/nonexistent/file.ttl")


def test_detect_mode_schema(schema_file):
    from ontorag.core.loader import parse_rdf
    graph = parse_rdf(schema_file)
    assert detect_mode(graph) == "schema"


def test_detect_mode_data(data_file):
    from ontorag.core.loader import parse_rdf
    graph = parse_rdf(data_file)
    assert detect_mode(graph) == "data"


def test_detect_mode_owl_ontology_declaration(tmp_path):
    ontology_ttl = """\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix ex:  <http://example.org/> .
ex:MyOntology a owl:Ontology .
"""
    f = tmp_path / "onto.ttl"
    f.write_text(ontology_ttl)
    graph = parse_rdf(str(f))
    assert detect_mode(graph) == "schema"


def test_detect_mode_subclass_predicate(tmp_path):
    ttl = """\
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex:   <http://example.org/> .
ex:Dog rdfs:subClassOf ex:Animal .
"""
    f = tmp_path / "sub.ttl"
    f.write_text(ttl)
    graph = parse_rdf(str(f))
    assert detect_mode(graph) == "schema"
