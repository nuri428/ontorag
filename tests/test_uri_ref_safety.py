"""Regression tests for uri_ref SPARQL-injection hardening.

uri_ref previously only validated inputs containing "://", so prefixed names
(pk:Foo) and urn: inputs bypassed validation and could break out of the
surrounding SPARQL when interpolated (e.g. via a crafted class_uri).
"""

from __future__ import annotations

import pytest

from ontorag.core.sparql import uri_ref


# ── Valid inputs are preserved exactly ────────────────────────────────────────


def test_full_uri_is_wrapped():
    assert uri_ref("http://example.org/pokemon#Pokemon") == (
        "<http://example.org/pokemon#Pokemon>"
    )


def test_prefixed_name_is_unwrapped():
    # Must NOT become <pk:Pokemon> — that would be a relative-URI literal.
    assert uri_ref("pk:Pokemon") == "pk:Pokemon"


def test_variable_passes_through():
    assert uri_ref("?inst") == "?inst"


def test_already_bracketed_passes_through():
    assert uri_ref("<http://example.org/x>") == "<http://example.org/x>"


# ── Injection attempts are rejected ───────────────────────────────────────────


@pytest.mark.parametrize(
    "evil",
    [
        "pk:Foo } INJECT",                                  # prefixed-name breakout
        "urn:x:Foo} SELECT ?s WHERE {?s ?p ?o",             # urn breakout
        'http://ex.org/x"} INJECT',                         # full URI w/ quote
        "pk:Foo .} DROP GRAPH <urn:ontorag:data> ;",        # statement injection
        "http://ex.org/x{var}",                             # brace
        "pk:Foo\n} BIND",                                   # newline breakout
        "pk:Foo`bad",                                       # backtick
    ],
)
def test_injection_inputs_raise(evil):
    with pytest.raises(ValueError):
        uri_ref(evil)
