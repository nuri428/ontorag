from __future__ import annotations

import pytest

from ontorag.learn import taxonomy
from tests.conftest import MockLLM, make_tool_response


class TestDiscoverTaxonomy:
    @pytest.mark.asyncio
    async def test_returns_valid_relations(self, pokemon_schema):
        response = make_tool_response("report_taxonomy_relations", {
            "relations": [
                {"child_term": "FirePokemon", "parent_uri": "http://example.org/pokemon#Pokemon", "confidence": 0.88},
                {"child_term": "WaterPokemon", "parent_uri": "http://example.org/pokemon#Pokemon", "confidence": 0.82},
            ]
        })
        results = await taxonomy.discover_taxonomy(MockLLM(response), pokemon_schema, "Some text", None)

        assert len(results) == 2
        assert results[0].child_term == "FirePokemon"
        assert results[0].parent_uri == "http://example.org/pokemon#Pokemon"
        assert results[0].confidence == pytest.approx(0.88)

    @pytest.mark.asyncio
    async def test_filters_unknown_parent_uri(self, pokemon_schema):
        response = make_tool_response("report_taxonomy_relations", {
            "relations": [
                {"child_term": "MysteryPokemon", "parent_uri": "http://example.org/pokemon#Ghost", "confidence": 0.9},
                {"child_term": "FirePokemon", "parent_uri": "http://example.org/pokemon#Pokemon", "confidence": 0.85},
            ]
        })
        results = await taxonomy.discover_taxonomy(MockLLM(response), pokemon_schema, "text", None)

        parent_uris = [r.parent_uri for r in results]
        assert "http://example.org/pokemon#Ghost" not in parent_uris
        assert "http://example.org/pokemon#Pokemon" in parent_uris

    @pytest.mark.asyncio
    async def test_empty_relations(self, pokemon_schema):
        response = make_tool_response("report_taxonomy_relations", {"relations": []})
        results = await taxonomy.discover_taxonomy(MockLLM(response), pokemon_schema, "text", None)
        assert results == []

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self, pokemon_schema):
        class FailingLLM:
            async def complete(self, *args, **kwargs):
                raise RuntimeError("timeout")

        results = await taxonomy.discover_taxonomy(FailingLLM(), pokemon_schema, "text", None)
        assert results == []
