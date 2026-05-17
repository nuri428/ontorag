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

    def test_object_uri_triple_serialized(self, pokemon_schema):
        """Line 89: object_uri branch in _serialize_to_ttl."""
        triples = [
            ExtractedTriple(
                subject_label="Pikachu",
                subject_uri="http://example.org/pokemon#Pikachu",
                predicate_uri="http://example.org/pokemon#hasType",
                object_uri="http://example.org/pokemon#Electric",
                confidence=0.9,
            )
        ]
        ttl = _serialize_to_ttl(triples, [], pokemon_schema)
        assert "Electric" in ttl

    def test_rdflib_import_error_raises(self, pokemon_schema, monkeypatch):
        """Lines 63-64: ImportError when rdflib is unavailable."""
        import builtins
        real_import = builtins.__import__

        def _block_rdflib(name, *args, **kwargs):
            if name == "rdflib":
                raise ImportError("rdflib not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_rdflib)
        with pytest.raises(ImportError, match="rdflib is required"):
            _serialize_to_ttl([], [], pokemon_schema)

    def test_bad_namespace_uri_skipped(self, pokemon_schema):
        """Lines 72-73: malformed namespace URI is logged and skipped."""
        from ontorag.stores.base import SchemaResult
        bad_schema = SchemaResult(
            total_classes=0,
            total_properties=0,
            namespaces={"bad": "not a valid uri !!"},
            classes=[],
        )
        # Should not raise — bad namespace is skipped with logger.debug
        ttl = _serialize_to_ttl([], [], bad_schema)
        assert isinstance(ttl, str)


class TestLLMOntologyLearnerDiscoverTaxonomy:
    @pytest.mark.asyncio
    async def test_discover_taxonomy_delegates(self, pokemon_schema):
        """Lines 123-124: discover_taxonomy delegates to taxonomy module."""
        from tests.conftest import MockLLM, MockGraphStore, make_tool_response

        response = make_tool_response("report_taxonomy_relations", {
            "relations": [
                {
                    "child_term": "FirePokemon",
                    "parent_uri": "http://example.org/pokemon#Pokemon",
                    "confidence": 0.88,
                }
            ]
        })
        learner = LLMOntologyLearner(MockGraphStore(pokemon_schema), MockLLM(response))
        results = await learner.discover_taxonomy("Charmander is a Fire-type Pokemon.")

        assert len(results) == 1
        assert results[0].child_term == "FirePokemon"


class TestExtractTermsFailure:
    @pytest.mark.asyncio
    async def test_extract_terms_failure_returns_empty(self, pokemon_schema):
        """Lines 198-200: _extract_terms logs warning and returns [] on failure."""
        from tests.conftest import MockGraphStore

        class _FailingLLM:
            async def complete(self, *args, **kwargs):
                raise RuntimeError("LLM unavailable")

        learner = LLMOntologyLearner(MockGraphStore(pokemon_schema), _FailingLLM())
        # populate_from_text calls _extract_terms; failure should not propagate
        result = await learner.populate_from_text("some text", auto_load=False)
        assert result.term_typings == []
        assert result.triples == []
