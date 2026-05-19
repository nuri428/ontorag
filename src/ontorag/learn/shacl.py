from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD, Namespace

logger = logging.getLogger(__name__)

SH = Namespace("http://www.w3.org/ns/shacl#")


@dataclass(frozen=True)
class ShaclViolation:
    focus_node: str
    result_path: str | None
    severity: str
    message: str
    source_shape: str | None
    source_constraint: str | None
    value: str | None


def _local_severity(uri: URIRef | None) -> str:
    if uri is None:
        return "Violation"
    return str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def validate(
    data_graph: Graph,
    shapes_path: str | Path,
) -> tuple[Graph, list[ShaclViolation]]:
    """Validate a data graph against SHACL shapes; return (kept_graph, violations).

    Triples whose (subject, predicate) pair matches any violation's
    (focus_node, result_path) are removed from the kept graph. Triples flagged
    by node-level shapes (no result_path) cause every triple with that subject
    to be removed.
    """
    from pyshacl import validate as _pyshacl_validate

    shapes_path = Path(shapes_path)
    shapes_graph = Graph()
    shapes_graph.parse(shapes_path, format="turtle")

    conforms, results_graph, _ = _pyshacl_validate(
        data_graph=data_graph,
        shacl_graph=shapes_graph,
        inference="none",
        abort_on_first=False,
        allow_warnings=True,
    )

    if conforms:
        return data_graph, []

    violations: list[ShaclViolation] = []
    bad_pairs: set[tuple[URIRef, URIRef | None]] = set()

    for report in results_graph.subjects(predicate=SH.result, object=None):
        for result_node in results_graph.objects(subject=report, predicate=SH.result):
            focus = results_graph.value(result_node, SH.focusNode)
            path = results_graph.value(result_node, SH.resultPath)
            severity = results_graph.value(result_node, SH.resultSeverity)
            message = results_graph.value(result_node, SH.resultMessage)
            shape = results_graph.value(result_node, SH.sourceShape)
            constraint = results_graph.value(result_node, SH.sourceConstraintComponent)
            value = results_graph.value(result_node, SH.value)

            if focus is None:
                continue

            violations.append(
                ShaclViolation(
                    focus_node=str(focus),
                    result_path=str(path) if path else None,
                    severity=_local_severity(severity),
                    message=str(message) if message else "",
                    source_shape=str(shape) if shape else None,
                    source_constraint=str(constraint) if constraint else None,
                    value=str(value) if value else None,
                )
            )
            if isinstance(focus, URIRef):
                bad_pairs.add((focus, path if isinstance(path, URIRef) else None))

    kept = Graph()
    for prefix, ns in data_graph.namespaces():
        kept.bind(prefix, ns)

    bad_subjects_any_pred = {s for s, p in bad_pairs if p is None}
    bad_subj_pred = {(s, p) for s, p in bad_pairs if p is not None}

    for s, p, o in data_graph:
        if s in bad_subjects_any_pred:
            continue
        if (s, p) in bad_subj_pred:
            continue
        kept.add((s, p, o))

    logger.info(
        "SHACL: %d violation(s); kept %d/%d triples",
        len(violations),
        len(kept),
        len(data_graph),
    )
    return kept, violations


def derive_from_owl(schema_path: str | Path) -> str:
    """Generate a SHACL skeleton from an OWL TBox.

    Maps three OWL idioms to SHACL constraints:
      - rdfs:range <xsd-type>      → sh:datatype
      - rdfs:range <Class>         → sh:class + sh:nodeKind sh:IRI
      - owl:FunctionalProperty     → sh:maxCount 1

    Properties without rdfs:domain are skipped (no shape target).
    Domain knowledge (enumerations, value ranges, cardinality > 1) is NOT
    derivable from OWL alone and must be added by hand after generation.

    Args:
        schema_path: Path to OWL schema (Turtle).

    Returns:
        SHACL shapes graph as Turtle string.
    """
    schema = Graph()
    schema.parse(Path(schema_path), format="turtle")

    out = Graph()
    for prefix, ns in schema.namespaces():
        out.bind(prefix, ns)
    out.bind("sh", SH)

    shapes_by_class: dict[URIRef, URIRef] = {}

    def _shape_for(cls: URIRef) -> URIRef:
        if cls not in shapes_by_class:
            shape_uri = URIRef(str(cls) + "Shape")
            out.add((shape_uri, RDF.type, SH.NodeShape))
            out.add((shape_uri, SH.targetClass, cls))
            shapes_by_class[cls] = shape_uri
        return shapes_by_class[cls]

    all_props = set(schema.subjects(RDF.type, OWL.DatatypeProperty)) | set(
        schema.subjects(RDF.type, OWL.ObjectProperty)
    )

    for prop in sorted(all_props, key=str):
        domains = list(schema.objects(prop, RDFS.domain))
        if not domains:
            logger.debug("derive_from_owl: skipping %s (no rdfs:domain)", prop)
            continue

        ranges = list(schema.objects(prop, RDFS.range))
        is_datatype = (prop, RDF.type, OWL.DatatypeProperty) in schema
        is_functional = (prop, RDF.type, OWL.FunctionalProperty) in schema

        for dom in domains:
            if not isinstance(dom, URIRef):
                continue
            shape_uri = _shape_for(dom)
            prop_shape = BNode()
            out.add((shape_uri, SH.property, prop_shape))
            out.add((prop_shape, SH.path, prop))

            for rng in ranges:
                if not isinstance(rng, URIRef):
                    continue
                if is_datatype or str(rng).startswith(str(XSD)):
                    out.add((prop_shape, SH.datatype, rng))
                else:
                    out.add((prop_shape, SH["class"], rng))
                    out.add((prop_shape, SH.nodeKind, SH.IRI))

            if is_functional:
                out.add((prop_shape, SH.maxCount, Literal(1)))

    return out.serialize(format="turtle")
