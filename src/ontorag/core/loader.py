from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from rdflib import OWL, RDF, RDFS, Graph, URIRef

logger = logging.getLogger(__name__)

# rdf:type 목적어로 나타나면 TBox 선언으로 간주
_SCHEMA_TYPES: frozenset[URIRef] = frozenset({
    OWL.Class,
    OWL.ObjectProperty,
    OWL.DatatypeProperty,
    OWL.AnnotationProperty,
    OWL.Ontology,
})

# 이 술어가 존재하면 TBox 구조
_SCHEMA_PREDICATES: frozenset[URIRef] = frozenset({
    RDFS.subClassOf,
    RDFS.subPropertyOf,
    RDFS.domain,
    RDFS.range,
    OWL.equivalentClass,
    OWL.disjointWith,
})


def detect_mode(graph: Graph) -> Literal["schema", "data"]:
    """Heuristically detect whether a graph is TBox (schema) or ABox (data).

    Checks for OWL class/property type declarations and structural RDFS predicates.

    Args:
        graph: Parsed RDF graph to inspect.

    Returns:
        "schema" if ontology declarations are found, "data" otherwise.
    """
    for schema_type in _SCHEMA_TYPES:
        if (None, RDF.type, schema_type) in graph:
            return "schema"
    for predicate in _SCHEMA_PREDICATES:
        if next(graph.triples((None, predicate, None)), None) is not None:
            return "schema"
    return "data"


def parse_rdf(path: str | Path) -> Graph:
    """Parse an RDF file into an rdflib Graph.

    Format is auto-detected by rdflib from file extension and content.
    Supported extensions: .ttl (Turtle), .jsonld (JSON-LD), .rdf / .owl (RDF/XML), .n3.

    Args:
        path: File path to parse.

    Returns:
        Populated RDF graph.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"RDF file not found: {path}")

    graph = Graph()
    graph.parse(str(path))
    logger.debug("Parsed %d triples from %s", len(graph), path.name)
    return graph
