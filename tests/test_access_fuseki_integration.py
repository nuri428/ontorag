"""Live Fuseki integration test for the filtered/fail-closed union-read guard.

This is the live-backend verification the unit suite in test_access.py
cannot provide: AccessControlledStore's guards are exercised there only
against a spy GraphStore, which proves the wrapper *logic* is correct
(right survivor set, right capability dispatch) but says nothing about
whether the underlying SPARQL 1.1 protocol default-graph-uri mechanism
actually excludes a denied ontology's data from a real Fuseki union query,
or whether it silently changes result multiplicity (a real risk this repo
has hit before — see the dedup fixes in its history). This file loads two
real ontologies into a live Fuseki dataset and asserts:

1. Baseline (unwrapped) union reads merge both ontologies — the leak
   surface the guard exists to close.
2. Wrapping that same live store with a policy that denies one ontology
   now returns the FILTERED union (public data only) rather than either
   leaking secret's data or hard-blocking the whole union — the actual
   roadmap SS3.2 fix, not the increment-2 fail-closed interim.
3. A parametrized safety net over every pure-SPARQL union-capable read
   method: none may return the denied ontology's data. This is the check
   that would have caught the Phase-0 "spy green, real leak" trap and the
   find_similar/Qdrant gap — testing get_schema alone was not enough
   either time.
4. Multiplicity/dedup safety: an entity whose URI appears in BOTH
   ontologies' data graphs must not be double-counted by the filtered
   union (default-graph-uri preserves RDF-merge semantics; GRAPH ?g {}
   iteration would not have).
5. search_text (Lucene-backed) and find_similar/dump_graph (Qdrant/GSP-
   backed, bypass _sparql_select entirely) must stay on the fail-closed
   path even though the wrapped store now has the filtering capability —
   routing them through restrict_default_graph would be a silent no-op
   that returns unfiltered data.
6. Scoped reads to the still-allowed ontology, and the "nothing
   restricted" case, keep working exactly as before.

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
from ontorag.stores.base import PatternQuery, PatternTriple
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
    ex:shared a ex:Widget ;
      rdfs:label "Shared Entity" .
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
    ex:g2 a ex:Gadget ;
      ex:relatesTo ex:g1 ;
      rdfs:label "Secret Gadget 2" .
    <http://example.org/public#shared> a ex:Gadget ;
      rdfs:label "Shared Entity" .
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
    """A FusekiStore with two ontologies loaded: 'public' and 'secret'.

    'ex:shared' (http://example.org/public#shared) is asserted as an
    instance of BOTH ex:Widget (in 'public') and ex:Gadget (in 'secret') —
    the multiplicity/dedup probe: a correct filtered union sees it once (as
    Widget only, since 'secret' is excluded); GRAPH ?g {} iteration would
    risk seeing it twice were secret ever included.
    """
    if not _fuseki_reachable():
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    s = FusekiStore.from_env()
    # clear_graph("all") with ontology=None only clears the legacy default
    # graphs (urn:ontorag:schema/:data) -- per-ontology graphs are separate
    # and must be cleared explicitly, or data accumulates across test runs.
    await s.clear_graph("all")
    await s.clear_graph("all", ontology="public")
    await s.clear_graph("all", ontology="secret")
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


def _wrapper(raw_store, policy_str: str) -> AccessControlledStore:
    return AccessControlledStore(raw_store, AccessPolicy.from_string(policy_str))


# ── baseline: prove the raw store actually leaks (the surface being closed) ──


@pytest.mark.asyncio
async def test_baseline_union_includes_both_ontologies_when_unwrapped(raw_store):
    schema = await raw_store.get_schema(ontology=None)
    class_uris = {c.uri for c in schema.classes}
    assert any("Widget" in u for u in class_uris)
    assert any("Gadget" in u for u in class_uris)


@pytest.mark.asyncio
async def test_baseline_scoped_read_excludes_other_ontology(raw_store):
    """Parity check: explicit ontology scoping (pre-existing, unaffected)
    already isolates 'public' from 'secret'."""
    schema = await raw_store.get_schema(ontology="public")
    class_uris = {c.uri for c in schema.classes}
    assert any("Widget" in u for u in class_uris)
    assert not any("Gadget" in u for u in class_uris)


# ── the fix: filtered union, not hard denial ─────────────────────────────────


@pytest.mark.asyncio
async def test_wrapped_union_read_filtered_not_leaked_not_denied(raw_store):
    """The actual roadmap SS3.2 fix: wrapping the SAME live store with a
    policy that denies 'secret' now returns the FILTERED union (public
    only) — succeeds, does not raise, and does not include secret's data."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    schema = await wrapper.get_schema(ontology=None)
    class_uris = {c.uri for c in schema.classes}
    assert any("Widget" in u for u in class_uris)
    assert not any("Gadget" in u for u in class_uris)


@pytest.mark.asyncio
async def test_query_pattern_stays_fail_closed_not_filtered(raw_store):
    """query_pattern (L2 DSL, previously totally unguarded) stays on the
    fail-closed path, not the filtered-union path: verified live that its
    translator hardcodes GRAPH <urn:ontorag:data> (a single legacy graph,
    never a per-ontology one), so there is nothing for restrict_default_graph
    to filter -- routing it there would be a no-op that looks like coverage
    it doesn't have. The plain can_read(None) fail-closed check is both
    correct and sufficient here."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    query = PatternQuery(
        select=["?class"], where=[PatternTriple(s="?class", p="rdf:type", o="owl:Class")]
    )
    with pytest.raises(AccessDenied):
        await wrapper.query_pattern(query)


@pytest.mark.asyncio
async def test_wrapped_union_read_filtered_via_find_entities(raw_store):
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    widgets = await wrapper.find_entities("http://example.org/public#Widget", ontology=None)
    assert any(e.uri.endswith("w1") for e in widgets)
    gadgets = await wrapper.find_entities("http://example.org/secret#Gadget", ontology=None)
    assert gadgets == []


# ── comprehensive safety net: no pure-SPARQL union method may leak ──────────


@pytest.mark.asyncio
async def test_get_schema_filtered_no_leak(raw_store):
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    schema = await wrapper.get_schema(ontology=None)
    assert "Gadget" not in repr(schema)
    assert "Widget" in repr(schema)


@pytest.mark.asyncio
async def test_get_class_detail_secret_is_empty_under_restriction(raw_store):
    """Asking about a denied ontology's own class by URI must not surface
    its instances via the filtered union path (ontology=None)."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    detail = await wrapper.get_class_detail(
        "http://example.org/secret#Gadget", ontology=None
    )
    assert detail.instance_count == 0


@pytest.mark.asyncio
async def test_describe_entity_filtered_no_leak(raw_store):
    """A denied entity must come back as genuinely not found (no bindings
    at all from the filtered union), not populated with its real data."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    with pytest.raises(KeyError):
        await wrapper.describe_entity("http://example.org/secret#g1", ontology=None)


@pytest.mark.asyncio
async def test_count_entities_filtered_no_leak(raw_store):
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    n_widgets = await wrapper.count_entities("http://example.org/public#Widget", ontology=None)
    n_gadgets = await wrapper.count_entities("http://example.org/secret#Gadget", ontology=None)
    assert n_widgets >= 1
    assert n_gadgets == 0


@pytest.mark.asyncio
async def test_traverse_filtered_no_leak(raw_store):
    """Confirmatory check for the traversal family (traverse/find_path/
    find_related/property_path_closure): grep-confirmed they share the
    same bare-pattern graph_clause(None, ...) chokepoint as the methods
    tested individually above (no hardcoded GRAPH <uri> anywhere in
    _traversal_mixin.py), so this one live test stands in for all four —
    traverse is the most complex shape (BFS with a predicate filter)."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    result = await wrapper.traverse(
        "http://example.org/secret#g2",
        predicate="http://example.org/secret#relatesTo",
        ontology=None,
    )
    # The start node itself is always echoed at depth 0 by the traversal
    # loop's seed, but no edge into the (excluded) secret graph may resolve.
    assert result.edges == []


@pytest.mark.asyncio
async def test_aggregate_filtered_no_leak(raw_store):
    from ontorag.stores.base import AggFunc

    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    result = await wrapper.aggregate(
        "http://example.org/secret#Gadget",
        "http://www.w3.org/2000/01/rdf-schema#label",
        agg=AggFunc.count,
        ontology=None,
    )
    assert "Gadget" not in repr(result)
    assert "Secret" not in repr(result)


# ── multiplicity / dedup safety (the exact regression class this repo has
#    already fixed before, for GRAPH ?g {} iteration semantics) ─────────────


@pytest.mark.asyncio
async def test_filtered_union_no_duplicate_rows_for_overlapping_uri(raw_store):
    """'ex:shared' is asserted as an instance of BOTH ex:Widget (public) and
    ex:Gadget (secret). Under a filtered union that excludes 'secret', it
    must appear exactly once (as a Widget) — default-graph-uri preserves
    RDF-merge multiplicity; GRAPH ?g {} iteration would risk it appearing
    once per matching named graph instead."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    widgets = await wrapper.find_entities("http://example.org/public#Widget", ontology=None)
    shared_hits = [e for e in widgets if e.uri.endswith("shared")]
    assert len(shared_hits) == 1


@pytest.mark.asyncio
async def test_unfiltered_union_baseline_shows_no_duplication_either(raw_store):
    """Sanity: even the raw unwrapped union (both ontologies visible) must
    not duplicate 'ex:shared' as Widget just because it's separately
    tagged Gadget in another graph -- confirms the fixture's dedup
    assumption before trusting the filtered-union dedup test above."""
    widgets = await raw_store.find_entities("http://example.org/public#Widget", ontology=None)
    shared_hits = [e for e in widgets if e.uri.endswith("shared")]
    assert len(shared_hits) == 1


# ── methods that must stay fail-closed even with the capability present ─────


@pytest.mark.asyncio
async def test_find_similar_stays_fail_closed_not_silently_unfiltered(raw_store):
    """find_similar is Qdrant-backed; it never reaches _sparql_select, so
    default-graph-uri has zero effect on it. It must raise AccessDenied,
    not silently return every ontology's vectors."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    with pytest.raises(AccessDenied):
        await wrapper.find_similar("http://example.org/public#w1", ontology=None)


@pytest.mark.asyncio
async def test_dump_graph_stays_fail_closed_not_silently_unfiltered(raw_store):
    """dump_graph uses GSP GET, not _sparql_select — same reasoning."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    with pytest.raises(AccessDenied):
        await wrapper.dump_graph("all", ontology=None)


@pytest.mark.asyncio
async def test_search_text_stays_fail_closed_conservatively(raw_store):
    """search_text DOES reach _sparql_select (text:query property function),
    but whether default-graph-uri actually excludes a denied ontology's
    Lucene hits is unverified (Lucene's index is global) -- so it is
    deliberately kept on the fail-closed path rather than assumed safe."""
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    with pytest.raises(AccessDenied):
        await wrapper.search_text("Gadget", ontology=None)


# ── unaffected behavior: explicit scopes, and the "nothing restricted" case ─


@pytest.mark.asyncio
async def test_wrapped_scoped_read_to_allowed_ontology_still_works(raw_store):
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    schema = await wrapper.get_schema(ontology="public")
    assert any("Widget" in c.uri for c in schema.classes)


@pytest.mark.asyncio
async def test_wrapped_scoped_read_to_denied_ontology_still_raises(raw_store):
    wrapper = _wrapper(raw_store, "secret:none,public:rw")
    with pytest.raises(AccessDenied):
        await wrapper.get_schema(ontology="secret")


@pytest.mark.asyncio
async def test_wrapped_union_read_still_works_when_nothing_restricted(raw_store):
    """No over-blocking: when the policy has no read-denied ontology, the
    wrapped union read still returns the real merged data — identical to
    the unwrapped baseline."""
    wrapper = _wrapper(raw_store, "public:rw")
    schema = await wrapper.get_schema(ontology=None)
    class_uris = {c.uri for c in schema.classes}
    assert any("Widget" in u for u in class_uris)
    assert any("Gadget" in u for u in class_uris)
