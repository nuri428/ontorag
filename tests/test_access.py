"""Tests for per-ontology access control.

Covers:
* Policy parsing (formats, write-implies-read, unlisted=open, none denies
  both, malformed→ValueError, invalid id→ValueError, deny_by_default).
* Wrapper enforcement against a spy GraphStore (denied write raises
  AccessDenied; allowed write delegates; denied scoped read raises;
  ontology=None read follows the 'default' policy entry; unknown method
  delegates via __getattr__; ontology-scoped capability methods are guarded;
  unsupported capability still raises AttributeError, preserving the
  route-level 501 pattern).
* query_pattern guarded as an ontology=None read.
* Audit logging (allow/deny) for guarded decisions.
* Factory wiring (ONTOLOGY_ACCESS set → AccessControlledStore;
  env unset → raw store).
"""

from __future__ import annotations

import logging

import pytest

from ontorag.core.access import AccessPolicy, Permission
from ontorag.stores.access_wrapper import AccessControlledStore, AccessDenied
from ontorag.stores.base import (
    AggFunc,
    LoadResult,
    PatternQuery,
    PatternTriple,
    SchemaResult,
    TraversalDirection,
)

# ── helpers ────────────────────────────────────────────────────────────────────

_NO_CLASSES = SchemaResult(
    total_classes=0, total_properties=0, namespaces={}, classes=[]
)

_A_PATTERN_QUERY = PatternQuery(
    select=["?x"], where=[PatternTriple(s="?x", p="rdf:type", o="ex:Thing")]
)


class _SpyStore:
    """Minimal GraphStore spy — records calls and returns sensible stubs.

    Only the methods exercised in the test suite are wired; everything else is
    accessible via normal attribute lookup so ``__getattr__`` on the wrapper
    passes through.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        # Extra attribute to test __getattr__ pass-through.
        self.capability_attr = "present"

    def _record(self, method: str, **kwargs: object) -> None:
        self.calls.append((method, kwargs))

    async def load_rdf(self, path, mode="auto", replace=False, ontology=None, graph=None):
        self._record("load_rdf", path=path, ontology=ontology, graph=graph)
        return LoadResult(triples_loaded=1, source=path, mode="data", ontology=ontology)

    async def clear_graph(self, target, ontology=None):
        self._record("clear_graph", target=target, ontology=ontology)
        return {}

    async def get_schema(self, ontology=None):
        self._record("get_schema", ontology=ontology)
        return _NO_CLASSES

    async def get_class_detail(self, class_uri, ontology=None):
        self._record("get_class_detail", class_uri=class_uri, ontology=ontology)
        return None  # good enough for delegation tests

    async def find_entities(self, class_uri, filters=None, limit=100, ontology=None):
        self._record("find_entities", class_uri=class_uri, ontology=ontology)
        return []

    async def describe_entity(self, uri, predicates=None, ontology=None):
        self._record("describe_entity", uri=uri, ontology=ontology)
        return None

    async def count_entities(self, class_uri, filters=None, ontology=None):
        self._record("count_entities", class_uri=class_uri, ontology=ontology)
        return 0

    async def aggregate(self, class_uri, group_by, agg=AggFunc.count, ontology=None):
        self._record("aggregate", class_uri=class_uri, ontology=ontology)
        return []

    async def traverse(
        self,
        start_uri,
        predicate=None,
        max_depth=2,
        direction=TraversalDirection.outgoing,
        ontology=None,
    ):
        self._record("traverse", start_uri=start_uri, ontology=ontology)
        return None

    async def find_path(self, uri_a, uri_b, max_depth=4, ontology=None):
        self._record("find_path", uri_a=uri_a, uri_b=uri_b, ontology=ontology)
        return None

    async def find_related(
        self,
        class_uri_a,
        predicate,
        class_uri_b,
        filters_a=None,
        filters_b=None,
        limit=100,
        ontology=None,
    ):
        self._record("find_related", ontology=ontology)
        return []

    async def query_pattern(self, query):
        self._record("query_pattern")
        return None

    async def property_path_closure(
        self,
        predicate_uri,
        start_uri=None,
        start_label=None,
        start_class_uri=None,
        limit=100,
        ontology=None,
    ):
        self._record("property_path_closure", predicate_uri=predicate_uri, ontology=ontology)
        return []

    async def dump_graph(self, target, fmt="ttl", ontology=None):
        self._record("dump_graph", target=target, ontology=ontology)
        return b""

    async def status(self):
        self._record("status")
        return None

    async def aclose(self):
        self._record("aclose")

    async def search_text(self, query, class_uri=None, limit=20, ontology=None):
        """Capability method — guarded via __getattr__ (ontology=)."""
        self._record("search_text", query=query, ontology=ontology)
        return []

    async def find_similar(self, uri, top_k=10, mode="structural", class_uri=None, ontology=None):
        self._record("find_similar", uri=uri, ontology=ontology)
        return []

    async def sameas_closure(self, uri, ontology=None):
        self._record("sameas_closure", uri=uri, ontology=ontology)
        return []

    async def get_bayes_network(self, ontology=None):
        self._record("get_bayes_network", ontology=ontology)
        return None

    async def get_causal_model(self, ontology=None):
        self._record("get_causal_model", ontology=ontology)
        return None

    async def build_embeddings(self, mode="both", embedding_provider=None, ontology=None):
        self._record("build_embeddings", ontology=ontology)
        return {}

    async def put_bayes_network(self, network, ontology=None):
        self._record("put_bayes_network", ontology=ontology)
        return 0

    async def clear_bayes_network(self, ontology=None):
        self._record("clear_bayes_network", ontology=ontology)
        return 0

    async def put_causal_model(self, model, ontology=None):
        self._record("put_causal_model", ontology=ontology)
        return 0

    async def clear_causal_model(self, ontology=None):
        self._record("clear_causal_model", ontology=ontology)
        return 0


class _PartialSpyStore(_SpyStore):
    """Spy missing a guarded capability, to verify the 501-detection pattern.

    ``find_similar`` is shadowed to ``None`` — exactly how an unsupported
    backend looks to ``getattr(store, "find_similar", None)`` (the pattern
    routes use to return HTTP 501). The guard's ``callable(attr)`` check
    must see this and skip wrapping, so the wrapper also exposes ``None``.
    """

    find_similar = None  # type: ignore[assignment]


# ── policy parsing ─────────────────────────────────────────────────────────────


class TestPolicyParsing:
    def test_write_token_rw(self):
        p = AccessPolicy.from_string("poke:rw")
        assert p.can_read("poke")
        assert p.can_write("poke")

    def test_write_token_w(self):
        p = AccessPolicy.from_string("poke:w")
        assert p.can_read("poke")
        assert p.can_write("poke")

    def test_read_token_r(self):
        p = AccessPolicy.from_string("shop:r")
        assert p.can_read("shop")
        assert not p.can_write("shop")

    def test_read_token_ro(self):
        p = AccessPolicy.from_string("shop:ro")
        assert p.can_read("shop")
        assert not p.can_write("shop")

    def test_none_token_none(self):
        p = AccessPolicy.from_string("secret:none")
        assert not p.can_read("secret")
        assert not p.can_write("secret")

    def test_none_token_dash(self):
        p = AccessPolicy.from_string("secret:-")
        assert not p.can_read("secret")
        assert not p.can_write("secret")

    def test_write_implies_read(self):
        """Permission.write must grant can_read too."""
        p = AccessPolicy.from_string("x:rw")
        assert p._permission_for("x") is Permission.write
        assert p.can_read("x")

    def test_unlisted_ontology_is_open(self):
        """Ontology not in the policy must default to full read+write."""
        p = AccessPolicy.from_string("poke:r")
        assert p.can_read("unlisted")
        assert p.can_write("unlisted")

    def test_none_ontology_defaults_open(self):
        """ontology=None (legacy default graph) is open unless listed as 'default'."""
        p = AccessPolicy.from_string("poke:r")
        assert p.can_read(None)
        assert p.can_write(None)

    def test_default_key_restricts_none_ontology(self):
        """'default:r' should restrict the ontology=None scope."""
        p = AccessPolicy.from_string("default:r")
        assert p.can_read(None)
        assert not p.can_write(None)

    def test_default_key_none_on_none(self):
        """'default:none' should deny all access for ontology=None."""
        p = AccessPolicy.from_string("default:none")
        assert not p.can_read(None)
        assert not p.can_write(None)

    def test_multi_entry_parsing(self):
        p = AccessPolicy.from_string("poke:rw,shop:r,secret:none")
        assert p.can_write("poke")
        assert p.can_read("shop") and not p.can_write("shop")
        assert not p.can_read("secret")

    def test_whitespace_tolerance(self):
        """Leading/trailing whitespace around entries and tokens is stripped."""
        p = AccessPolicy.from_string("  poke : rw ,  shop : r  ")
        assert p.can_write("poke")
        assert p.can_read("shop") and not p.can_write("shop")

    def test_empty_entries_ignored(self):
        """Trailing commas and empty entries must not raise."""
        p = AccessPolicy.from_string("poke:rw,")
        assert p.can_write("poke")

    def test_malformed_no_colon_raises(self):
        with pytest.raises(ValueError, match="Malformed"):
            AccessPolicy.from_string("poke-rw")

    def test_unknown_perm_token_raises(self):
        with pytest.raises(ValueError, match="Unknown permission token"):
            AccessPolicy.from_string("poke:admin")

    def test_invalid_ontology_id_raises(self):
        """An id that fails validate_ontology_id must raise ValueError."""
        with pytest.raises(ValueError):
            AccessPolicy.from_string("poke/bad:rw")

    def test_from_env_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("ONTOLOGY_ACCESS", raising=False)
        assert AccessPolicy.from_env() is None

    def test_from_env_none_when_empty(self, monkeypatch):
        monkeypatch.setenv("ONTOLOGY_ACCESS", "  ")
        assert AccessPolicy.from_env() is None

    def test_from_env_parses_correctly(self, monkeypatch):
        monkeypatch.setenv("ONTOLOGY_ACCESS", "poke:r")
        p = AccessPolicy.from_env()
        assert p is not None
        assert p.can_read("poke")
        assert not p.can_write("poke")

    # --- deny_by_default --------------------------------------------------------

    def test_deny_by_default_off_unlisted_is_open(self):
        """deny_by_default defaults to False — unchanged open-by-default behaviour."""
        p = AccessPolicy.from_string("poke:r")
        assert p.can_read("unlisted")
        assert p.can_write("unlisted")

    def test_deny_by_default_on_unlisted_denied(self):
        p = AccessPolicy.from_string("poke:r", deny_by_default=True)
        assert not p.can_read("unlisted")
        assert not p.can_write("unlisted")
        # Explicitly listed ontologies are unaffected.
        assert p.can_read("poke")
        assert not p.can_write("poke")

    def test_deny_by_default_on_none_ontology_denied(self):
        """deny_by_default also flips the fallback for ontology=None."""
        p = AccessPolicy.from_string("poke:r", deny_by_default=True)
        assert not p.can_read(None)
        assert not p.can_write(None)

    def test_deny_by_default_explicit_default_overrides(self):
        """An explicit 'default:' entry still wins over deny_by_default."""
        p = AccessPolicy.from_string("default:rw", deny_by_default=True)
        assert p.can_read(None)
        assert p.can_write(None)

    def test_from_env_deny_by_default_alone_builds_policy(self, monkeypatch):
        """ONTOLOGY_ACCESS unset but the deny flag set must still build a policy."""
        monkeypatch.delenv("ONTOLOGY_ACCESS", raising=False)
        monkeypatch.setenv("ONTOLOGY_ACCESS_DENY_BY_DEFAULT", "true")
        p = AccessPolicy.from_env()
        assert p is not None
        assert not p.can_read("anything")
        assert not p.can_write("anything")

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_from_env_deny_by_default_truthy_values(self, monkeypatch, value):
        monkeypatch.delenv("ONTOLOGY_ACCESS", raising=False)
        monkeypatch.setenv("ONTOLOGY_ACCESS_DENY_BY_DEFAULT", value)
        p = AccessPolicy.from_env()
        assert p is not None
        assert not p.can_write("anything")

    def test_from_env_deny_by_default_false_by_default(self, monkeypatch):
        monkeypatch.delenv("ONTOLOGY_ACCESS", raising=False)
        monkeypatch.delenv("ONTOLOGY_ACCESS_DENY_BY_DEFAULT", raising=False)
        assert AccessPolicy.from_env() is None

    # --- has_read_restricted_ontology (fail-closed union guard input) ----------

    def test_has_read_restricted_ontology_false_when_empty(self):
        assert not AccessPolicy.from_string("").has_read_restricted_ontology()

    def test_has_read_restricted_ontology_false_for_rw_and_r_only(self):
        """Read-only entries are not a confidentiality restriction."""
        p = AccessPolicy.from_string("poke:rw,shop:r")
        assert not p.has_read_restricted_ontology()

    def test_has_read_restricted_ontology_true_for_any_none_entry(self):
        p = AccessPolicy.from_string("poke:rw,secret:none")
        assert p.has_read_restricted_ontology()

    def test_has_read_restricted_ontology_ignores_default_key(self):
        """'default:none' governs ontology=None itself (via can_read), not
        the fail-closed guard, so it must not count here."""
        p = AccessPolicy.from_string("default:none")
        assert not p.has_read_restricted_ontology()

    def test_has_read_restricted_ontology_true_with_default_key_present_too(self):
        p = AccessPolicy.from_string("default:rw,secret:none")
        assert p.has_read_restricted_ontology()


# ── wrapper enforcement ────────────────────────────────────────────────────────


class TestAccessControlledStore:
    """Wrapper enforcement against a spy GraphStore."""

    def _make(self, policy_str: str) -> tuple[AccessControlledStore, _SpyStore]:
        spy = _SpyStore()
        policy = AccessPolicy.from_string(policy_str)
        return AccessControlledStore(spy, policy), spy

    # --- write denied ---------------------------------------------------------

    async def test_load_rdf_denied_raises(self):
        wrapper, spy = self._make("poke:r")
        with pytest.raises(AccessDenied):
            await wrapper.load_rdf("/tmp/f.ttl", ontology="poke")
        assert not spy.calls  # delegate was NOT reached

    async def test_clear_graph_denied_raises(self):
        wrapper, spy = self._make("poke:none")
        with pytest.raises(AccessDenied):
            await wrapper.clear_graph("all", ontology="poke")
        assert not spy.calls

    # --- write allowed --------------------------------------------------------

    async def test_load_rdf_allowed_delegates(self):
        wrapper, spy = self._make("poke:rw")
        await wrapper.load_rdf("/tmp/f.ttl", ontology="poke")
        assert len(spy.calls) == 1
        assert spy.calls[0][0] == "load_rdf"

    async def test_clear_graph_allowed_delegates(self):
        wrapper, spy = self._make("poke:rw")
        await wrapper.clear_graph("data", ontology="poke")
        assert spy.calls[0][0] == "clear_graph"

    # --- scoped read denied ---------------------------------------------------

    async def test_get_schema_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.get_schema(ontology="secret")
        assert not spy.calls

    async def test_find_entities_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.find_entities("ex:Foo", ontology="secret")
        assert not spy.calls

    async def test_describe_entity_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.describe_entity("ex:bar", ontology="secret")
        assert not spy.calls

    async def test_count_entities_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.count_entities("ex:Foo", ontology="secret")

    async def test_aggregate_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.aggregate("ex:Foo", "ex:prop", ontology="secret")

    async def test_traverse_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.traverse("ex:bar", ontology="secret")

    async def test_find_path_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.find_path("ex:a", "ex:b", ontology="secret")

    async def test_find_related_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.find_related("ex:A", "ex:rel", "ex:B", ontology="secret")

    async def test_property_path_closure_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.property_path_closure("ex:pred", ontology="secret")

    async def test_dump_graph_denied_raises(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.dump_graph("all", ontology="secret")

    # --- ontology=None read: open only when NOTHING is read-restricted --------

    async def test_get_schema_none_ontology_open_when_no_restrictions(self):
        """ontology=None stays open when nothing in the policy denies read."""
        wrapper, spy = self._make("poke:rw,shop:r")
        await wrapper.get_schema(ontology=None)
        assert spy.calls[0][0] == "get_schema"

    async def test_find_entities_none_ontology_open_when_no_restrictions(self):
        wrapper, spy = self._make("poke:rw,shop:r")
        await wrapper.find_entities("ex:Foo", ontology=None)
        assert spy.calls[0][0] == "find_entities"

    # --- fail-closed union guard: any denied ontology blocks union reads ------

    async def test_get_schema_none_ontology_denied_by_unrelated_none_entry(self):
        """Regression: 'secret:none' must ALSO block union reads.

        Previously ``_require_read`` special-cased ``ontology is not None``
        and never evaluated ``can_read(None)`` at all, so a per-ontology
        'none' entry had zero effect on union-scoped reads — get_schema(),
        find_entities(), search_text(), query_pattern(), etc. with
        ontology=None (or omitted) would silently return 'secret' data too.
        Since there is no filtered-union implementation yet (see
        AccessPolicy.has_read_restricted_ontology), the union read is now
        blocked outright rather than silently leaking it.
        """
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.get_schema(ontology=None)
        assert not spy.calls

    async def test_find_entities_none_ontology_denied_by_unrelated_none_entry(self):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.find_entities("ex:Foo", ontology=None)
        assert not spy.calls

    async def test_none_ontology_denied_even_with_explicit_default_rw(self):
        """The fail-closed guard fires even when 'default:' is explicitly open.

        An operator who explicitly reopens the union scope ('default:rw')
        still cannot see a ontology explicitly marked 'none' through it.
        """
        wrapper, spy = self._make("default:rw,secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.get_schema(ontology=None)
        assert not spy.calls

    async def test_read_only_ontology_does_not_trigger_fail_closed_guard(self):
        """A read-only ('r') entry is not a confidentiality restriction —
        it must not block union reads (only 'none' entries do)."""
        wrapper, spy = self._make("shop:r")
        await wrapper.get_schema(ontology=None)
        assert spy.calls[0][0] == "get_schema"

    # --- regression: union read must respect an explicit 'default:' policy ----

    async def test_get_schema_none_ontology_denied_by_explicit_default_none(self):
        """Regression: 'default:none' must block union reads too.

        Previously ``_require_read`` special-cased ``ontology is not None``
        and never evaluated ``can_read(None)`` at all, so this policy was
        silently ignored for every union-scoped read method.
        """
        wrapper, spy = self._make("default:none")
        with pytest.raises(AccessDenied):
            await wrapper.get_schema(ontology=None)
        assert not spy.calls

    async def test_find_entities_none_ontology_denied_by_explicit_default_none(self):
        wrapper, spy = self._make("default:none")
        with pytest.raises(AccessDenied):
            await wrapper.find_entities("ex:Foo", ontology=None)
        assert not spy.calls

    async def test_get_schema_none_ontology_allowed_by_explicit_default_rw(self):
        """An explicit 'default:rw' (or 'default:r') keeps union reads allowed."""
        wrapper, spy = self._make("default:rw")
        await wrapper.get_schema(ontology=None)
        assert spy.calls[0][0] == "get_schema"

    # --- scoped read allowed --------------------------------------------------

    async def test_get_schema_allowed_delegates(self):
        wrapper, spy = self._make("poke:r")
        await wrapper.get_schema(ontology="poke")
        assert spy.calls[0][0] == "get_schema"

    async def test_get_class_detail_allowed_delegates(self):
        wrapper, spy = self._make("poke:r")
        await wrapper.get_class_detail("ex:Foo", ontology="poke")
        assert spy.calls[0][0] == "get_class_detail"

    # --- __getattr__ delegation (unguarded attributes) -------------------------

    async def test_search_text_no_ontology_delegates_via_getattr(self):
        """search_text is guarded via __getattr__, and with no ontology=
        kwarg it targets the union scope (ontology=None). With nothing
        read-restricted, it delegates exactly like an unguarded pass-through
        would have."""
        wrapper, spy = self._make("poke:rw")
        result = await wrapper.search_text("pikachu")
        assert spy.calls[0][0] == "search_text"
        assert result == []

    def test_plain_attribute_delegates_via_getattr(self):
        """Non-method attribute access should also pass through."""
        wrapper, spy = self._make("poke:r")
        assert wrapper.capability_attr == "present"

    # --- status / aclose pass-through -----------------------------------------

    async def test_status_delegates(self):
        wrapper, spy = self._make("poke:none")
        await wrapper.status()
        assert spy.calls[0][0] == "status"

    async def test_aclose_delegates(self):
        wrapper, spy = self._make("poke:none")
        await wrapper.aclose()
        assert spy.calls[0][0] == "aclose"

    # --- AccessDenied is a PermissionError ------------------------------------

    def test_access_denied_is_permission_error(self):
        assert issubclass(AccessDenied, PermissionError)

    async def test_access_denied_message_contains_ontology(self):
        wrapper, _ = self._make("secret:none")
        with pytest.raises(AccessDenied, match="secret"):
            await wrapper.get_schema(ontology="secret")


# ── capability method guards (search_text, find_similar, bayes, causal, …) ─────


class TestReadGuardedCapabilities:
    """search_text / find_similar / sameas_closure / get_bayes_network /
    get_causal_model are dispatched via __getattr__ but must still honour the
    ontology= kwarg the caller passes, exactly like the explicit read methods.
    """

    def _make(self, policy_str: str) -> tuple[AccessControlledStore, _SpyStore]:
        spy = _SpyStore()
        policy = AccessPolicy.from_string(policy_str)
        return AccessControlledStore(spy, policy), spy

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("search_text", ("pikachu",)),
            ("find_similar", ("ex:pikachu",)),
            ("sameas_closure", ("ex:pikachu",)),
            ("get_bayes_network", ()),
            ("get_causal_model", ()),
        ],
    )
    async def test_denied_for_restricted_ontology(self, method, args):
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await getattr(wrapper, method)(*args, ontology="secret")
        assert not spy.calls

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("search_text", ("pikachu",)),
            ("find_similar", ("ex:pikachu",)),
            ("sameas_closure", ("ex:pikachu",)),
            ("get_bayes_network", ()),
            ("get_causal_model", ()),
        ],
    )
    async def test_allowed_for_readable_ontology(self, method, args):
        wrapper, spy = self._make("poke:r")
        await getattr(wrapper, method)(*args, ontology="poke")
        assert spy.calls[0][0] == method

    async def test_search_text_none_ontology_denied_by_explicit_default_none(self):
        """Same union-read regression as get_schema, for a capability method."""
        wrapper, spy = self._make("default:none")
        with pytest.raises(AccessDenied):
            await wrapper.search_text("pikachu", ontology=None)
        assert not spy.calls

    async def test_search_text_none_ontology_denied_by_fail_closed_guard(self):
        """Fail-closed union guard: an unrelated 'secret:none' entry also
        blocks search_text's union scope (ontology omitted -> None)."""
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.search_text("pikachu")
        assert not spy.calls


class TestWriteGuardedCapabilities:
    """build_embeddings / put_bayes_network / clear_bayes_network /
    put_causal_model / clear_causal_model mutate state and must be
    write-guarded, not read-guarded.
    """

    def _make(self, policy_str: str) -> tuple[AccessControlledStore, _SpyStore]:
        spy = _SpyStore()
        policy = AccessPolicy.from_string(policy_str)
        return AccessControlledStore(spy, policy), spy

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("build_embeddings", ()),
            ("put_bayes_network", (None,)),
            ("clear_bayes_network", ()),
            ("put_causal_model", (None,)),
            ("clear_causal_model", ()),
        ],
    )
    async def test_denied_for_read_only_ontology(self, method, args):
        """read-only ('poke:r') must not be enough to write."""
        wrapper, spy = self._make("poke:r")
        with pytest.raises(AccessDenied):
            await getattr(wrapper, method)(*args, ontology="poke")
        assert not spy.calls

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("build_embeddings", ()),
            ("put_bayes_network", (None,)),
            ("clear_bayes_network", ()),
            ("put_causal_model", (None,)),
            ("clear_causal_model", ()),
        ],
    )
    async def test_allowed_for_writable_ontology(self, method, args):
        wrapper, spy = self._make("poke:rw")
        await getattr(wrapper, method)(*args, ontology="poke")
        assert spy.calls[0][0] == method


class TestUnsupportedCapabilityFallback:
    """A backend that doesn't implement a capability must still look
    unsupported through the wrapper, so route-level 501 detection
    (`getattr(store, name, None) is None`) keeps working.
    """

    def test_missing_capability_getattr_default_returns_none(self):
        spy = _PartialSpyStore()
        policy = AccessPolicy.from_string("poke:r")
        wrapper = AccessControlledStore(spy, policy)
        assert getattr(wrapper, "find_similar", None) is None

    def test_missing_capability_matches_raw_store_behavior(self):
        """The wrapper must not appear MORE capable than the raw store."""
        spy = _PartialSpyStore()
        assert getattr(spy, "find_similar", None) is None

        policy = AccessPolicy.from_string("poke:r")
        wrapper = AccessControlledStore(spy, policy)
        assert getattr(wrapper, "find_similar", None) is None


# ── query_pattern guard ──────────────────────────────────────────────────────


class TestQueryPatternGuard:
    """query_pattern (L2 DSL) has no ontology field and the translators run
    against the full/union dataset, so it is guarded as an ontology=None read.
    """

    def _make(self, policy_str: str) -> tuple[AccessControlledStore, _SpyStore]:
        spy = _SpyStore()
        policy = AccessPolicy.from_string(policy_str)
        return AccessControlledStore(spy, policy), spy

    async def test_denied_when_default_none(self):
        wrapper, spy = self._make("default:none")
        with pytest.raises(AccessDenied):
            await wrapper.query_pattern(_A_PATTERN_QUERY)
        assert not spy.calls

    async def test_denied_by_fail_closed_guard_with_unrelated_restriction(self):
        """query_pattern's union scope is also blocked by the fail-closed
        guard — an unlisted 'default' key does not save it when some other
        ontology is explicitly denied read."""
        wrapper, spy = self._make("secret:none")
        with pytest.raises(AccessDenied):
            await wrapper.query_pattern(_A_PATTERN_QUERY)
        assert not spy.calls

    async def test_allowed_when_no_restrictions_at_all(self):
        wrapper, spy = self._make("poke:rw")
        await wrapper.query_pattern(_A_PATTERN_QUERY)
        assert spy.calls[0][0] == "query_pattern"

    async def test_allowed_when_default_explicitly_open_and_nothing_restricted(self):
        wrapper, spy = self._make("default:rw")
        await wrapper.query_pattern(_A_PATTERN_QUERY)
        assert spy.calls[0][0] == "query_pattern"


# ── audit logging ────────────────────────────────────────────────────────────


class TestAuditLogging:
    """Every guard decision (allow/deny) is logged via the
    'ontorag.access.audit' logger — a minimal subset of the roadmap's §3.3
    audit event (no request_id/subject/tenant; those need RequestContext,
    which is out of scope here).
    """

    def _make(self, policy_str: str) -> tuple[AccessControlledStore, _SpyStore]:
        spy = _SpyStore()
        policy = AccessPolicy.from_string(policy_str)
        return AccessControlledStore(spy, policy), spy

    async def test_deny_logged_as_warning(self, caplog):
        wrapper, _ = self._make("secret:none")
        with caplog.at_level(logging.WARNING, logger="ontorag.access.audit"):
            with pytest.raises(AccessDenied):
                await wrapper.get_schema(ontology="secret")
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        assert record.audit_method == "get_schema"
        assert record.audit_ontology == "secret"
        assert record.audit_mode == "read"
        assert record.audit_decision == "deny"

    async def test_allow_logged_as_info(self, caplog):
        wrapper, _ = self._make("poke:r")
        with caplog.at_level(logging.INFO, logger="ontorag.access.audit"):
            await wrapper.get_schema(ontology="poke")
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.INFO
        assert record.audit_method == "get_schema"
        assert record.audit_ontology == "poke"
        assert record.audit_mode == "read"
        assert record.audit_decision == "allow"

    async def test_write_deny_logged(self, caplog):
        wrapper, _ = self._make("poke:r")
        with caplog.at_level(logging.WARNING, logger="ontorag.access.audit"):
            with pytest.raises(AccessDenied):
                await wrapper.load_rdf("/tmp/f.ttl", ontology="poke")
        assert caplog.records[0].audit_mode == "write"
        assert caplog.records[0].audit_decision == "deny"

    async def test_capability_guard_also_audited(self, caplog):
        wrapper, _ = self._make("secret:none")
        with caplog.at_level(logging.WARNING, logger="ontorag.access.audit"):
            with pytest.raises(AccessDenied):
                await wrapper.search_text("pikachu", ontology="secret")
        assert caplog.records[0].audit_method == "search_text"
        assert caplog.records[0].audit_decision == "deny"

    async def test_fail_closed_union_deny_is_audited(self, caplog):
        """The fail-closed union guard denial is audited like any other deny,
        with ontology=None (not the unrelated restricted ontology's id)."""
        wrapper, _ = self._make("secret:none")
        with caplog.at_level(logging.WARNING, logger="ontorag.access.audit"):
            with pytest.raises(AccessDenied):
                await wrapper.get_schema(ontology=None)
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.audit_method == "get_schema"
        assert record.audit_ontology is None
        assert record.audit_mode == "read"
        assert record.audit_decision == "deny"


# ── factory wiring ─────────────────────────────────────────────────────────────


class TestFactoryWiring:
    def test_env_unset_returns_raw_store(self, monkeypatch):
        """No ONTOLOGY_ACCESS → raw FusekiStore (zero overhead)."""
        from ontorag.stores.factory import create_store
        from ontorag.stores.fuseki import FusekiStore

        monkeypatch.delenv("ONTOLOGY_ACCESS", raising=False)
        monkeypatch.delenv("GRAPH_STORE", raising=False)
        store = create_store()
        assert isinstance(store, FusekiStore)
        assert not isinstance(store, AccessControlledStore)

    def test_env_set_returns_access_controlled_store(self, monkeypatch):
        """ONTOLOGY_ACCESS set → AccessControlledStore wrapping the raw store."""
        from ontorag.stores.factory import create_store

        monkeypatch.delenv("GRAPH_STORE", raising=False)
        monkeypatch.setenv("ONTOLOGY_ACCESS", "poke:r")
        store = create_store()
        assert isinstance(store, AccessControlledStore)

    def test_env_empty_returns_raw_store(self, monkeypatch):
        """ONTOLOGY_ACCESS='' (empty) → no wrapping, backward-compatible."""
        from ontorag.stores.factory import create_store
        from ontorag.stores.fuseki import FusekiStore

        monkeypatch.delenv("GRAPH_STORE", raising=False)
        monkeypatch.setenv("ONTOLOGY_ACCESS", "")
        store = create_store()
        assert isinstance(store, FusekiStore)
        assert not isinstance(store, AccessControlledStore)

    def test_malformed_ontology_access_raises_at_factory(self, monkeypatch):
        """Malformed ONTOLOGY_ACCESS must propagate ValueError from factory."""
        from ontorag.stores.factory import create_store

        monkeypatch.delenv("GRAPH_STORE", raising=False)
        monkeypatch.setenv("ONTOLOGY_ACCESS", "bad-entry-no-colon")
        with pytest.raises(ValueError, match="Malformed"):
            create_store()
