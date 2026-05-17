from __future__ import annotations

import pytest

from ontorag.learn import relation
from ontorag.stores.base import ClassSummary, PropertySummary, SchemaResult
from tests.conftest import MockLLM, make_tool_response


@pytest.fixture()
def schema_with_properties() -> SchemaResult:
    """SchemaResult with top-level properties list for predicate validation tests."""
    return SchemaResult(
        total_classes=1,
        total_properties=2,
        namespaces={"pk": "http://example.org/pokemon#"},
        classes=[
            ClassSummary(
                uri="http://example.org/pokemon#Pokemon",
                label="Pokemon",
                parent_uri=None,
                property_count=2,
                instance_count=5,
            )
        ],
        properties=[
            PropertySummary(uri="http://example.org/pokemon#hasType", label="hasType", prop_type="object"),
            PropertySummary(uri="http://example.org/pokemon#hp", label="HP", prop_type="datatype"),
        ],
    )


class TestExtractRelations:
    @pytest.mark.asyncio
    async def test_returns_valid_triples(self, schema_with_properties):
        response = make_tool_response("report_triples", {
            "triples": [
                {
                    "subject_label": "Pikachu",
                    "subject_uri": None,
                    "predicate_uri": "http://example.org/pokemon#hasType",
                    "object_uri": "http://example.org/pokemon#Electric",
                    "object_value": None,
                    "confidence": 0.92,
                },
                {
                    "subject_label": "Pikachu",
                    "subject_uri": None,
                    "predicate_uri": "http://example.org/pokemon#hp",
                    "object_uri": None,
                    "object_value": "35",
                    "confidence": 0.85,
                },
            ]
        })
        results = await relation.extract_relations(
            MockLLM(response), schema_with_properties, "Pikachu is Electric type with 35 HP.", None, 0.7
        )

        assert len(results) == 2
        assert results[0].subject_label == "Pikachu"
        assert results[0].predicate_uri == "http://example.org/pokemon#hasType"
        assert results[0].object_uri == "http://example.org/pokemon#Electric"

    @pytest.mark.asyncio
    async def test_filters_unknown_predicate(self, schema_with_properties):
        response = make_tool_response("report_triples", {
            "triples": [
                {
                    "subject_label": "Pikachu",
                    "predicate_uri": "http://example.org/pokemon#FAKE",
                    "confidence": 0.9,
                },
            ]
        })
        results = await relation.extract_relations(
            MockLLM(response), schema_with_properties, "text", None, 0.7
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_filters_below_min_confidence(self, schema_with_properties):
        response = make_tool_response("report_triples", {
            "triples": [
                {
                    "subject_label": "Pikachu",
                    "predicate_uri": "http://example.org/pokemon#hasType",
                    "confidence": 0.5,
                },
            ]
        })
        results = await relation.extract_relations(
            MockLLM(response), schema_with_properties, "text", None, 0.7
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_schema_without_properties_accepts_all(self, pokemon_schema):
        """When SchemaResult.properties is empty, predicate validation is skipped."""
        response = make_tool_response("report_triples", {
            "triples": [
                {
                    "subject_label": "Ash",
                    "predicate_uri": "http://example.org/pokemon#anyPredicate",
                    "object_value": "value",
                    "confidence": 0.9,
                },
            ]
        })
        results = await relation.extract_relations(
            MockLLM(response), pokemon_schema, "text", None, 0.7
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self, schema_with_properties):
        class FailingLLM:
            async def complete(self, *args, **kwargs):
                raise ConnectionError("network error")

        results = await relation.extract_relations(
            FailingLLM(), schema_with_properties, "text", None, 0.7
        )
        assert results == []
