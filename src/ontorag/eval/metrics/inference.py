"""Inference Utilization — does the system exploit OWL/RDFS reasoning?

This metric measures whether the system's retrieval relies on inferred
triples (e.g. transitive closure via ``owl:TransitiveProperty``, class
membership via ``rdfs:subClassOf``) versus only explicit assertions.

Two complementary measurements are provided:

* :func:`inference_utilization_score` — compares results obtained with
  reasoning enabled vs disabled. The fraction that depends on reasoning
  is the metric.
* :func:`system_uses_inference_features` — pure-syntactic check of the
  system-produced SPARQL for features that *imply* inference reliance
  (SPARQL 1.1 property paths ``+`` / ``*``).

Both return ``None`` when the question is not flagged ``uses_inference``,
signalling "not applicable" to aggregators.
"""

from __future__ import annotations

import re

from rdflib import Graph

from ontorag.eval.goldset import GoldsetQuestion
from ontorag.eval.metrics.sparql_eq import _safe_query


PROPERTY_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z_])"          # not preceded by an identifier char
    r"[a-zA-Z_][\w:]*"          # predicate identifier (CURIE or local)
    r"[+*?]"                    # quantifier suffix
)


def inference_utilization_score(
    question: GoldsetQuestion,
    graph_with_inference: Graph,
    graph_without_inference: Graph,
) -> float | None:
    """Fraction of gold-query result rows that require inference to obtain.

    Args:
        question: The goldset question being evaluated.
        graph_with_inference: rdflib Graph with OWL/RDFS reasoning enabled
            (e.g. a Fuseki dataset configured with ``ja:OntModelSpec``).
        graph_without_inference: Same content with reasoning disabled —
            asserted triples only.

    Returns:
        * 1.0 — every gold answer row depends on inference
        * 0.0 — no gold answer row depends on inference (all explicit)
        * 0 < x < 1 — partial dependence
        * None — question is not an inference-required question
    """
    if not question.uses_inference:
        return None

    with_inf = _safe_query(question.gold_sparql, graph_with_inference)
    without_inf = _safe_query(question.gold_sparql, graph_without_inference)

    if not with_inf:
        return 0.0
    inferred_only = with_inf - without_inf
    return len(inferred_only) / len(with_inf)


def system_uses_inference_features(system_sparql: str) -> bool:
    """True if the system's SPARQL uses constructs that imply inference.

    Currently detects SPARQL 1.1 property paths (``+``, ``*``, ``?``) on
    predicate positions — these can replace OWL transitive closure when
    the underlying graph does not have reasoning enabled. A system using
    property paths is demonstrating awareness that traversal is needed
    even if it cannot rely on a reasoner.

    Note: This is a syntactic check. A system that uses property paths
    on a goldset question that requires no inference will still score
    True here — interpret in combination with the question metadata.
    """
    return bool(PROPERTY_PATH_PATTERN.search(system_sparql))
