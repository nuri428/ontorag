from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ontorag.stores.base import AggFunc, EntityFilter, FilterOp
from ontorag.stores.fuseki import FusekiStore

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store() -> FusekiStore:
    return FusekiStore("http://localhost:3030", "test", "admin", "admin")


def _uris_result(*uris: str):
    return {
        "results": {
            "bindings": [
                {"inst": {"value": u}, "label": {"value": u.split("/")[-1]}}
                for u in uris
            ]
        }
    }


def _props_result(uri: str, props: dict) -> dict:
    return {
        "results": {
            "bindings": [
                {"inst": {"value": uri}, "pred": {"value": p}, "obj": {"value": v}}
                for p, v in props.items()
            ]
        }
    }


_EMPTY = {"results": {"bindings": []}}

# ── find_entities ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_entities_returns_results(store):
    uri = "http://example.org/alice"
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                _uris_result(uri),
                _props_result(uri, {"http://xmlns.com/foaf/0.1/name": "Alice"}),
            ]
        ),
    ):
        results = await store.find_entities("http://xmlns.com/foaf/0.1/Person")

    assert len(results) == 1
    assert results[0].uri == uri
    assert "http://xmlns.com/foaf/0.1/name" in results[0].properties


@pytest.mark.asyncio
async def test_find_entities_empty_result(store):
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=_EMPTY)):
        results = await store.find_entities("http://example.org/Ghost")
    assert results == []


@pytest.mark.asyncio
async def test_find_entities_with_filter_includes_filter_triple(store):
    """Filter properties must appear in the SPARQL query sent to Fuseki."""
    calls: list[str] = []

    async def capture(sparql: str) -> dict:
        calls.append(sparql)
        if len(calls) == 1:
            return _uris_result("http://example.org/alice")
        return _EMPTY

    f = EntityFilter(property="foaf:age", op=FilterOp.gt, value=30)
    with patch.object(store, "_sparql_select", new=AsyncMock(side_effect=capture)):
        await store.find_entities("http://xmlns.com/foaf/0.1/Person", filters=[f])

    assert "foaf:age" in calls[0]
    assert "FILTER" in calls[0]


# ── describe_entity ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_entity_returns_properties(store):
    result_data = {
        "results": {
            "bindings": [
                {
                    "pred": {"value": "http://xmlns.com/foaf/0.1/name"},
                    "obj": {"value": "Alice"},
                    "label": {"value": "Alice"},
                },
                {
                    "pred": {
                        "value": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
                    },
                    "obj": {"value": "http://xmlns.com/foaf/0.1/Person"},
                },
            ]
        }
    }
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=result_data)):
        entity = await store.describe_entity("http://example.org/alice")

    assert entity.uri == "http://example.org/alice"
    assert entity.label == "Alice"
    assert entity.class_uri == "http://xmlns.com/foaf/0.1/Person"
    assert "http://xmlns.com/foaf/0.1/name" in entity.properties


@pytest.mark.asyncio
async def test_describe_entity_raises_key_error_when_not_found(store):
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=_EMPTY)):
        with pytest.raises(KeyError):
            await store.describe_entity("http://example.org/ghost")


# ── count_entities ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_entities_returns_integer(store):
    count_result = {"results": {"bindings": [{"n": {"value": "7"}}]}}
    with patch.object(
        store, "_sparql_select", new=AsyncMock(return_value=count_result)
    ):
        count = await store.count_entities("http://xmlns.com/foaf/0.1/Person")
    assert count == 7


@pytest.mark.asyncio
async def test_count_entities_returns_zero_when_empty(store):
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=_EMPTY)):
        count = await store.count_entities("http://example.org/Ghost")
    assert count == 0


# ── aggregate ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregate_count_returns_group_results(store):
    agg_result = {
        "results": {
            "bindings": [
                {"group": {"value": "Engineering"}, "result": {"value": "5"}},
                {"group": {"value": "Design"}, "result": {"value": "3"}},
            ]
        }
    }
    with patch.object(store, "_sparql_select", new=AsyncMock(return_value=agg_result)):
        results = await store.aggregate(
            "http://xmlns.com/foaf/0.1/Person",
            "http://example.org/department",
            AggFunc.count,
        )

    assert len(results) == 2
    assert results[0].group_value == "Engineering"
    assert results[0].result == 5
