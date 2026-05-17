from __future__ import annotations

import pytest

from ontorag.learn.pipeline import LLMOntologyLearner, _serialize_to_ttl
from ontorag.learn.base import ExtractedTriple, TermTypingResult
from ontorag.llm.base import _CompletionMessage, _ToolUseBlock
from tests.conftest import MockGraphStore, make_tool_response


def _multi_call_llm(*responses):
    """LLM mock that returns responses in order, cycling on the last one."""
    class _MultiCallLLM:
        def __init__(self):
            self._idx = 0
            self.calls = []

        async def complete(self, messages, tools, system=None, force_tool_use=False, force_tool_name=None):
            self.calls.append(force_tool_name)
            resp = responses[min(self._idx, len(responses) - 1)]
            self._idx += 1
            return resp

    return _MultiCallLLM()


class TestLLMOntologyLearnerTypeterm:
    @pytest.mark.asyncio
    async def test_type_term_delegates_to_term_typing(self, pokemon_schema):
        response = make_tool_response("report_term_typings", {
            "typings": [{"class_uri": "http://example.org/pokemon#Pokemon", "confidence": 0.9}]
        })
        store = MockGraphStore(pokemon_schema)
        learner = LLMOntologyLearner(store, _multi_call_llm(response))

        results = await learner.type_term("Pikachu")
        assert results[0].class_uri == "http://example.org/pokemon#Pokemon"

    @pytest.mark.asyncio
    async def test_extract_relations_delegates(self, pokemon_schema):
        response = make_tool_response("report_triples", {"triples": []})
        store = MockGraphStore(pokemon_schema)
        learner = LLMOntologyLearner(store, _multi_call_llm(response))

        results = await learner.extract_relations("some text")
        assert results == []


class TestPopulateFromText:
    @pytest.mark.asyncio
    async def test_populate_dry_run_does_not_load(self, pokemon_schema):
        term_resp = make_tool_response("report_entity_terms", {"terms": ["Pikachu"]})
        typing_resp = make_tool_response("report_term_typings", {
            "typings": [{"class_uri": "http://example.org/pokemon#Pokemon", "confidence": 0.9}]
        })
        taxonomy_resp = make_tool_response("report_taxonomy_relations", {"relations": []})
        relation_resp = make_tool_response("report_triples", {"triples": []})

        store = MockGraphStore(pokemon_schema)
        llm = _multi_call_llm(term_resp, typing_resp, taxonomy_resp, relation_resp)
        learner = LLMOntologyLearner(store, llm)

        result = await learner.populate_from_text("Pikachu is a Pokemon.", auto_load=False)

        assert result.triples_loaded is None
        assert len(store.load_calls) == 0

    @pytest.mark.asyncio
    async def test_populate_auto_load_calls_store(self, pokemon_schema):
        term_resp = make_tool_response("report_entity_terms", {"terms": ["Pikachu"]})
        typing_resp = make_tool_response("report_term_typings", {
            "typings": [{"class_uri": "http://example.org/pokemon#Pokemon", "confidence": 0.9}]
        })
        taxonomy_resp = make_tool_response("report_taxonomy_relations", {"relations": []})
        relation_resp = make_tool_response("report_triples", {"triples": []})

        store = MockGraphStore(pokemon_schema)
        llm = _multi_call_llm(term_resp, typing_resp, taxonomy_resp, relation_resp)
        learner = LLMOntologyLearner(store, llm)

        result = await learner.populate_from_text("Pikachu is a Pokemon.", auto_load=True)

        assert len(store.load_calls) == 1
        assert result.triples_loaded == 5

    @pytest.mark.asyncio
    async def test_confidence_filtering(self, pokemon_schema):
        term_resp = make_tool_response("report_entity_terms", {"terms": ["X"]})
        typing_resp = make_tool_response("report_term_typings", {
            "typings": [{"class_uri": "http://example.org/pokemon#Pokemon", "confidence": 0.3}]
        })
        taxonomy_resp = make_tool_response("report_taxonomy_relations", {"relations": []})
        relation_resp = make_tool_response("report_triples", {"triples": []})

        store = MockGraphStore(pokemon_schema)
        llm = _multi_call_llm(term_resp, typing_resp, taxonomy_resp, relation_resp)
        learner = LLMOntologyLearner(store, llm)

        result = await learner.populate_from_text("X.", auto_load=False, min_confidence=0.7)

        assert result.term_typings == []


class TestSerializeToTTL:
    def test_produces_valid_turtle(self, pokemon_schema):
        typings = [
            TermTypingResult(
                term="Pikachu",
                class_uri="http://example.org/pokemon#Pokemon",
                label="Pokemon",
                confidence=0.9,
            )
        ]
        triples = [
            ExtractedTriple(
                subject_label="Pikachu",
                subject_uri=None,
                predicate_uri="http://example.org/pokemon#hp",
                object_value="35",
                confidence=0.85,
            )
        ]
        ttl = _serialize_to_ttl(triples, typings, pokemon_schema)

        assert "rdf:type" in ttl or "a " in ttl
        assert "Pikachu" in ttl

    def test_empty_inputs_produces_minimal_turtle(self, pokemon_schema):
        ttl = _serialize_to_ttl([], [], pokemon_schema)
        assert isinstance(ttl, str)
