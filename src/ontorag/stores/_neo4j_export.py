"""RDF serialisation helpers for Neo4jStore.dump_graph.

Split out of neo4j.py to keep the adapter module under the 800-line cap.
Each helper takes the SPO rows produced by ``n10s.rdf.export.cypher`` and
serialises them to a target format. Behaviour is identical to the in-module
versions these replaced.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


def triples_to_ttl(triples: list[dict]) -> bytes:
    """Serialise SPO rows from n10s export as Turtle bytes.

    Args:
        triples: List of dicts with keys s, p, o, isLiteral, literalType,
            literalLang.

    Returns:
        UTF-8 encoded Turtle bytes.
    """
    from rdflib import Graph, Literal, URIRef  # noqa: PLC0415
    from rdflib.namespace import XSD  # noqa: PLC0415

    g = Graph()
    for t in triples:
        subj = URIRef(t["s"])
        pred = URIRef(t["p"])
        if t.get("isLiteral"):
            lang = t.get("literalLang") or ""
            dtype = t.get("literalType") or ""
            if lang and lang != "null":
                obj = Literal(t["o"], lang=lang)
            elif dtype and dtype != "null" and dtype != str(XSD.string):
                obj = Literal(t["o"], datatype=URIRef(dtype))
            else:
                obj = Literal(t["o"])
        else:
            obj = URIRef(t["o"])
        g.add((subj, pred, obj))
    return g.serialize(format="turtle").encode()


def triples_to_xlsx(triples: list[dict], label: str = "data") -> bytes:
    """Serialise SPO rows as an XLSX workbook.

    Args:
        triples: List of dicts with keys s, p, o.
        label: Sheet name.

    Returns:
        XLSX bytes.

    Raises:
        ImportError: If openpyxl is not installed.
    """
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("openpyxl is not installed. Run: uv add openpyxl") from exc

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = label
    ws.append(["Subject", "Predicate", "Object"])
    for t in triples:
        ws.append([t["s"], t["p"], t["o"]])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
