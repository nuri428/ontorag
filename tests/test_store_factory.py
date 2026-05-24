from __future__ import annotations

import pytest

from ontorag.stores.base import GraphStore
from ontorag.stores.factory import create_store
from ontorag.stores.fuseki import FusekiStore


def test_create_store_defaults_to_fuseki(monkeypatch):
    """No GRAPH_STORE env → fuseki (the v0.1 default backend)."""
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    assert isinstance(create_store(), FusekiStore)


def test_create_store_fuseki_explicit(monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "fuseki")
    assert isinstance(create_store(), FusekiStore)


def test_create_store_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "  FUSEKI  ")
    assert isinstance(create_store(), FusekiStore)


def test_create_store_returns_graphstore_protocol(monkeypatch):
    """Returned object must satisfy the GraphStore protocol (runtime_checkable)."""
    monkeypatch.setenv("GRAPH_STORE", "fuseki")
    assert isinstance(create_store(), GraphStore)


def test_create_store_neo4j_not_yet_supported(monkeypatch):
    """neo4j is a recognised backend but its adapter ships later in v0.5.0."""
    monkeypatch.setenv("GRAPH_STORE", "neo4j")
    with pytest.raises(ValueError, match="neo4j"):
        create_store()


def test_create_store_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "sqlite")
    with pytest.raises(ValueError, match="Unknown GRAPH_STORE"):
        create_store()
