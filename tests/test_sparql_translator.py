from __future__ import annotations

import pytest

from ontorag.core.sparql import _sparql_value, pattern_to_sparql
from ontorag.stores.base import PatternFilter, PatternQuery, PatternTriple


def _make_query(**kwargs) -> PatternQuery:
    defaults = dict(
        select=["?person", "?name"],
        where=[
            PatternTriple(s="?person", p="rdf:type", o="foaf:Person"),
            PatternTriple(s="?person", p="foaf:name", o="?name"),
        ],
    )
    defaults.update(kwargs)
    return PatternQuery(**defaults)


# ── PREFIX block ──────────────────────────────────────────────────────────────


def test_prefix_block_includes_used_prefixes():
    q = _make_query()
    sparql = pattern_to_sparql(q)
    assert "PREFIX rdf:" in sparql
    assert "PREFIX foaf:" in sparql


def test_prefix_block_excludes_unused_prefixes():
    q = _make_query()
    sparql = pattern_to_sparql(q)
    assert "PREFIX dcterms:" not in sparql
    assert "PREFIX skos:" not in sparql


def test_extra_prefixes_are_included():
    q = PatternQuery(
        select=["?x"],
        where=[PatternTriple(s="?x", p="rdf:type", o="ex:Widget")],
    )
    sparql = pattern_to_sparql(q, extra_prefixes={"ex": "http://example.org/"})
    assert "PREFIX ex:" in sparql
    assert "http://example.org/" in sparql


def test_uri_terms_produce_no_prefix_block():
    q = PatternQuery(
        select=["?x"],
        where=[
            PatternTriple(
                s="?x",
                p="<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>",
                o="<http://example.org/Widget>",
            )
        ],
    )
    sparql = pattern_to_sparql(q)
    assert "PREFIX" not in sparql


# ── SELECT clause ─────────────────────────────────────────────────────────────


def test_select_clause_contains_variables():
    q = _make_query()
    sparql = pattern_to_sparql(q)
    assert "SELECT ?person ?name" in sparql


def test_select_distinct():
    q = _make_query(distinct=True)
    sparql = pattern_to_sparql(q)
    assert "SELECT DISTINCT ?person ?name" in sparql


# ── WHERE clause ─────────────────────────────────────────────────────────────


def test_where_block_contains_triples():
    q = _make_query()
    sparql = pattern_to_sparql(q)
    assert "?person rdf:type foaf:Person ." in sparql
    assert "?person foaf:name ?name ." in sparql


def test_filter_clause_is_appended():
    q = _make_query(filters=[PatternFilter(var="?name", op="!=", value="")])
    sparql = pattern_to_sparql(q)
    assert "FILTER (?name !=" in sparql


# ── LIMIT / OFFSET ────────────────────────────────────────────────────────────


def test_limit_is_appended():
    q = _make_query(limit=42)
    sparql = pattern_to_sparql(q)
    assert "LIMIT 42" in sparql


def test_offset_is_appended_when_nonzero():
    q = _make_query(offset=20)
    sparql = pattern_to_sparql(q)
    assert "OFFSET 20" in sparql


def test_offset_omitted_when_zero():
    q = _make_query(offset=0)
    sparql = pattern_to_sparql(q)
    assert "OFFSET" not in sparql


# ── _sparql_value ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "true"),
        (False, "false"),
        (42, "42"),
        (3.14, "3.14"),
        ("hello", '"hello"'),
        ('"already quoted"', '"already quoted"'),
    ],
)
def test_sparql_value_formatting(value, expected):
    # Validate Pydantic coercion path (bool→int) doesn't raise
    PatternFilter(var="?x", op="=", value=value if not isinstance(value, bool) else 1)
    # bypass Pydantic int coercion for bool test
    f2 = PatternFilter.model_construct(var="?x", op="=", value=value)
    assert _sparql_value(f2) == expected
