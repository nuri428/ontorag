"""Citation Coverage — does the answer's text trace back to cited triples?

In ontorag, an "answer" consists of (a) natural-language text and (b) a
list of triple URIs the system claims it used. Citation coverage
measures the link between the two: for each substantive claim in the
answer, can we find a cited triple whose terms (URIs, literals) appear
in the answer text?

This is a token-overlap heuristic — it cannot detect semantically
correct but lexically divergent paraphrases. For a stricter check use
LLM-as-judge (the RAGAS Faithfulness metric, see ``ragas_wrapper.py``).
The heuristic is cheap and deterministic; it suits CI gates and
regression detection where LLM-as-judge cost is prohibitive.
"""

from __future__ import annotations

import re

from rdflib.term import Literal, Node, URIRef


_LOCAL_NAME_SEP = re.compile(r"[#/]")
# Use \W (non-word chars) so punctuation also acts as a separator —
# critical for matching answers like "Buddha;" or "Amitabha, the buddha".
# In re.UNICODE mode (default), \w matches Korean and CJK characters too.
_TOKEN_SEP = re.compile(r"\W+", re.UNICODE)


def _terms_of(triple: tuple[Node, Node, Node]) -> list[str]:
    """Return human-readable surface forms for each term in a triple.

    URIs contribute their local name (after the last ``#`` or ``/``).
    Literals contribute their lexical form. The result is lowercased
    and stripped of common URI scheme prefixes.
    """
    surfaces: list[str] = []
    for term in triple:
        if isinstance(term, URIRef):
            local = _LOCAL_NAME_SEP.split(str(term))[-1]
            surfaces.append(local.lower())
        elif isinstance(term, Literal):
            surfaces.append(str(term).lower())
        else:
            surfaces.append(str(term).lower())
    return surfaces


def _tokenise(text: str) -> set[str]:
    """Lowercase tokenisation; splits on whitespace, ``_`` and ``-``."""
    return {tok for tok in _TOKEN_SEP.split(text.lower()) if tok}


def _triple_token_set(triple: tuple[Node, Node, Node]) -> set[str]:
    """All tokens extracted from a triple's three terms."""
    tokens: set[str] = set()
    for surface in _terms_of(triple):
        tokens.update(_tokenise(surface))
    return tokens


def triple_supports_answer(
    triple: tuple[Node, Node, Node],
    answer_text: str,
    min_overlap: float = 0.5,
) -> bool:
    """True if at least ``min_overlap`` of the triple's tokens appear in the answer.

    The default 0.5 threshold means the answer text must mention at
    least half of the triple's distinct terms. The intent is: if a
    triple is genuinely cited, the answer text should reference its
    subject and object (or close paraphrases).
    """
    triple_tokens = _triple_token_set(triple)
    if not triple_tokens:
        return False
    answer_tokens = _tokenise(answer_text)
    overlap = len(triple_tokens & answer_tokens)
    return (overlap / len(triple_tokens)) >= min_overlap


def citation_coverage(
    answer_text: str,
    cited_triples: list[tuple[Node, Node, Node]],
    min_overlap: float = 0.5,
) -> float:
    """Fraction of cited triples whose terms are referenced by the answer text.

    Args:
        answer_text: The system's natural-language answer.
        cited_triples: Triples the system claims were used to derive the
            answer (returned via SSE ``citation`` events or in API output).
        min_overlap: Per-triple threshold (default 0.5). A triple counts
            as "supported by the answer" if at least this fraction of
            its tokens appears in the answer text.

    Returns:
        Fraction in [0, 1]. Returns 1.0 for empty ``cited_triples``
        (vacuously perfect — there's nothing the answer needs to support).
    """
    if not cited_triples:
        return 1.0
    supported = sum(
        1 for t in cited_triples if triple_supports_answer(t, answer_text, min_overlap)
    )
    return supported / len(cited_triples)
