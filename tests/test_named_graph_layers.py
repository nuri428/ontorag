"""v0.7.0 named-graph layer infrastructure.

Covers the OntologyLayer enum, the LAYER_GRAPH_URI map, layer_graph_uri across
both scoping dimensions, the schema/data backward-compat aliases, and the
opt-in inference assembler template. See docs/design/named-graph-layers.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph

from ontorag.core.ontology import (
    DEFAULT_DATA_GRAPH,
    DEFAULT_SCHEMA_GRAPH,
    LAYER_GRAPH_URI,
    OntologyLayer,
    data_graph_uri,
    layer_graph_uri,
    resolve_layer,
    schema_graph_uri,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ── OntologyLayer enum ────────────────────────────────────────────────────────


def test_layer_members_are_the_v07_vocabulary():
    assert {m.value for m in OntologyLayer} == {
        "semantic",
        "policy",
        "state",
        "provenance",
    }


def test_layer_is_str_mixin():
    # str mixin → interpolates as its value without .value ceremony
    assert f"{OntologyLayer.policy}" == "policy" or OntologyLayer.policy == "policy"
    assert OntologyLayer.semantic == "semantic"


# ── LAYER_GRAPH_URI + backward-compat invariants ─────────────────────────────


def test_layer_graph_uri_map_values():
    assert LAYER_GRAPH_URI[OntologyLayer.semantic] == "urn:ontorag:schema"
    assert LAYER_GRAPH_URI[OntologyLayer.state] == "urn:ontorag:data"
    assert LAYER_GRAPH_URI[OntologyLayer.policy] == "urn:ontorag:policy"
    assert LAYER_GRAPH_URI[OntologyLayer.provenance] == "urn:ontorag:provenance"


def test_legacy_layers_keep_physical_uris():
    # The whole point of "rename for backward compat": physical URIs unchanged.
    assert LAYER_GRAPH_URI[OntologyLayer.semantic] == DEFAULT_SCHEMA_GRAPH
    assert LAYER_GRAPH_URI[OntologyLayer.state] == DEFAULT_DATA_GRAPH
    assert DEFAULT_SCHEMA_GRAPH == "urn:ontorag:schema"
    assert DEFAULT_DATA_GRAPH == "urn:ontorag:data"


def test_map_covers_every_layer():
    assert set(LAYER_GRAPH_URI) == set(OntologyLayer)


# ── resolve_layer ─────────────────────────────────────────────────────────────


def test_resolve_layer_aliases():
    assert resolve_layer("schema") is OntologyLayer.semantic
    assert resolve_layer("data") is OntologyLayer.state


def test_resolve_layer_canonical_names():
    for member in OntologyLayer:
        assert resolve_layer(member.value) is member


def test_resolve_layer_passes_through_enum():
    assert resolve_layer(OntologyLayer.provenance) is OntologyLayer.provenance


def test_resolve_layer_is_case_and_whitespace_insensitive():
    assert resolve_layer("  SEMANTIC ") is OntologyLayer.semantic
    assert resolve_layer("Schema") is OntologyLayer.semantic


@pytest.mark.parametrize("bad", ["", "foo", "tbox", "abox", "semantics", "states"])
def test_resolve_layer_rejects_unknown(bad):
    with pytest.raises(ValueError, match="Unknown ontology layer"):
        resolve_layer(bad)


# ── layer_graph_uri across both dimensions ───────────────────────────────────


def test_layer_graph_uri_default_scope():
    assert layer_graph_uri(None, OntologyLayer.semantic) == "urn:ontorag:schema"
    assert layer_graph_uri(None, OntologyLayer.state) == "urn:ontorag:data"
    assert layer_graph_uri(None, OntologyLayer.policy) == "urn:ontorag:policy"
    assert layer_graph_uri(None, OntologyLayer.provenance) == "urn:ontorag:provenance"


def test_layer_graph_uri_named_scope():
    assert layer_graph_uri("pokemon", OntologyLayer.semantic) == "urn:ontorag:pokemon:schema"
    assert layer_graph_uri("pokemon", OntologyLayer.state) == "urn:ontorag:pokemon:data"
    assert layer_graph_uri("pokemon", OntologyLayer.policy) == "urn:ontorag:pokemon:policy"
    assert (
        layer_graph_uri("pokemon", OntologyLayer.provenance)
        == "urn:ontorag:pokemon:provenance"
    )


def test_layer_graph_uri_accepts_alias_strings():
    assert layer_graph_uri(None, "schema") == "urn:ontorag:schema"
    assert layer_graph_uri("foaf", "data") == "urn:ontorag:foaf:data"


def test_layer_graph_uri_rejects_unsafe_ontology_id():
    with pytest.raises(ValueError, match="Invalid ontology id"):
        layer_graph_uri("evil} GRAPH <x>", OntologyLayer.semantic)


def test_layer_graph_uri_rejects_unknown_layer():
    with pytest.raises(ValueError, match="Unknown ontology layer"):
        layer_graph_uri("pokemon", "bogus")


# ── legacy wrappers derive from the layer primitive ───────────────────────────


@pytest.mark.parametrize("ontology", [None, "pokemon", "foaf-2"])
def test_legacy_wrappers_match_layer_primitive(ontology):
    assert schema_graph_uri(ontology) == layer_graph_uri(ontology, OntologyLayer.semantic)
    assert data_graph_uri(ontology) == layer_graph_uri(ontology, OntologyLayer.state)


# ── base.py re-export is the same object ──────────────────────────────────────


def test_base_reexports_match_core():
    from ontorag.stores import base

    assert base.OntologyLayer is OntologyLayer
    assert base.LAYER_GRAPH_URI is LAYER_GRAPH_URI


# ── Fuseki inference assembler template ───────────────────────────────────────


def _render(template_path: Path) -> str:
    return template_path.read_text().replace("__FUSEKI_DATASET__", "ontorag")


def test_inference_template_is_valid_turtle():
    path = _REPO_ROOT / "docker/fuseki/config-inference.ttl.template"
    Graph().parse(data=_render(path), format="turtle")  # raises on bad TTL


def test_inference_template_uses_union_graph_and_owl_reasoner():
    txt = (_REPO_ROOT / "docker/fuseki/config-inference.ttl.template").read_text()
    assert "urn:x-arq:UnionGraph" in txt
    assert "OWLMicroFBRuleReasoner" in txt
    assert "ja:InfModel" in txt


def test_both_templates_reference_fixed_named_graph_uris():
    for name in ("config.ttl.template", "config-inference.ttl.template"):
        txt = (_REPO_ROOT / "docker/fuseki" / name).read_text()
        assert "urn:ontorag:schema" in txt
        assert "urn:ontorag:data" in txt


def test_inference_template_shares_tdb_location_with_default():
    # Same TDB2 location → flip inference on/off without reloading data.
    txt = (_REPO_ROOT / "docker/fuseki/config-inference.ttl.template").read_text()
    assert 'tdb2:location "/fuseki/databases/__FUSEKI_DATASET__"' in txt


def test_templates_synced_to_scaffolding():
    # docker/fuseki/* must match the _templates/ scaffolding copies shipped to
    # users, so `ontorag init` projects get the inference option too.
    for name in ("Dockerfile", "config-inference.ttl.template"):
        canonical = (_REPO_ROOT / "docker/fuseki" / name).read_text()
        scaffold = (
            _REPO_ROOT / "src/ontorag/_templates/docker/fuseki" / name
        ).read_text()
        assert canonical == scaffold, f"{name} drifted from its _templates copy"
