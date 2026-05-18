"""Hallucinated Triples — system claims that do not exist in the KG.

A hallucinated triple is one the system asserts in its output but which
is not present in the reference graph (neither explicit nor inferred,
depending on the graph's reasoning configuration).

This metric is ontorag-specific: vector RAG produces unstructured text
answers and cannot easily be checked against a KG at the triple level.
For ontology RAG, the system's tool outputs and citations are triple-
structured, so hallucination can be measured exactly.

Two callable forms:

* :func:`hallucinated_triple_count` — returns counts (no division).
* :func:`hallucination_rate` — returns the rate in [0, 1].
"""

from __future__ import annotations

from typing import TypeAlias

from rdflib import Graph, Literal, URIRef
from rdflib.term import Node


TripleLike: TypeAlias = tuple[Node | str, Node | str, Node | str]


def _coerce_term(term: Node | str) -> Node:
    """Convert a string subject/predicate/object to an rdflib Node.

    Strings that look like URIs (start with http://, https://, urn:, or
    contain a colon followed by a path) are coerced to URIRef; everything
    else becomes a Literal. rdflib Node instances pass through unchanged.
    """
    if isinstance(term, (URIRef, Literal)):
        return term
    if isinstance(term, str):
        s = term.strip()
        if s.startswith("<") and s.endswith(">"):
            s = s[1:-1]
        if (
            s.startswith(("http://", "https://", "urn:", "file:"))
            or (":" in s and not s.startswith('"'))
        ):
            return URIRef(s)
        return Literal(s)
    raise TypeError(f"Cannot coerce {type(term).__name__} to rdflib term")


def _triple_exists(triple: TripleLike, graph: Graph) -> bool:
    """Test whether a triple is in the graph (asserted or inferred)."""
    try:
        s, p, o = (_coerce_term(t) for t in triple)
    except (TypeError, ValueError):
        return False
    return (s, p, o) in graph


def hallucinated_triple_count(
    claimed_triples: list[TripleLike] | set[TripleLike],
    reference_graph: Graph,
) -> dict[str, int]:
    """Count how many claimed triples are absent from the reference graph.

    Args:
        claimed_triples: Triples the system asserts as evidence for its
            answer. Each triple is a (subject, predicate, object) tuple
            of rdflib Nodes or strings (auto-coerced).
        reference_graph: The ground-truth KG.

    Returns:
        ``{'total': N, 'hallucinated': K, 'grounded': N-K}``.
        For an empty claim set: ``{'total': 0, 'hallucinated': 0,
        'grounded': 0}``.
    """
    seen: set[TripleLike] = set()
    hallucinated = 0
    for triple in claimed_triples:
        if triple in seen:
            continue
        seen.add(triple)
        if not _triple_exists(triple, reference_graph):
            hallucinated += 1
    total = len(seen)
    return {
        "total": total,
        "hallucinated": hallucinated,
        "grounded": total - hallucinated,
    }


def hallucination_rate(
    claimed_triples: list[TripleLike] | set[TripleLike],
    reference_graph: Graph,
) -> float:
    """Fraction of claimed triples that are hallucinated, in [0, 1].

    Returns 0.0 when no triples were claimed — interpret as "no claims,
    no hallucination". Aggregators may wish to weight this case
    differently (e.g. report alongside total claim count).
    """
    counts = hallucinated_triple_count(claimed_triples, reference_graph)
    if counts["total"] == 0:
        return 0.0
    return counts["hallucinated"] / counts["total"]
