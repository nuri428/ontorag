"""SPARQL result-set equivalence — metric for SPARQL Correctness.

Two SPARQL queries are considered equivalent here iff their result sets
(when executed against the same graph) match as unordered multisets of
rows, with each row compared as a sorted tuple of values. This:

* tolerates differences in SELECT variable names (?b vs ?buddha),
* tolerates differences in WHERE-clause ordering,
* tolerates surface variations that produce identical results,
* but does *not* tolerate different result cardinalities or values.

For partial-credit scoring (e.g. when an LLM-produced query overlaps but
does not exactly match the gold query), use ``sparql_result_jaccard``.

These functions never raise on query execution failure — they treat
errors as "empty result", which yields a 0.0 Jaccard score against any
non-empty reference. Callers can pre-validate SPARQL syntax separately
(see ``ontorag.eval.goldset.GoldsetQuestion.prepared_query``).
"""

from __future__ import annotations

import logging
from typing import Iterable

from rdflib import Graph
from rdflib.query import Result

logger = logging.getLogger(__name__)


SparqlResultSet = frozenset[tuple[str, ...]]
"""Canonical representation of a SPARQL result set.

Each row is a sorted tuple of stringified values; the whole set is a
frozenset of such tuples. Variable names are deliberately discarded so
that semantically equivalent queries with different SELECT aliases
compare as equal.
"""


def _row_signature(row: Iterable) -> tuple[str, ...]:
    """Map a SPARQL result row to a variable-name-agnostic signature.

    Each binding is converted to a string and the bindings are sorted, so
    column order and variable naming do not affect the signature. ``None``
    bindings (OPTIONAL with no match) map to the sentinel ``<unbound>``.
    """
    return tuple(
        sorted(str(value) if value is not None else "<unbound>" for value in row)
    )


def _result_to_set(result: Result) -> SparqlResultSet:
    """Convert an rdflib Result into a canonical SparqlResultSet."""
    rows: list[tuple[str, ...]] = []
    for row in result:
        try:
            rows.append(_row_signature(row))
        except TypeError:
            # Non-iterable single value (rare; defensive)
            rows.append((str(row),))
    return frozenset(rows)


def _safe_query(sparql: str, graph: Graph) -> SparqlResultSet:
    """Execute a SPARQL query and return a result set; on error return empty set."""
    try:
        result = graph.query(sparql)
    except Exception as e:  # noqa: BLE001 — rdflib raises a range of exceptions
        logger.debug("SPARQL execution failed: %s", e)
        return frozenset()
    return _result_to_set(result)


def sparql_result_equivalent(
    query_a: str,
    query_b: str,
    graph: Graph,
) -> bool:
    """Return True iff the two SPARQL queries produce identical result sets.

    Equivalence is judged by variable-name-agnostic row signatures (see
    ``_row_signature``). Two queries that return the same triples but with
    columns in a different order, or with different variable names, are
    considered equivalent.

    Args:
        query_a: First SPARQL query (typically the gold query).
        query_b: Second SPARQL query (typically the system-produced query).
        graph: The RDF graph to execute both queries against. Must be the
            same instance for both to ensure inference state is identical.

    Returns:
        True when the result sets are exactly equal.
    """
    return _safe_query(query_a, graph) == _safe_query(query_b, graph)


def sparql_result_jaccard(
    query_a: str,
    query_b: str,
    graph: Graph,
) -> float:
    """Jaccard similarity of the two queries' result sets, in [0, 1].

    Useful when an LLM-produced query partially overlaps with the gold
    query — e.g. it found some of the correct entities but missed others,
    or hallucinated extra rows.

    Edge cases:
        * Both empty → 1.0 (vacuous match — both queries returned nothing,
          which can be a *correct* outcome for hallucination-trap goldset
          rows).
        * One empty, one not → 0.0.
    """
    set_a = _safe_query(query_a, graph)
    set_b = _safe_query(query_b, graph)
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:  # defensive; cannot happen if either set is non-empty
        return 0.0
    return len(intersection) / len(union)
