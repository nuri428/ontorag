from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ontorag.stores.fuseki import FusekiStore

# ─── Fixture helpers ──────────────────────────────────────────────────────────

CLASSES_RESULT = {
    "results": {
        "bindings": [
            {
                "class": {"value": "http://xmlns.com/foaf/0.1/Person"},
                "label": {"value": "Person"},
            },
            {
                "class": {"value": "http://xmlns.com/foaf/0.1/Agent"},
                "label": {"value": "Agent"},
            },
        ]
    }
}

PROPS_RESULT = {
    "results": {
        "bindings": [
            {
                "prop": {"value": "http://xmlns.com/foaf/0.1/name"},
                "domain": {"value": "http://xmlns.com/foaf/0.1/Person"},
                "propType": {"value": "http://www.w3.org/2002/07/owl#DatatypeProperty"},
            },
            {
                "prop": {"value": "http://xmlns.com/foaf/0.1/knows"},
                "domain": {"value": "http://xmlns.com/foaf/0.1/Person"},
                "propType": {"value": "http://www.w3.org/2002/07/owl#ObjectProperty"},
            },
        ]
    }
}

INST_COUNT_RESULT = {
    "results": {
        "bindings": [
            {
                "class": {"value": "http://xmlns.com/foaf/0.1/Person"},
                "count": {"value": "5"},
            }
        ]
    }
}

META_RESULT = {
    "results": {
        "bindings": [
            {
                "label": {"value": "Person"},
                "description": {"value": "A human being."},
                "parent": {"value": "http://xmlns.com/foaf/0.1/Agent"},
            }
        ]
    }
}

CLASS_PROPS_RESULT = {
    "results": {
        "bindings": [
            {
                "prop": {"value": "http://xmlns.com/foaf/0.1/name"},
                "propType": {"value": "http://www.w3.org/2002/07/owl#DatatypeProperty"},
                "label": {"value": "name"},
                "range": {"value": "http://www.w3.org/2001/XMLSchema#string"},
            }
        ]
    }
}

CHILDREN_RESULT = {"results": {"bindings": []}}

SAMPLE_INST_RESULT = {
    "results": {
        "bindings": [
            {"inst": {"value": "http://example.org/alice"}},
            {"inst": {"value": "http://example.org/bob"}},
        ]
    }
}

CLASS_INST_COUNT_RESULT = {"results": {"bindings": [{"n": {"value": "2"}}]}}


# ─── get_schema ───────────────────────────────────────────────────────────────


@pytest.fixture
def store():
    return FusekiStore("http://localhost:3030", "test", "admin", "admin")


@pytest.mark.asyncio
async def test_get_schema_returns_class_summaries(store):
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(side_effect=[CLASSES_RESULT, PROPS_RESULT, INST_COUNT_RESULT]),
    ):
        result = await store.get_schema()

    assert result.total_classes == 2
    assert result.total_properties == 2


@pytest.mark.asyncio
async def test_get_schema_property_counts_per_class(store):
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(side_effect=[CLASSES_RESULT, PROPS_RESULT, INST_COUNT_RESULT]),
    ):
        result = await store.get_schema()

    person = next(c for c in result.classes if "Person" in c.uri)
    assert person.property_count == 2


@pytest.mark.asyncio
async def test_get_schema_instance_counts(store):
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(side_effect=[CLASSES_RESULT, PROPS_RESULT, INST_COUNT_RESULT]),
    ):
        result = await store.get_schema()

    person = next(c for c in result.classes if "Person" in c.uri)
    assert person.instance_count == 5


@pytest.mark.asyncio
async def test_get_schema_no_duplicate_classes(store):
    """Classes with multiple parent entries in SPARQL must be deduplicated."""
    duplicate_classes = {
        "results": {
            "bindings": [
                {
                    "class": {"value": "http://ex.org/A"},
                    "parent": {"value": "http://ex.org/B"},
                },
                {
                    "class": {"value": "http://ex.org/A"},
                    "parent": {"value": "http://ex.org/C"},
                },
            ]
        }
    }
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                duplicate_classes,
                {"results": {"bindings": []}},
                {"results": {"bindings": []}},
            ]
        ),
    ):
        result = await store.get_schema()

    assert result.total_classes == 1


# ─── get_class_detail ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_class_detail_returns_detail(store):
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                META_RESULT,
                CLASS_PROPS_RESULT,
                CHILDREN_RESULT,
                SAMPLE_INST_RESULT,
                CLASS_INST_COUNT_RESULT,
            ]
        ),
    ):
        detail = await store.get_class_detail("http://xmlns.com/foaf/0.1/Person")

    assert detail.label == "Person"
    assert detail.description == "A human being."
    assert "http://xmlns.com/foaf/0.1/Agent" in detail.parent_uris


@pytest.mark.asyncio
async def test_get_class_detail_properties(store):
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                META_RESULT,
                CLASS_PROPS_RESULT,
                CHILDREN_RESULT,
                SAMPLE_INST_RESULT,
                CLASS_INST_COUNT_RESULT,
            ]
        ),
    ):
        detail = await store.get_class_detail("http://xmlns.com/foaf/0.1/Person")

    assert len(detail.properties) == 1
    assert detail.properties[0].prop_type == "datatype"
    assert detail.properties[0].label == "name"


@pytest.mark.asyncio
async def test_get_class_detail_instance_count_and_samples(store):
    with patch.object(
        store,
        "_sparql_select",
        new=AsyncMock(
            side_effect=[
                META_RESULT,
                CLASS_PROPS_RESULT,
                CHILDREN_RESULT,
                SAMPLE_INST_RESULT,
                CLASS_INST_COUNT_RESULT,
            ]
        ),
    ):
        detail = await store.get_class_detail("http://xmlns.com/foaf/0.1/Person")

    assert detail.instance_count == 2
    assert len(detail.sample_instance_uris) == 2


# ─── query_pattern ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_pattern_translates_and_returns_rows(store):
    from ontorag.stores.base import PatternQuery, PatternTriple

    query = PatternQuery(
        select=["?person"],
        where=[PatternTriple(s="?person", p="rdf:type", o="foaf:Person")],
    )
    raw_response = {
        "head": {"vars": ["person"]},
        "results": {
            "bindings": [
                {"person": {"value": "http://example.org/alice"}},
                {"person": {"value": "http://example.org/bob"}},
            ]
        },
    }

    with patch.object(
        store, "_sparql_select", new=AsyncMock(return_value=raw_response)
    ):
        result = await store.query_pattern(query)

    assert result.columns == ["person"]
    assert result.total == 2
    assert result.rows[0]["person"] == "http://example.org/alice"
