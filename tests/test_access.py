"""Tests for per-ontology access control.

Covers:
* Policy parsing (formats, write-implies-read, unlisted=open, none denies
  both, malformed→ValueError, invalid id→ValueError).
* Wrapper enforcement against a spy GraphStore (denied write raises
  AccessDenied; allowed write delegates; denied scoped read raises;
  ontology=None read always allowed; unknown method delegates via __getattr__).
* Factory wiring (ONTOLOGY_ACCESS set → AccessControlledStore;
  env unset → raw store).
"""

from __future__ import annotations

import pytest

from ontorag.core.access import AccessPolicy, Permission
from ontorag.stores.access_wrapper import AccessControlledStore, AccessDenied
from ontorag.stores.base import (
    AggFunc,
    LoadResult,
    SchemaResult,
    TraversalDirection,
)

# ── helpers ────────────────────────────────────────────────────────────────────

_NO_CLASSES = SchemaResult(
    total_classes=0, total_properties=0, namespaces={}, classes=[]
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

    async def load_rdf(self, path, mode="auto", replace=False, ontology=None):
        self._record("load_rdf", path=path, ontology=ontology)
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

    async def search_text(self, query, **kwargs):
        """Capability method — not guarded, must pass through via __getattr__."""
        self._record("search_text", query=query)
        return []


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

    # --- ontology=None read always allowed ------------------------------------

    async def test_get_schema_none_ontology_always_allowed(self):
        """ontology=None must never be blocked by the read guard (union view)."""
        wrapper, spy = self._make("secret:none")
        await wrapper.get_schema(ontology=None)
        assert spy.calls[0][0] == "get_schema"

    async def test_find_entities_none_ontology_allowed(self):
        wrapper, spy = self._make("secret:none")
        await wrapper.find_entities("ex:Foo", ontology=None)
        assert spy.calls[0][0] == "find_entities"

    # --- scoped read allowed --------------------------------------------------

    async def test_get_schema_allowed_delegates(self):
        wrapper, spy = self._make("poke:r")
        await wrapper.get_schema(ontology="poke")
        assert spy.calls[0][0] == "get_schema"

    async def test_get_class_detail_allowed_delegates(self):
        wrapper, spy = self._make("poke:r")
        await wrapper.get_class_detail("ex:Foo", ontology="poke")
        assert spy.calls[0][0] == "get_class_detail"

    # --- __getattr__ delegation -----------------------------------------------

    async def test_search_text_delegates_via_getattr(self):
        """Capability method not defined on wrapper must delegate via __getattr__."""
        wrapper, spy = self._make("poke:none")
        # search_text is NOT a guarded method — it should pass through directly.
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
