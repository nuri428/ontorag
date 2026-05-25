from __future__ import annotations

from pathlib import Path

import pytest

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from ontorag.learn.shacl import ShaclViolation, validate

PK = Namespace("http://example.org/pokemon#")
SHAPES = Path(__file__).parent.parent / "examples" / "pokemon" / "shapes.ttl"


def _ex(label: str) -> URIRef:
    return URIRef(f"http://example.org/entity/{label}")


def _base_pokemon(g: Graph, name: str) -> URIRef:
    s = _ex(name)
    g.add((s, RDF.type, PK.Pokemon))
    g.add((s, RDFS.label, Literal(name)))
    return s


def test_clean_data_passes_shacl():
    g = Graph()
    pikachu = _base_pokemon(g, "Pikachu")
    g.add((pikachu, PK.hasType, _ex("Electric")))
    g.add((pikachu, PK.hp, Literal(35, datatype=XSD.integer)))
    g.add((pikachu, PK.nationalDex, Literal(25, datatype=XSD.integer)))

    kept, violations = validate(g, SHAPES)

    assert violations == []
    assert len(kept) == len(g)


def test_too_many_types_is_flagged():
    g = Graph()
    bug = _base_pokemon(g, "BugPokemon")
    g.add((bug, PK.hasType, _ex("Fire")))
    g.add((bug, PK.hasType, _ex("Water")))
    g.add((bug, PK.hasType, _ex("Grass")))  # over the maxCount 2

    kept, violations = validate(g, SHAPES)

    assert len(violations) >= 1
    flagged_paths = {v.result_path for v in violations}
    assert str(PK.hasType) in flagged_paths
    # All hasType triples for the violating subject should be dropped
    assert (bug, PK.hasType, _ex("Fire")) not in kept


def test_out_of_range_hp_is_flagged():
    g = Graph()
    bad = _base_pokemon(g, "OverflowMon")
    g.add((bad, PK.hp, Literal(1500, datatype=XSD.integer)))  # > 999

    kept, violations = validate(g, SHAPES)

    paths = {v.result_path for v in violations}
    assert str(PK.hp) in paths
    assert (bad, PK.hp, Literal(1500, datatype=XSD.integer)) not in kept
    # The label triple (different predicate) should survive.
    assert (bad, RDFS.label, Literal("OverflowMon")) in kept


def test_move_category_outside_enum_is_flagged():
    g = Graph()
    m = _ex("WeirdMove")
    g.add((m, RDF.type, PK.Move))
    g.add((m, PK.category, Literal("Mystic")))  # not in {Physical, Special, Status}

    kept, violations = validate(g, SHAPES)

    paths = {v.result_path for v in violations}
    assert str(PK.category) in paths


def test_violation_carries_message_and_severity():
    g = Graph()
    bad = _base_pokemon(g, "TooManyTypes")
    g.add((bad, PK.hasType, _ex("A")))
    g.add((bad, PK.hasType, _ex("B")))
    g.add((bad, PK.hasType, _ex("C")))

    _, violations = validate(g, SHAPES)

    assert any(isinstance(v, ShaclViolation) for v in violations)
    v0 = violations[0]
    assert v0.severity in {"Violation", "Warning", "Info"}
    assert v0.focus_node == str(bad)


def test_missing_shapes_file_raises(tmp_path):
    g = Graph()
    missing = tmp_path / "no_such_shapes.ttl"
    with pytest.raises(Exception):
        validate(g, missing)


# ── derive_from_owl ────────────────────────────────────────────────────────

from ontorag.learn.shacl import derive_from_owl  # noqa: E402


def _derive(tmp_path: Path, ttl_body: str) -> Graph:
    """Helper: write a schema fixture, run derive_from_owl, return parsed Graph."""
    schema = tmp_path / "schema.ttl"
    schema.write_text(
        "@prefix owl:  <http://www.w3.org/2002/07/owl#> .\n"
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .\n"
        "@prefix ex:   <http://example.org/ex#> .\n"
        + ttl_body
    )
    result_ttl = derive_from_owl(schema)
    g = Graph()
    g.parse(data=result_ttl, format="turtle")
    return g


def test_derive_datatype_property_emits_sh_datatype(tmp_path):
    out = _derive(
        tmp_path,
        """
        ex:Cls a owl:Class .
        ex:age a owl:DatatypeProperty ;
            rdfs:domain ex:Cls ;
            rdfs:range  xsd:integer .
        """,
    )
    EX = Namespace("http://example.org/ex#")
    SH = Namespace("http://www.w3.org/ns/shacl#")

    shape = URIRef(str(EX.Cls) + "Shape")
    assert (shape, SH.targetClass, EX.Cls) in out
    # exactly one property shape declared with sh:datatype xsd:integer
    paths = list(out.objects(predicate=SH.path))
    assert EX.age in paths
    datatypes = list(out.objects(predicate=SH.datatype))
    assert URIRef("http://www.w3.org/2001/XMLSchema#integer") in datatypes


def test_derive_object_property_emits_class_and_node_kind(tmp_path):
    out = _derive(
        tmp_path,
        """
        ex:A a owl:Class .
        ex:B a owl:Class .
        ex:rel a owl:ObjectProperty ;
            rdfs:domain ex:A ;
            rdfs:range  ex:B .
        """,
    )
    SH = Namespace("http://www.w3.org/ns/shacl#")
    EX = Namespace("http://example.org/ex#")

    classes = list(out.objects(predicate=SH["class"]))
    assert EX.B in classes
    node_kinds = list(out.objects(predicate=SH.nodeKind))
    assert SH.IRI in node_kinds


def test_derive_functional_property_emits_max_count_1(tmp_path):
    out = _derive(
        tmp_path,
        """
        ex:Cls a owl:Class .
        ex:uniq a owl:DatatypeProperty , owl:FunctionalProperty ;
            rdfs:domain ex:Cls ;
            rdfs:range  xsd:string .
        """,
    )
    SH = Namespace("http://www.w3.org/ns/shacl#")
    max_counts = list(out.objects(predicate=SH.maxCount))
    assert any(int(m) == 1 for m in max_counts)


def test_derive_no_domain_property_is_skipped(tmp_path):
    out = _derive(
        tmp_path,
        """
        ex:Cls a owl:Class .
        ex:orphan a owl:DatatypeProperty ;
            rdfs:range xsd:string .
        """,
    )
    SH = Namespace("http://www.w3.org/ns/shacl#")
    EX = Namespace("http://example.org/ex#")
    paths = list(out.objects(predicate=SH.path))
    assert EX.orphan not in paths


def test_derived_output_is_valid_shacl(tmp_path):
    """Derived shapes must be parseable as a SHACL graph (conforms on empty data)."""
    from pyshacl import validate as _pyshacl_validate

    ttl = derive_from_owl(Path(__file__).parent.parent / "examples" / "pokemon" / "schema.ttl")
    derived = Graph()
    derived.parse(data=ttl, format="turtle")

    conforms, _, _ = _pyshacl_validate(
        data_graph=Graph(),
        shacl_graph=derived,
        inference="none",
    )
    assert conforms is True
