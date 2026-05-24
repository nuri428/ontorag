from __future__ import annotations

"""Translate PatternQuery DSL to Cypher (symmetric to pattern_to_sparql).

Used by Neo4jStore.query_pattern — same PatternQuery validation prevents
Cypher injection (variables, URI, prefixed name, or literal only).
"""

import re

from ontorag.stores.base import PatternFilter, PatternQuery, PatternTriple

# Matches SPARQL/Turtle prefixed names like  rdf:type  pk:Pokemon
_PREFIXED_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_\-]*):(.*)")

# Matches full URIs wrapped in angle brackets <http://...>
_URI_RE = re.compile(r"^<([^<>]+)>$")

# SPARQL variable  ?foo
_VAR_RE = re.compile(r"^\?[a-zA-Z][a-zA-Z0-9_]*$")

# Numeric literals (int or float)
_NUM_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")


# ── Public API ────────────────────────────────────────────────────────────────


def pattern_to_cypher(
    query: PatternQuery,
    shorten_fn: "None | (str) -> str" = None,  # type: ignore[valid-type]
    expand_fn: "None | (str) -> str" = None,  # type: ignore[valid-type]
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

    # Build MATCH clauses from PatternTriples
    match_parts: list[str] = []
    where_parts: list[str] = []
    _declared_vars: set[str] = set()

    for triple in query.where:
        m = _build_triple_cypher(
            triple,
            shorten_fn,
            alloc_param,
            _declared_vars,
        )
        match_parts.append(m)

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
    shorten_fn: "callable[[str], str]",  # type: ignore[valid-type]
    alloc_param: "callable[[object], str]",  # type: ignore[valid-type]
    declared: set[str],
) -> str:
    """Convert one PatternTriple to a Cypher MATCH clause fragment.

    Handles three triple shapes:
    - ``?s rdf:type <ClassName>``  →  ``MATCH (s:LabelName)`` when predicate
      is rdf:type and object is a concrete URI/prefix.
    - ``?s <predURI> ?o``  →  ``MATCH (s)-[:PRED_SHORT]->(o)``
    - ``?s <predURI> "literal"`` → not representable as edge; skipped silently
      (literal object properties in n10s are node properties, not edges).
    """
    s_term = triple.s
    p_term = triple.p
    o_term = triple.o

    s_cypher = _term_to_node(s_term, declared)
    o_cypher = _term_to_node(o_term, declared)

    # Detect rdf:type shorthand: predicate is rdf:type (any form)
    p_short = shorten_fn(p_term) if not _is_var(p_term) else ""
    is_rdf_type = (
        p_short == "rdf__type"
        or p_term == "rdf:type"
        or p_term == "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
    )

    if is_rdf_type and not _is_var(o_term):
        # rdf:type with concrete class — map object to a node label
        o_raw = o_term
        # Strip angle brackets if present
        if o_raw.startswith("<") and o_raw.endswith(">"):
            o_raw = o_raw[1:-1]
        label_short = shorten_fn(o_raw)
        if label_short and label_short != o_raw:
            # shorten_fn already returned prefix__local form
            label = label_short
        else:
            label = label_short.replace(":", "__") if ":" in label_short else label_short
        s_var = s_term.lstrip("?")
        declared.add(s_var)
        return f"({s_var}:{label})"

    # Predicate as relationship type
    p_short = _term_to_rel_type(p_term, shorten_fn)

    if _is_literal(o_term):
        # Literal: add WHERE clause for node property
        s_var = s_term.lstrip("?")
        declared.add(s_var)
        prop_key = p_short.replace(":", "__") if ":" in p_short else p_short
        lit_val = _parse_literal_value(o_term)
        p_ref = alloc_param(lit_val)
        # Store as a free-standing MATCH on the subject + WHERE on property
        return f"({s_var}) WHERE {s_var}.`{prop_key}` = {p_ref}"

    if p_short:
        s_var = s_term.lstrip("?")
        o_var_or_node = o_cypher
        declared.add(s_var)
        if _is_var(o_term):
            declared.add(o_term.lstrip("?"))
        return f"({s_var})-[:`{p_short}`]->({o_var_or_node})"

    # Fallback: just MATCH both nodes
    return f"({s_cypher})"


def _term_to_node(term: str, declared: set[str]) -> str:
    """Convert a term to a Cypher node pattern fragment (without parens).

    ``?var`` → ``var``  (just the variable name)
    ``<URI>`` or ``prefix:local`` → will be handled via shortestPath or
    property match — return empty string (caller handles concrete objects).
    """
    if _is_var(term):
        return term.lstrip("?")
    return ""


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


def _term_to_rel_type(term: str, shorten_fn: "callable") -> str:  # type: ignore[valid-type]
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


def _filter_value(f: PatternFilter, alloc_param: "callable") -> str:  # type: ignore[valid-type]
    """Format a PatternFilter value for Cypher WHERE."""
    v = f.value
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    return alloc_param(str(v))
