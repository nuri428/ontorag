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


def test_create_store_neo4j_returns_neo4j_store(monkeypatch):
    """neo4j backend is now wired — GRAPH_STORE=neo4j returns Neo4jStore."""
    import neo4j  # noqa: PLC0415
    from unittest.mock import MagicMock  # noqa: PLC0415

    monkeypatch.setenv("GRAPH_STORE", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test")

    original_driver = neo4j.AsyncGraphDatabase.driver
    neo4j.AsyncGraphDatabase.driver = MagicMock(return_value=MagicMock())
    try:
        from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

        store = create_store()
        assert isinstance(store, Neo4jStore)
    finally:
        neo4j.AsyncGraphDatabase.driver = original_driver


def test_create_store_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("GRAPH_STORE", "sqlite")
    with pytest.raises(ValueError, match="Unknown GRAPH_STORE"):
        create_store()
