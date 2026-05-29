"""Tests for configurable query/LLM timeouts (v1.0 hardening).

Covers:
- env_timeout parsing (number / unset / 0 / malformed)
- each graph store actually passes the timeout to its driver call (mocked)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ontorag.core.config import env_timeout


# ── env_timeout ────────────────────────────────────────────────────────────


def test_env_timeout_unset_uses_default(monkeypatch):
    monkeypatch.delenv("X_TIMEOUT", raising=False)
    assert env_timeout("X_TIMEOUT", 30.0) == 30.0


def test_env_timeout_parses_number(monkeypatch):
    monkeypatch.setenv("X_TIMEOUT", "12.5")
    assert env_timeout("X_TIMEOUT", 30.0) == 12.5


def test_env_timeout_zero_means_unbounded(monkeypatch):
    monkeypatch.setenv("X_TIMEOUT", "0")
    assert env_timeout("X_TIMEOUT", 30.0) is None


def test_env_timeout_empty_uses_default(monkeypatch):
    monkeypatch.setenv("X_TIMEOUT", "")
    assert env_timeout("X_TIMEOUT", 30.0) == 30.0


def test_env_timeout_malformed_falls_back(monkeypatch):
    monkeypatch.setenv("X_TIMEOUT", "not-a-number")
    assert env_timeout("X_TIMEOUT", 30.0) == 30.0


# ── Neo4j passes timeout to session.run ────────────────────────────────────


@pytest.mark.asyncio
async def test_neo4j_run_passes_timeout(monkeypatch):
    pytest.importorskip("neo4j")
    from ontorag.stores.neo4j import Neo4jStore

    store = Neo4jStore.__new__(Neo4jStore)  # skip __init__ (no real driver)
    store._database = "neo4j"
    store._query_timeout = 7.0

    captured: dict = {}

    class _Result:
        async def data(self):
            return []

    class _Session:
        async def run(self, cypher, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    store._driver = MagicMock()
    store._driver.session = MagicMock(return_value=_Session())

    await store._run("MATCH (n) RETURN n")
    assert captured["timeout"] == 7.0


# ── FalkorDB passes timeout (ms) to graph.query ────────────────────────────


@pytest.mark.asyncio
async def test_falkordb_run_passes_timeout_ms(monkeypatch):
    pytest.importorskip("falkordb")
    from ontorag.stores.falkordb import FalkorDBStore

    store = FalkorDBStore.__new__(FalkorDBStore)  # skip __init__
    store._timeout_ms = 5000

    captured: dict = {}

    class _Graph:
        async def query(self, cypher, params=None, timeout=None):
            captured["timeout"] = timeout
            return MagicMock(header=[], result_set=[])

    store._graph = _Graph()
    await store._run("MATCH (n) RETURN n")
    assert captured["timeout"] == 5000


# ── Fuseki builds the httpx client with the configured timeout ──────────────


@pytest.mark.asyncio
async def test_fuseki_http_uses_timeout(monkeypatch):
    from ontorag.stores.fuseki import FusekiStore

    store = FusekiStore(
        url="http://localhost:3030",
        dataset="t",
        user="u",
        password="p",
        timeout=9.0,
    )
    client = await store._http()
    try:
        # httpx stores the connect/read/write/pool timeouts; read should match.
        assert client.timeout.read == 9.0
    finally:
        await store.aclose()
