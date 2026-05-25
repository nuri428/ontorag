"""Translate PatternQuery DSL to Cypher (symmetric to pattern_to_sparql).

Used by Neo4jStore.query_pattern — same PatternQuery validation prevents
Cypher injection (variables, URI, prefixed name, or literal only).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ontorag.stores.base import PatternFilter, PatternQuery, PatternTriple

# Matches SPARQL/Turtle prefixed names like  rdf:type  pk:Pokemon
_PREFIXED_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_\-]*):(.*)")

# Matches full URIs wrapped in angle brackets <http://...>
_URI_RE = re.compile(r"^<([^<>]+)>$")

# SPARQL variable  ?foo
_VAR_RE = re.compile(r"^\?[a-zA-Z][a-zA-Z0-9_]*$")

# Numeric literals (int or float)
_NUM_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")

# Safe n10s-shortened Cypher identifier: ``prefix__Local``.  Cypher
# relationship types / labels / property keys are interpolated (not
# parameterizable), so backtick-quoting alone is NOT enough — a backtick in
# the input breaks out of the quoting.  Every rel-type/label/prop-key
# interpolation site MUST route the shortened value through _safe_rel() first.
_SAFE_SHORT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*__[A-Za-z0-9_.\-]+$")


def _safe_rel(short: str) -> str:
    """Validate an n10s-shortened identifier before Cypher interpolation.

    Cypher has no parameter binding for relationship types, labels, or
    property keys, so these are string-interpolated.  Backtick-quoting is
    insufficient because a backtick in the value escapes the quoting.  This
    validator enforces the strict ``prefix__Local`` shape, rejecting anything
    that could break out of a backtick-quoted identifier.

    Args:
        short: A shortened identifier (``prefix__Local``) to interpolate.

    Returns:
        The validated identifier, unchanged.

    Raises:
        ValueError: If the identifier does not match the safe pattern.
    """
    if not _SAFE_SHORT_RE.match(short):
        raise ValueError(f"Unsafe Cypher identifier: {short!r}")
    return short


# ── Public API ────────────────────────────────────────────────────────────────


def pattern_to_cypher(
    query: PatternQuery,
    shorten_fn: Callable[[str], str] | None = None,
    expand_fn: Callable[[str], str] | None = None,
) -> tuple[str, dict]:
    """Translate a validated PatternQuery DSL into a Cypher query string + params.

    Variables become Cypher variables (without the leading ``?``).
    URIs and prefixed names become node-match ``{uri: $p_N}`` params or
    relationship type predicates (``SHORTEN`` form).

    The caller must supply ``shorten_fn`` / ``expand_fn`` so the translator
    can convert full URIs  ↔  ``prefix__local`` without duplicating the
    prefix-map logic owned by ``Neo4jStore``.

    Args:
        query: A validated PatternQuery object from the DSL.
        shorten_fn: ``full_uri -> prefix__local``; identity if None.
        expand_fn: ``prefix__local -> full_uri``; identity if None.

    Returns:
        ``(cypher_string, params_dict)`` — params are bound with ``$p_N`` keys.
    """
    if shorten_fn is None:

        def shorten_fn(s: str) -> str:  # type: ignore[misc]
            return s

    params: dict[str, object] = {}
    param_counter: list[int] = [0]

    def alloc_param(value: object) -> str:
        key = f"p_{param_counter[0]}"
        params[key] = value
        param_counter[0] += 1
        return f"${key}"

    # Accumulate MATCH fragments and WHERE conditions SEPARATELY so that a
    # literal-object triple's filter does not get attached to the wrong MATCH
    # in a multi-triple query (review #4). A single combined WHERE is emitted.
    match_parts: list[str] = []
    where_parts: list[str] = []
    _declared_vars: set[str] = set()

    for triple in query.where:
        match_frag, where_frag = _build_triple_cypher(
            triple,
            shorten_fn,
            alloc_param,
            _declared_vars,
        )
        if match_frag:
            match_parts.append(match_frag)
        if where_frag:
            where_parts.append(where_frag)

    # FILTER → WHERE conditions
    for f in query.filters:
        cypher_var = f.var.lstrip("?")
        val_expr = _filter_value(f, alloc_param)
        where_parts.append(f"{cypher_var} {f.op} {val_expr}")

    # SELECT variables → RETURN clause
    return_vars = [v.lstrip("?") for v in query.select]
    distinct_kw = "DISTINCT " if query.distinct else ""

    lines: list[str] = []
    lines.append("\n".join(f"MATCH {m}" for m in match_parts))
    if where_parts:
        lines.append("WHERE " + " AND ".join(where_parts))
    lines.append(f"RETURN {distinct_kw}{', '.join(return_vars)}")
    lines.append(f"SKIP {query.offset}")
    lines.append(f"LIMIT {query.limit}")

    cypher = "\n".join(lines)
    return cypher, params


# ── Internal helpers ──────────────────────────────────────────────────────────


def _build_triple_cypher(
    triple: PatternTriple,
    shorten_fn: Callable[[str], str],
    alloc_param: Callable[[object], str],
    declared: set[str],
) -> tuple[str, str]:
    """Convert one PatternTriple to ``(match_fragment, where_fragment)``.

    Either fragment may be empty. Returning them separately lets the caller
    emit ONE combined WHERE block (review #4) instead of embedding WHERE
    inside a per-triple MATCH (which mis-attaches in multi-triple queries).

    Handles four triple shapes:
    - ``?s rdf:type <ClassName>``  →  ``(s:LabelName)`` node-label match.
    - ``?s <predURI> "literal"``   →  ``(s)`` match + property-equality WHERE.
    - ``?s <predURI> <Bob>``       →  ``(s)-[:REL]->(o {uri:$pN})`` — the
      concrete object URI is BOUND on the node so it is not dropped (#5).
    - ``?s <predURI> ?o``          →  ``(s)-[:REL]->(o)`` relationship match.
    """
    s_term = triple.s
    p_term = triple.p
    o_term = triple.o

    # Detect rdf:type shorthand: predicate is rdf:type (any form)
    p_short_raw = shorten_fn(p_term) if not _is_var(p_term) else ""
    is_rdf_type = (
        p_short_raw == "rdf__type"
        or p_term == "rdf:type"
        or p_term == "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
    )

    if is_rdf_type and not _is_var(o_term):
        # rdf:type with concrete class — map object to a node label
        o_raw = o_term
        if o_raw.startswith("<") and o_raw.endswith(">"):
            o_raw = o_raw[1:-1]
        label_short = shorten_fn(o_raw)
        if label_short and label_short != o_raw:
            label = label_short
        else:
            label = label_short.replace(":", "__") if ":" in label_short else label_short
        label = _safe_rel(label)
        s_var = s_term.lstrip("?")
        declared.add(s_var)
        return f"({s_var}:{label})", ""

    # Predicate as relationship / property key (shortened form)
    p_short = _term_to_rel_type(p_term, shorten_fn)

    if _is_literal(o_term):
        # Literal object → node property filter. Emit the subject MATCH only
        # if the subject hasn't been declared by another triple yet; emit the
        # filter as a SEPARATE WHERE condition keyed on the subject variable.
        s_var = s_term.lstrip("?")
        prop_key = _safe_rel(p_short)
        lit_val = _parse_literal_value(o_term)
        p_ref = alloc_param(lit_val)
        match_frag = "" if s_var in declared else f"({s_var})"
        declared.add(s_var)
        # n10s stores props as ARRAY → match either the scalar or list[0].
        where_frag = (
            f"({s_var}.`{prop_key}` = {p_ref} "
            f"OR {s_var}.`{prop_key}`[0] = {p_ref})"
        )
        return match_frag, where_frag

    if p_short:
        rel = _safe_rel(p_short)
        s_var = s_term.lstrip("?")
        declared.add(s_var)
        if _is_var(o_term):
            o_var = o_term.lstrip("?")
            declared.add(o_var)
            return f"({s_var})-[:`{rel}`]->({o_var})", ""
        # Concrete URI object: BIND it on the node via $param so the edge is
        # constrained to that specific target (review #5 — was dropping it).
        o_uri = o_term[1:-1] if o_term.startswith("<") and o_term.endswith(">") else o_term
        # Resolve prefixed names (pk:Bob) to full URI when possible.
        o_full = _resolve_object_uri(o_uri, shorten_fn)
        uri_ref = alloc_param(o_full)
        return f"({s_var})-[:`{rel}`]->(:Resource {{uri: {uri_ref}}})", ""

    # Fallback: bare subject match (variable subject, unknown predicate)
    s_var = s_term.lstrip("?") if _is_var(s_term) else ""
    if s_var:
        declared.add(s_var)
        return f"({s_var})", ""
    return "", ""


def _resolve_object_uri(
    o_uri: str, shorten_fn: Callable[[str], str]
) -> str:
    """Best-effort resolution of a concrete object term to a full URI.

    A prefixed name (``pk:Bob``) is round-tripped through the store's
    shorten/expand-aware ``shorten_fn`` is not directly available here, so we
    keep the value as-is when it already looks like a full URI, and otherwise
    pass the prefixed name unchanged (the store seeds full URIs in n10s, so an
    angle-bracketed/full URI is the common case).

    Args:
        o_uri: Object term (full URI or prefixed name, brackets stripped).
        shorten_fn: Identifier shortener (unused for expansion; kept for API).

    Returns:
        The object URI string to bind as a node ``uri`` parameter.
    """
    return o_uri


def _term_to_full_uri(term: str) -> str | None:
    """Extract full URI from an angle-bracketed term or prefixed name.

    Returns None for variables and literals.
    """
    m = _URI_RE.match(term)
    if m:
        return m.group(1)
    pm = _PREFIXED_RE.match(term)
    if pm and not term.startswith("?") and not term.startswith('"'):
        # Prefixed name — return as-is; shorten_fn caller will handle
        return term
    return None


def _term_to_rel_type(term: str, shorten_fn: Callable[[str], str]) -> str:
    """Convert a predicate term to a Neo4j relationship type (shortened form)."""
    if _is_var(term):
        return ""
    full = _term_to_full_uri(term)
    if full is None:
        return ""
    short = shorten_fn(full)
    return short.replace(":", "__") if ":" in short else short


def _is_var(term: str) -> bool:
    return bool(_VAR_RE.match(term))


def _is_literal(term: str) -> bool:
    return term.startswith('"') or _NUM_RE.match(term) is not None or term in ("true", "false")


def _parse_literal_value(term: str) -> object:
    """Parse a DSL literal term to a Python scalar."""
    if term in ("true", "false"):
        return term == "true"
    m = _NUM_RE.match(term)
    if m:
        return float(term) if "." in term else int(term)
    # Quoted string: strip outer quotes and optional @lang / ^^type suffixes
    inner = term.strip('"').split('"@')[0].split('"^^')[0]
    if inner.startswith('"'):
        inner = inner[1:]
    return inner


def _filter_value(f: PatternFilter, alloc_param: Callable[[object], str]) -> str:
    """Format a PatternFilter value for Cypher WHERE."""
    v = f.value
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    return alloc_param(str(v))
