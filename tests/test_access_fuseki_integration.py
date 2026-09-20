"""Live Fuseki integration test for the fail-closed union-read guard.

This is the live-backend verification the unit suite in test_access.py
cannot provide: AccessControlledStore's guards are exercised there only
against a spy GraphStore, which proves the wrapper *logic* is correct but
says nothing about what a real backend actually returns for
ontology=None (union) queries. This file loads two real ontologies into a
live Fuseki dataset and asserts:

1. Baseline (unwrapped) union reads merge both ontologies — this is the
   leak surface the wrapper's fail-closed guard exists to close.
2. Wrapping that same live store with a policy that denies one ontology
   blocks the union read outright (AccessDenied), rather than silently
   returning the merged data.
3. Scoped reads to the still-allowed ontology keep working through the
   wrapper.
4. When nothing is restricted, the wrapper does not over-block: the union
   read still returns the real merged data, unchanged from the baseline.

Skipped automatically when no Fuseki is reachable at FUSEKI_URL. Start one
with: docker compose up -d fuseki
"""

from __future__ import annotations

import os
from textwrap import dedent

import httpx
import pytest
from rdflib import Graph

from ontorag.core.access import AccessPolicy
from ontorag.stores.access_wrapper import AccessControlledStore, AccessDenied
from ontorag.stores.fuseki import FusekiStore

pytestmark = pytest.mark.integration

_FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030")

_PUBLIC_SCHEMA_TTL = dedent("""\
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix ex:  <http://example.org/public#> .
    ex:Widget a owl:Class .
""")
_PUBLIC_DATA_TTL = dedent("""\
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix ex:   <http://example.org/public#> .
    ex:w1 a ex:Widget ;
      rdfs:label "Public Widget" .
""")
_SECRET_SCHEMA_TTL = dedent("""\
    @prefix owl: <http://www.w3.org/2002/07/owl#> .
    @prefix ex:  <http://example.org/secret#> .
    ex:Gadget a owl:Class .
""")
_SECRET_DATA_TTL = dedent("""\
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix ex:   <http://example.org/secret#> .
    ex:g1 a ex:Gadget ;
      rdfs:label "Secret Gadget" .
""")


def _fuseki_reachable() -> bool:
    try:
        resp = httpx.get(f"{_FUSEKI_URL}/$/ping", timeout=2.0)
        return resp.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


def _graph(ttl: str) -> Graph:
    g = Graph()
    g.parse(data=ttl, format="turtle")
    return g


@pytest.fixture
async def raw_store():
    """A FusekiStore with two ontologies loaded: 'public' and 'secret'."""
    if not _fuseki_reachable():
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    s = FusekiStore.from_env()
    await s.clear_graph("all")
    await s.load_rdf(
        "public_schema.ttl", mode="schema", ontology="public", graph=_graph(_PUBLIC_SCHEMA_TTL)
    )
    await s.load_rdf(
        "public_data.ttl", mode="data", ontology="public", graph=_graph(_PUBLIC_DATA_TTL)
    )
    await s.load_rdf(
        "secret_schema.ttl", mode="schema", ontology="secret", graph=_graph(_SECRET_SCHEMA_TTL)
    )
    await s.load_rdf(
        "secret_data.ttl", mode="data", ontology="secret", graph=_graph(_SECRET_DATA_TTL)
    )
    yield s
    await s.aclose()


@pytest.mark.asyncio
async def test_baseline_union_includes_both_ontologies_when_unwrapped(raw_store):
    """Sanity check + documents the leak surface: an unwrapped store's union
    read (ontology=None) merges every loaded ontology via Fuseki's
    tdb2:unionDefaultGraph. This is exactly why a policy-denied ontology
    must not be reachable through it."""
    schema = await raw_store.get_schema(ontology=None)
    class_uris = {c.uri for c in schema.classes}
    assert any("Widget" in u for u in class_uris)
    assert any("Gadget" in u for u in class_uris)


@pytest.mark.asyncio
async def test_baseline_scoped_read_excludes_other_ontology(raw_store):
    """Parity check: explicit ontology scoping (pre-existing behavior,
    unaffected by this change) already isolates 'public' from 'secret'."""
    schema = await raw_store.get_schema(ontology="public")
    class_uris = {c.uri for c in schema.classes}
    assert any("Widget" in u for u in class_uris)
    assert not any("Gadget" in u for u in class_uris)


@pytest.mark.asyncio
async def test_wrapped_union_read_blocked_when_secret_denied(raw_store):
    """The fix under test: wrapping the SAME live store with a policy that
    denies 'secret' must block the union read outright — AccessDenied,
    zero query execution — rather than silently returning both ontologies'
    data (which the baseline test above shows the raw store would do)."""
    policy = AccessPolicy.from_string("secret:none")
    wrapper = AccessControlledStore(raw_store, policy)
    with pytest.raises(AccessDenied):
        await wrapper.get_schema(ontology=None)


@pytest.mark.asyncio
async def test_wrapped_union_read_blocked_via_query_pattern(raw_store):
    """query_pattern (L2 DSL, previously totally unguarded) is blocked the
    same way — it has no ontology field and would otherwise run against the
    same union-scoped dataset."""
    from ontorag.stores.base import PatternQuery, PatternTriple

    policy = AccessPolicy.from_string("secret:none")
    wrapper = AccessControlledStore(raw_store, policy)
    query = PatternQuery(
        select=["?x"], where=[PatternTriple(s="?x", p="rdf:type", o="owl:Class")]
    )
    with pytest.raises(AccessDenied):
        await wrapper.query_pattern(query)


@pytest.mark.asyncio
async def test_wrapped_scoped_read_to_allowed_ontology_still_works(raw_store):
    """Explicit reads to the still-allowed ontology keep working through the
    wrapper even while the union is blocked."""
    policy = AccessPolicy.from_string("secret:none")
    wrapper = AccessControlledStore(raw_store, policy)
    schema = await wrapper.get_schema(ontology="public")
    assert any("Widget" in c.uri for c in schema.classes)


@pytest.mark.asyncio
async def test_wrapped_union_read_denied_to_secret_scope_directly(raw_store):
    policy = AccessPolicy.from_string("secret:none")
    wrapper = AccessControlledStore(raw_store, policy)
    with pytest.raises(AccessDenied):
        await wrapper.get_schema(ontology="secret")


@pytest.mark.asyncio
async def test_wrapped_union_read_still_works_when_nothing_restricted(raw_store):
    """No over-blocking: when the policy has no read-denied ontology, the
    wrapped union read still returns the real merged data — identical to
    the unwrapped baseline, proving the guard doesn't break normal
    operation when there's nothing to protect."""
    policy = AccessPolicy.from_string("public:rw")
    wrapper = AccessControlledStore(raw_store, policy)
    schema = await wrapper.get_schema(ontology=None)
    class_uris = {c.uri for c in schema.classes}
    assert any("Widget" in u for u in class_uris)
    assert any("Gadget" in u for u in class_uris)
