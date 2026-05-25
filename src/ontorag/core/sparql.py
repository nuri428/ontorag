from __future__ import annotations

import re

from ontorag.stores.base import PatternFilter, PatternQuery

DATA_GRAPH_URI = "urn:ontorag:data"

_SAFE_URI_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+\-.]*:[^\s<>"{}|\\^`\[\]]*$')
# Bare token (no scheme/prefix) — conservative local-name charset.
_SAFE_LOCAL_RE = re.compile(r"^[a-zA-Z0-9_.\-]+$")

STANDARD_PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
    "schema": "http://schema.org/",
}


def pattern_to_sparql(
    query: PatternQuery,
    extra_prefixes: dict[str, str] | None = None,
) -> str:
    """Translate a validated PatternQuery DSL into a SPARQL SELECT string.

    No injection is possible: PatternQuery fields are validated by Pydantic
    before this function is called.

    Args:
        query: A validated PatternQuery object.
        extra_prefixes: Domain-specific namespace prefixes from the loaded ontology.

    Returns:
        Ready-to-execute SPARQL SELECT string.
    """
    all_prefixes = {**STANDARD_PREFIXES, **(extra_prefixes or {})}
    used_prefixes = _collect_used_prefixes(query, all_prefixes)

    prefix_block = "\n".join(
        f"PREFIX {p}: <{ns}>" for p, ns in sorted(used_prefixes.items())
    )

    distinct = "DISTINCT " if query.distinct else ""
    select_clause = f"SELECT {distinct}{' '.join(query.select)}"

    triple_lines = "\n".join(f"  {t.s} {t.p} {t.o} ." for t in query.where)
    filter_lines = "\n".join(
        f"  FILTER ({f.var} {f.op} {_sparql_value(f)})" for f in query.filters
    )

    where_body = triple_lines
    if filter_lines:
        where_body = f"{triple_lines}\n{filter_lines}"

    # Wrap the entire WHERE body in GRAPH <urn:ontorag:data> { ... } so that
    # queries hit the named data graph instead of the (empty) default graph.
    where_body = f"  GRAPH <{DATA_GRAPH_URI}> {{\n{where_body}\n  }}"

    parts = []
    if prefix_block:
        parts.append(prefix_block)
    parts.append(f"{select_clause}\nWHERE {{\n{where_body}\n}}")
    parts.append(f"LIMIT {query.limit}")
    if query.offset:
        parts.append(f"OFFSET {query.offset}")

    return "\n".join(parts)


def _collect_used_prefixes(
    query: PatternQuery, all_prefixes: dict[str, str]
) -> dict[str, str]:
    """Return only the prefix entries that appear in the query terms."""
    terms: list[str] = list(query.select)
    for t in query.where:
        terms.extend([t.s, t.p, t.o])
    for f in query.filters:
        terms.append(f.var)

    used: dict[str, str] = {}
    for term in terms:
        if ":" in term and not term.startswith("?") and not term.startswith("<"):
            prefix = term.split(":")[0]
            if prefix in all_prefixes:
                used[prefix] = all_prefixes[prefix]
    return used


def _sparql_value(f: PatternFilter) -> str:
    """Format a PatternFilter value as a SPARQL literal."""
    v = f.value
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    # string: wrap in quotes if not already quoted
    s = str(v)
    if not (s.startswith('"') or s.startswith("'")):
        s = f'"{s}"'
    return s


# ── Shared helpers used by FusekiStore mixins ─────────────────────────────────


def build_prefix_block(all_prefixes: dict[str, str]) -> str:
    """Build a SPARQL PREFIX block string from a namespace dict."""
    return "\n".join(f"PREFIX {p}: <{ns}>" for p, ns in sorted(all_prefixes.items()))


def uri_ref(uri: str) -> str:
    """Wrap a full URI in angle brackets; leave prefixed names and ?variables as-is.

    Args:
        uri: A full URI, prefixed name, or SPARQL variable.

    Returns:
        The URI wrapped in angle brackets, or the input unchanged.

    Raises:
        ValueError: If the URI contains characters that could enable SPARQL injection.
    """
    if uri.startswith("?") or uri.startswith("<"):
        # SPARQL variable or already-bracketed URI — pass through.
        return uri
    if "://" in uri:
        # Absolute URI → validate and wrap in angle brackets.
        if not _SAFE_URI_RE.match(uri):
            raise ValueError(f"Invalid or potentially unsafe URI: {uri!r}")
        return f"<{uri}>"
    if ":" in uri:
        # Prefixed name (prefix:local) or opaque scheme (e.g. urn:) — validate
        # against the same safe charset but leave unwrapped. Closes the
        # injection gap where non-"://" inputs were previously returned
        # verbatim (e.g. "pk:Foo } INJECT", "urn:x:Foo} SELECT ...").
        if not _SAFE_URI_RE.match(uri):
            raise ValueError(f"Invalid or potentially unsafe prefixed name: {uri!r}")
        return uri
    # Bare token (no scheme/prefix) — allow only safe local-name characters.
    if not _SAFE_LOCAL_RE.match(uri):
        raise ValueError(f"Invalid or potentially unsafe term: {uri!r}")
    return uri


def sparql_literal(value: str | int | float | bool) -> str:
    """Format a Python scalar as a SPARQL literal."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if not (s.startswith('"') or s.startswith("'")):
        s = f'"{s}"'
    return s


def build_filter_sparql(
    filters: list,
    subject_var: str = "?inst",
    var_prefix: str = "fv",
) -> tuple[str, str]:
    """Build SPARQL triple patterns and a FILTER line from EntityFilter list.

    Args:
        filters: list[EntityFilter] — imported locally to avoid circular import.
        subject_var: SPARQL variable for the subject entity.
        var_prefix: Prefix for generated binding variables (?fv0, ?fv1, …).

    Returns:
        (triple_lines, filter_line) — both are empty strings when filters is empty.
    """
    from ontorag.stores.base import FilterOp  # local to break circular import

    triples: list[str] = []
    filter_parts: list[str] = []

    for i, f in enumerate(filters):
        fvar = f"?{var_prefix}{i}"
        prop = uri_ref(f.property)
        val = sparql_literal(f.value)

        triples.append(f"    {subject_var} {prop} {fvar} .")

        if f.op == FilterOp.contains:
            filter_parts.append(f"CONTAINS(STR({fvar}), {val})")
        elif f.op == FilterOp.starts_with:
            filter_parts.append(f"STRSTARTS(STR({fvar}), {val})")
        elif f.op.value == "=":
            # Lang-tagged literals ("Peacock"@en) are NOT equal to plain
            # ("Peacock") under RDF semantics, so multilingual rdfs:label
            # filters would silently return 0 rows. STR() strips the lang
            # tag and datatype, restoring intuitive equality. We keep the
            # original ?fv = "..." disjunct so URI-valued filters and
            # exact-typed-literal matches still work.
            #
            # Also LCASE both sides so natural-language label filters with
            # mixed-case user input still match — "peacock" vs "Peacock"
            # was a goldset-discovered failure mode.
            filter_parts.append(
                f"({fvar} = {val}"
                f" || STR({fvar}) = {val}"
                f" || LCASE(STR({fvar})) = LCASE({val}))"
            )
        else:
            filter_parts.append(f"{fvar} {f.op.value} {val}")

    triple_lines = "\n".join(triples)
    filter_line = (
        ("    FILTER(" + " && ".join(filter_parts) + ")") if filter_parts else ""
    )
    return triple_lines, filter_line
