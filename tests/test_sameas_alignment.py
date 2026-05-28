"""Tests for cross-ontology owl:sameAs alignment (find_aligned / sameas_closure).

Covers both backends with unit (mock) tests and integration tests that require
live containers (guarded by the same skipif pattern used in test_inverse_of.py).

Unit tests (no backend required):
  - Fuseki: mocks ``_sparql_select`` to verify the SPARQL closure is called and
    the result shape ``{"uri", "label"}`` is returned correctly.
  - Neo4j: mocks ``_run`` to verify the Cypher closure query and result shape.
  - Agent dispatch: mocks the store and verifies ``_call_tool("find_aligned",…)``
    routes to ``sameas_closure`` and wraps results in the expected envelope.

Integration tests (pytestmark skipif guards):
  - Fuseki: loads sameas_{schema,data}.ttl, asserts transitive + symmetric
    closure returns Beta and Gamma for Alpha.
  - Neo4j: same assertions via Cypher/n10s.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ontorag.stores.fuseki import FusekiStore

# ── Constants ─────────────────────────────────────────────────────────────────

_EX = "http://example.org/sameas#"
_DATA = "http://example.org/sameas/data#"

_ALPHA = f"{_DATA}Alpha"
_BETA = f"{_DATA}Beta"
_GAMMA = f"{_DATA}Gamma"
_DELTA = f"{_DATA}Delta"

_FIXTURES = Path(__file__).parent / "fixtures"
_SCHEMA_TTL = str(_FIXTURES / "sameas_schema.ttl")
_DATA_TTL = str(_FIXTURES / "sameas_data.ttl")

_FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030")
_NEO4J_URI = "bolt://localhost:7687"
_NEO4J_USER = "neo4j"
_NEO4J_PASSWORD = "ontorag123"

# ── Connectivity guards ───────────────────────────────────────────────────────


def _fuseki_reachable() -> bool:
    try:
        import httpx

        resp = httpx.get(f"{_FUSEKI_URL}/$/ping", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def _neo4j_reachable() -> bool:
    try:
        host, port_str = _NEO4J_URI.replace("bolt://", "").split(":")
        with socket.create_connection((host, int(port_str)), timeout=2):
            return True
    except Exception:
        return False


_FUSEKI_UP = _fuseki_reachable()
_NEO4J_UP = _neo4j_reachable()

# ── Helpers ───────────────────────────────────────────────────────────────────


def _empty_bindings() -> dict:
    return {"results": {"bindings": []}}


def _sameas_bindings(uris_labels: list[tuple[str, str | None]]) -> dict:
    """Build a fake SPARQL result for the sameas_closure query.

    Args:
        uris_labels: List of (uri, label_or_None) tuples to return as bindings.

    Returns:
        Dict shaped like a SPARQL SELECT JSON response.
    """
    bindings = []
    for uri, label in uris_labels:
        b: dict = {"other": {"type": "uri", "value": uri}}
        if label is not None:
            b["label"] = {"type": "literal", "value": label}
        bindings.append(b)
    return {"results": {"bindings": bindings}}


# ── Unit tests: Fuseki mock ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fuseki_unit_sameas_returns_equivalents():
    """Mock unit: sameas_closure returns Beta and Gamma for Alpha."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            return_value=_sameas_bindings([(_BETA, "Beta"), (_GAMMA, "Gamma")])
        ),
    ):
        result = await store.sameas_closure(_ALPHA)

    uris = [r["uri"] for r in result]
    assert _BETA in uris, f"Beta must be in sameAs closure of Alpha; got {uris}"
    assert _GAMMA in uris, f"Gamma must be in sameAs closure of Alpha; got {uris}"
    # Alpha itself must not appear
    assert _ALPHA not in uris, "Alpha must be excluded from its own closure"


@pytest.mark.asyncio
async def test_fuseki_unit_sameas_no_equivalents():
    """Mock unit: sameas_closure returns empty list when no sameAs triples exist."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(return_value=_empty_bindings()),
    ):
        result = await store.sameas_closure(_DELTA)

    assert result == [], f"Delta has no sameAs equivalents; got {result}"


@pytest.mark.asyncio
async def test_fuseki_unit_sameas_result_shape():
    """Mock unit: each result item has 'uri' and 'label' keys."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(return_value=_sameas_bindings([(_BETA, "Beta")])),
    ):
        result = await store.sameas_closure(_ALPHA)

    assert len(result) == 1
    item = result[0]
    assert "uri" in item
    assert "label" in item
    assert item["uri"] == _BETA
    assert item["label"] == "Beta"


@pytest.mark.asyncio
async def test_fuseki_unit_sameas_label_none_when_missing():
    """Mock unit: label is None when the binding lacks a label entry."""
    store = FusekiStore("http://localhost:3030", "test", "admin", "admin")

    # Return a binding without a label key
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            return_value=_sameas_bindings([(_BETA, None)])
        ),
    ):
        result = await store.sameas_closure(_ALPHA)

    assert result[0]["label"] is None


# ── Unit tests: Neo4j mock ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_neo4j_unit_sameas_returns_equivalents():
    """Mock unit (Neo4j): sameas_closure returns Beta and Gamma for Alpha."""
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    store = Neo4jStore.__new__(Neo4jStore)
    # Inject required attributes without calling __init__
    store._prefix_map_loaded = True
    store._prefix_map = {}

    async def _fake_ensure():
        pass

    store._ensure_prefix_map = _fake_ensure

    store._run = AsyncMock(
        return_value=[
            {"uri": _BETA, "label": ["Beta"]},
            {"uri": _GAMMA, "label": ["Gamma"]},
        ]
    )

    result = await store.sameas_closure(_ALPHA)

    uris = [r["uri"] for r in result]
    assert _BETA in uris
    assert _GAMMA in uris
    assert _ALPHA not in uris


@pytest.mark.asyncio
async def test_neo4j_unit_sameas_no_equivalents():
    """Mock unit (Neo4j): sameas_closure returns empty list when no rows."""
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    store = Neo4jStore.__new__(Neo4jStore)
    store._prefix_map_loaded = True
    store._prefix_map = {}

    async def _fake_ensure():
        pass

    store._ensure_prefix_map = _fake_ensure
    store._run = AsyncMock(return_value=[])

    result = await store.sameas_closure(_DELTA)

    assert result == []


# ── Unit tests: Agent dispatch ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_call_tool_find_aligned_dispatch():
    """Agent _call_tool('find_aligned',...) routes to sameas_closure."""
    from ontorag.chat.agent import AgentLoop  # noqa: PLC0415

    mock_store = MagicMock()
    mock_store.sameas_closure = AsyncMock(
        return_value=[
            {"uri": _BETA, "label": "Beta"},
            {"uri": _GAMMA, "label": "Gamma"},
        ]
    )
    mock_llm = MagicMock()

    agent = AgentLoop(mock_store, mock_llm)
    result = await agent._call_tool("find_aligned", {"uri": _ALPHA})

    mock_store.sameas_closure.assert_awaited_once_with(_ALPHA, ontology=None)
    assert result["returned"] == 2
    assert len(result["aligned"]) == 2
    uris = [item["uri"] for item in result["aligned"]]
    assert _BETA in uris
    assert _GAMMA in uris


@pytest.mark.asyncio
async def test_agent_call_tool_find_aligned_with_ontology():
    """Agent _call_tool('find_aligned',...) passes ontology= through."""
    from ontorag.chat.agent import AgentLoop  # noqa: PLC0415

    mock_store = MagicMock()
    mock_store.sameas_closure = AsyncMock(return_value=[])
    mock_llm = MagicMock()

    agent = AgentLoop(mock_store, mock_llm)
    result = await agent._call_tool("find_aligned", {"uri": _ALPHA, "ontology": "onto1"})

    mock_store.sameas_closure.assert_awaited_once_with(_ALPHA, ontology="onto1")
    assert result["returned"] == 0
    assert result["aligned"] == []


@pytest.mark.asyncio
async def test_agent_call_tool_find_aligned_missing_capability():
    """Agent _call_tool('find_aligned',...) returns error when capability absent."""
    from ontorag.chat.agent import AgentLoop  # noqa: PLC0415

    mock_store = MagicMock(spec=[])  # spec=[] means NO attributes — simulates missing method
    mock_llm = MagicMock()

    agent = AgentLoop(mock_store, mock_llm)
    result = await agent._call_tool("find_aligned", {"uri": _ALPHA})

    assert "error" in result
    assert result["aligned"] == []
    assert result["returned"] == 0


# ── Integration tests: Fuseki ─────────────────────────────────────────────────


@pytest.fixture()
async def fuseki_store_sameas():
    """Live Fuseki store loaded with sameAs fixture; skips if Fuseki is down."""
    if not _FUSEKI_UP:
        pytest.skip(f"Fuseki not reachable at {_FUSEKI_URL}")
    os.environ.setdefault("FUSEKI_URL", _FUSEKI_URL)
    store = FusekiStore.from_env()
    await store.clear_graph("all")
    await store.load_rdf(_SCHEMA_TTL, mode="schema")
    await store.load_rdf(_DATA_TTL, mode="data")
    yield store
    await store.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_sameas_closure_direct(fuseki_store_sameas):
    """Live Fuseki: sameas_closure(Alpha) includes Beta (direct assertion)."""
    result = await fuseki_store_sameas.sameas_closure(_ALPHA)
    uris = [r["uri"] for r in result]
    assert _BETA in uris, f"Beta must appear in Alpha's sameAs closure; got {uris}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_sameas_closure_transitive(fuseki_store_sameas):
    """Live Fuseki: sameas_closure(Alpha) includes Gamma via Beta (transitivity)."""
    result = await fuseki_store_sameas.sameas_closure(_ALPHA)
    uris = [r["uri"] for r in result]
    assert _GAMMA in uris, f"Gamma must appear via transitivity; got {uris}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_sameas_excludes_self(fuseki_store_sameas):
    """Live Fuseki: sameas_closure(Alpha) does not include Alpha itself."""
    result = await fuseki_store_sameas.sameas_closure(_ALPHA)
    uris = [r["uri"] for r in result]
    assert _ALPHA not in uris


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_sameas_empty_for_unrelated(fuseki_store_sameas):
    """Live Fuseki: sameas_closure(Delta) returns empty (no sameAs assertions)."""
    result = await fuseki_store_sameas.sameas_closure(_DELTA)
    assert result == [], f"Delta has no sameAs; expected [], got {result}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _FUSEKI_UP, reason=f"Fuseki not reachable at {_FUSEKI_URL}")
async def test_fuseki_integration_sameas_symmetric_from_beta(fuseki_store_sameas):
    """Live Fuseki: sameas_closure(Beta) includes Alpha (symmetry from Beta's perspective).

    Alpha --sameAs--> Beta is asserted, so querying from Beta should also
    find Alpha via the (owl:sameAs|^owl:sameAs)+ property path.
    """
    result = await fuseki_store_sameas.sameas_closure(_BETA)
    uris = [r["uri"] for r in result]
    assert _ALPHA in uris, f"Alpha must appear via symmetric path from Beta; got {uris}"


# ── Integration tests: Neo4j ──────────────────────────────────────────────────


@pytest.fixture()
async def neo4j_store_sameas():
    """Live Neo4j store loaded with sameAs fixture; skips if Neo4j is down."""
    if not _NEO4J_UP:
        pytest.skip("Neo4j not reachable at bolt://localhost:7687")
    from ontorag.stores.neo4j import Neo4jStore  # noqa: PLC0415

    store = Neo4jStore(uri=_NEO4J_URI, user=_NEO4J_USER, password=_NEO4J_PASSWORD)
    await store._run_write("MATCH (p:_NsPrefDef) DETACH DELETE p")
    await store.clear_graph("all")
    await store.load_rdf(_SCHEMA_TTL, mode="schema")
    await store.load_rdf(_DATA_TTL, mode="data")
    yield store
    await store.aclose()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_sameas_closure_direct(neo4j_store_sameas):
    """Live Neo4j: sameas_closure(Alpha) includes Beta (direct assertion)."""
    result = await neo4j_store_sameas.sameas_closure(_ALPHA)
    uris = [r["uri"] for r in result]
    assert _BETA in uris, f"Beta must appear in Alpha's sameAs closure; got {uris}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_sameas_closure_transitive(neo4j_store_sameas):
    """Live Neo4j: sameas_closure(Alpha) includes Gamma via Beta (transitivity)."""
    result = await neo4j_store_sameas.sameas_closure(_ALPHA)
    uris = [r["uri"] for r in result]
    assert _GAMMA in uris, f"Gamma must appear via transitivity; got {uris}"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_sameas_excludes_self(neo4j_store_sameas):
    """Live Neo4j: sameas_closure(Alpha) does not include Alpha itself."""
    result = await neo4j_store_sameas.sameas_closure(_ALPHA)
    uris = [r["uri"] for r in result]
    assert _ALPHA not in uris


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _NEO4J_UP, reason="Neo4j not reachable at bolt://localhost:7687")
async def test_neo4j_integration_sameas_empty_for_unrelated(neo4j_store_sameas):
    """Live Neo4j: sameas_closure(Delta) returns empty (no sameAs assertions)."""
    result = await neo4j_store_sameas.sameas_closure(_DELTA)
    assert result == [], f"Delta has no sameAs; expected [], got {result}"
