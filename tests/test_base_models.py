from __future__ import annotations

import pytest
from pydantic import ValidationError

from ontorag.stores.base import (
    EntityFilter,
    FilterOp,
    PatternFilter,
    PatternQuery,
    PatternTriple,
)


# ── PatternTriple validation ──────────────────────────────────────────────────

def test_pattern_triple_valid_variable():
    t = PatternTriple(s="?person", p="rdf:type", o="foaf:Person")
    assert t.s == "?person"


def test_pattern_triple_valid_uri():
    t = PatternTriple(s="<http://ex.org/a>", p="<http://ex.org/p>", o="<http://ex.org/b>")
    assert t.s == "<http://ex.org/a>"


def test_pattern_triple_valid_literal():
    t = PatternTriple(s="?x", p="foaf:name", o='"Alice"')
    assert t.o == '"Alice"'


def test_pattern_triple_rejects_injection():
    with pytest.raises(ValidationError):
        PatternTriple(s="?x} UNION {?y", p="rdf:type", o="ex:Class")


def test_pattern_triple_rejects_invalid_variable():
    with pytest.raises(ValidationError):
        PatternTriple(s="?123invalid", p="rdf:type", o="ex:Class")


# ── PatternFilter validation ──────────────────────────────────────────────────

def test_pattern_filter_valid():
    f = PatternFilter(var="?year", op=">=", value=2020)
    assert f.var == "?year"
    assert f.value == 2020


def test_pattern_filter_rejects_bad_var():
    with pytest.raises(ValidationError):
        PatternFilter(var="year", op="=", value="x")  # missing ?


# ── PatternQuery validation ───────────────────────────────────────────────────

def test_pattern_query_valid():
    q = PatternQuery(
        select=["?person", "?name"],
        where=[
            PatternTriple(s="?person", p="rdf:type", o="foaf:Person"),
            PatternTriple(s="?person", p="foaf:name", o="?name"),
        ],
        limit=50,
    )
    assert len(q.where) == 2
    assert q.limit == 50


def test_pattern_query_enforces_limit_max():
    with pytest.raises(ValidationError):
        PatternQuery(
            select=["?x"],
            where=[PatternTriple(s="?x", p="rdf:type", o="ex:A")],
            limit=99_999,
        )


def test_pattern_query_requires_at_least_one_where():
    with pytest.raises(ValidationError):
        PatternQuery(select=["?x"], where=[])


def test_pattern_query_rejects_bad_select_var():
    with pytest.raises(ValidationError):
        PatternQuery(
            select=["person"],  # missing ?
            where=[PatternTriple(s="?x", p="rdf:type", o="ex:A")],
        )


# ── EntityFilter ──────────────────────────────────────────────────────────────

def test_entity_filter_defaults_to_eq():
    f = EntityFilter(property="foaf:age", value=30)
    assert f.op == FilterOp.eq


def test_entity_filter_with_op():
    f = EntityFilter(property="foaf:age", op=FilterOp.gte, value=18)
    assert f.op == FilterOp.gte
