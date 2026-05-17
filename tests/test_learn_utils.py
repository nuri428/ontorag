from __future__ import annotations


from ontorag.learn._utils import mint_uri, primary_namespace
from ontorag.stores.base import SchemaResult


def _schema(namespaces: dict) -> SchemaResult:
    return SchemaResult(
        total_classes=0,
        total_properties=0,
        namespaces=namespaces,
        classes=[],
    )


class TestPrimaryNamespace:
    def test_returns_first_custom_namespace(self):
        schema = _schema(
            {
                "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                "pk": "http://example.org/pokemon#",
            }
        )
        assert primary_namespace(schema) == "http://example.org/pokemon#"

    def test_falls_back_to_urn_when_all_standard(self):
        schema = _schema(
            {
                "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
                "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            }
        )
        assert primary_namespace(schema) == "urn:ontorag:learned:"

    def test_empty_namespaces_returns_fallback(self):
        schema = _schema({})
        assert primary_namespace(schema) == "urn:ontorag:learned:"


class TestMintUri:
    def test_simple_term(self):
        schema = _schema({"pk": "http://example.org/pokemon#"})
        uri = mint_uri("Pikachu", schema)
        assert uri == "http://example.org/pokemon#Pikachu"

    def test_term_with_spaces_slugified(self):
        schema = _schema({"pk": "http://example.org/pokemon#"})
        uri = mint_uri("Fire Type", schema)
        assert uri == "http://example.org/pokemon#Fire_Type"

    def test_term_with_special_chars_stripped(self):
        schema = _schema({"pk": "http://example.org/pokemon#"})
        uri = mint_uri("Pikachu!", schema)
        assert uri == "http://example.org/pokemon#Pikachu"
