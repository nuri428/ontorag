from __future__ import annotations

import pytest

from ontorag.learn import term_typing
from tests.conftest import MockLLM, make_tool_response


class TestTypeTermHappyPath:
    """Task A returns correctly parsed TermTypingResult list."""

    @pytest.mark.asyncio
    async def test_returns_ranked_results(self, pokemon_schema):
        response = make_tool_response(
            "report_term_typings",
            {
                "typings": [
                    {
                        "class_uri": "http://example.org/pokemon#Pokemon",
                        "confidence": 0.95,
                        "reasoning": "It is a Pokemon",
                    },
                    {
                        "class_uri": "http://example.org/pokemon#LegendaryPokemon",
                        "confidence": 0.4,
                        "reasoning": "Could be legendary",
                    },
                ]
            },
        )
        llm = MockLLM(response)

        results = await term_typing.type_term(
            llm, pokemon_schema, "Pikachu", context=None, top_k=3
        )

        assert len(results) == 2
        assert results[0].class_uri == "http://example.org/pokemon#Pokemon"
        assert results[0].confidence == pytest.approx(0.95)
        assert results[0].term == "Pikachu"
        assert results[1].class_uri == "http://example.org/pokemon#LegendaryPokemon"

    @pytest.mark.asyncio
    async def test_top_k_limit(self, pokemon_schema):
        response = make_tool_response(
            "report_term_typings",
            {
                "typings": [
                    {
                        "class_uri": "http://example.org/pokemon#Pokemon",
                        "confidence": 0.9,
                    },
                    {
                        "class_uri": "http://example.org/pokemon#LegendaryPokemon",
                        "confidence": 0.8,
                    },
                    {"class_uri": "http://example.org/pokemon#Type", "confidence": 0.3},
                ]
            },
        )
        llm = MockLLM(response)

        results = await term_typing.type_term(
            llm, pokemon_schema, "Mewtwo", context=None, top_k=2
        )

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_label_populated_from_schema(self, pokemon_schema):
        response = make_tool_response(
            "report_term_typings",
            {
                "typings": [
                    {
                        "class_uri": "http://example.org/pokemon#Pokemon",
                        "confidence": 0.9,
                    },
                ]
            },
        )
        results = await term_typing.type_term(
            MockLLM(response), pokemon_schema, "Bulbasaur", None, 3
        )

        assert results[0].label == "Pokemon"


class TestTypeTermValidation:
    """Task A filters out URIs not in the TBox."""

    @pytest.mark.asyncio
    async def test_unknown_uri_filtered(self, pokemon_schema):
        response = make_tool_response(
            "report_term_typings",
            {
                "typings": [
                    {
                        "class_uri": "http://example.org/pokemon#FakeClass",
                        "confidence": 0.99,
                    },
                    {
                        "class_uri": "http://example.org/pokemon#Pokemon",
                        "confidence": 0.7,
                    },
                ]
            },
        )
        results = await term_typing.type_term(
            MockLLM(response), pokemon_schema, "X", None, 3
        )

        uris = [r.class_uri for r in results]
        assert "http://example.org/pokemon#FakeClass" not in uris
        assert "http://example.org/pokemon#Pokemon" in uris

    @pytest.mark.asyncio
    async def test_empty_typings_returns_empty_list(self, pokemon_schema):
        response = make_tool_response("report_term_typings", {"typings": []})
        results = await term_typing.type_term(
            MockLLM(response), pokemon_schema, "Unknown", None, 3
        )
        assert results == []


class TestTypeTermLLMFailure:
    """Graceful degradation when LLM call fails."""

    @pytest.mark.asyncio
    async def test_llm_exception_returns_empty(self, pokemon_schema):
        class FailingLLM:
            async def complete(self, *args, **kwargs):
                raise RuntimeError("API down")

        results = await term_typing.type_term(
            FailingLLM(), pokemon_schema, "Pikachu", None, 3
        )
        assert results == []
