"""Directory / multi-file RDF loader — orchestration layer.

Scans a directory for RDF files and feeds them to a :class:`GraphStore`
one file at a time via the existing single-file ``load_rdf`` contract. The
``GraphStore`` Protocol is intentionally untouched: directory walking,
scope mapping, and ordering live here, so every backend (Fuseki / Neo4j)
gets directory loading with zero adapter changes.

Behaviour (see ``docs/design/directory-loader.md``):

* **Manifest override (§3-2 / §8).** When ``<root>/ontorag.yaml`` is present
  it overrides the default sub-directory mapping.  Providing ``ontology=``
  together with a manifest is a conflict and raises :class:`ValueError` before
  any load begins.  Manifest parsing and validation is in
  :mod:`ontorag.core.manifest`.
* **Scope mapping (§3).** ``ontology=`` given → flat-merge every file into
  that one scope. Otherwise each 1-depth sub-directory name becomes an
  ontology id (validated by ``validate_ontology_id``); files directly under
  the root load under ``ontology=None`` (legacy default graph pair); files
  nested deeper than one level are attributed to their nearest 1-depth dir.
* **schema-before-data (§6).** Per scope, all ``mode=schema`` files load
  before any ``mode=data`` file — never rely on filename order.
* **replace policy (§7).** ``replace=True`` replaces each scope's data graph
  once (on that scope's first data file) then appends; ``replace=False`` is
  pure append.
* **continue-and-report (§13).** A per-file load error is recorded as
  ``failed`` and the run continues; a schema-file failure skips that scope's
  remaining data files. Only configuration errors (bad slug, manifest issues)
  fail fast, before any load begins.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ontorag.core.loader import detect_mode, parse_rdf
from ontorag.core.manifest import load_manifest
from ontorag.core.ontology import validate_ontology_id
from ontorag.stores.base import BatchLoadResult, FileLoadOutcome, GraphStore

if TYPE_CHECKING:
    from rdflib import Graph

logger = logging.getLogger(__name__)

#: A single load-plan entry: (file path, ontology scope, mode, pre-parsed graph
#: or None). The default-mapping path parses to detect mode and passes that
#: graph through to load_rdf (avoiding a re-parse); the manifest path declares
#: mode explicitly and carries None.
_PlanEntry = tuple[Path, "str | None", Literal["schema", "data"], "Graph | None"]

#: File extensions recognised as RDF.
RDF_SUFFIXES: frozenset[str] = frozenset({".ttl", ".jsonld", ".rdf", ".owl", ".n3"})

#: Directory names always skipped during the scan.
DEFAULT_IGNORE: frozenset[str] = frozenset(
    {".git", "node_modules", "__pycache__", ".venv"}
)

_MODE_RANK: dict[str, int] = {"schema": 0, "data": 1}


def _collect_files(
    root: Path, recursive: bool, ignore: frozenset[str]
) -> list[Path]:
    """Return RDF files under *root*, skipping ignored dirs and hidden files.

    Args:
        root: Directory to scan.
        recursive: Walk sub-directories when True, else 1-depth only.
        ignore: Directory names to skip entirely (matched on any path part).

    Returns:
        Sorted list of file paths (deterministic order for reproducibility).
    """
    candidates = root.rglob("*") if recursive else root.iterdir()
    files: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        if path.suffix.lower() not in RDF_SUFFIXES:
            continue
        rel_parts = path.relative_to(root).parts
        # Skip ignored directories and any hidden component (dotfile/dotdir).
        if any(part in ignore or part.startswith(".") for part in rel_parts):
            continue
        files.append(path)
    return sorted(files)


def _resolve_scope(file: Path, root: Path, override: str | None) -> str | None:
    """Decide the ontology id a file is attributed to (§3).

    Args:
        file: The RDF file path.
        root: Scan root.
        override: ``--ontology`` value (flat-merge) or None for default rules.

    Returns:
        Ontology id, or None for root-level files (legacy default graph).

    Raises:
        ValueError: If a sub-directory name fails ``validate_ontology_id``.
    """
    if override is not None:
        return override
    rel_parts = file.relative_to(root).parts
    if len(rel_parts) <= 1:
        # File sits directly under root → legacy default graph pair.
        return None
    # Nearest 1-depth directory name is the scope (deeper nesting collapses).
    return validate_ontology_id(rel_parts[0])


def _classify_and_order(
    files: list[Path], root: Path, override: str | None
) -> tuple[list[_PlanEntry], list[FileLoadOutcome]]:
    """Parse each file, detect its mode, and order schema-before-data per scope.

    The parsed graph is kept in each plan entry so the loader can hand it back
    to ``store.load_rdf(graph=...)`` and avoid a second parse.

    Args:
        files: Collected RDF file paths.
        root: Scan root (for relative ``source`` strings + scope mapping).
        override: ``--ontology`` flat-merge id, or None.

    Returns:
        ``(load_plan, parse_failures)`` where load_plan is the ordered list of
        ``(file, scope, mode, graph)`` and parse_failures are ``failed``
        outcomes for files that could not be parsed.
    """
    parsed: list[_PlanEntry] = []
    failures: list[FileLoadOutcome] = []
    # Track scope first-seen order for a predictable, stable load sequence.
    scope_order: dict[str | None, int] = {}

    for file in files:
        scope = _resolve_scope(file, root, override)  # may raise (fail-fast)
        scope_order.setdefault(scope, len(scope_order))
        try:
            parsed_graph = parse_rdf(file)
            mode = detect_mode(parsed_graph)
        except Exception as exc:  # noqa: BLE001 — record + continue
            logger.warning("Parse failed for %s: %s", file, exc)
            failures.append(
                FileLoadOutcome(
                    source=str(file.relative_to(root)),
                    ontology=scope,
                    status="failed",
                    reason=f"parse error: {exc}",
                )
            )
            continue
        parsed.append((file, scope, mode, parsed_graph))

    parsed.sort(key=lambda t: (scope_order[t[1]], _MODE_RANK[t[2]]))
    return parsed, failures


def _build_plan_from_manifest(
    root: Path,
) -> tuple[list[_PlanEntry], list[str]]:
    """Return ``(load_plan, extra_ignore)`` derived from a manifest.

    The manifest's ``ontologies`` list already separates schema from data and
    specifies listed order; this function flattens it to the same
    ``(file, scope, mode)`` tuple list that ``_classify_and_order`` would
    produce, preserving manifest-defined ordering exactly.

    Args:
        root: Scan root (manifest is at ``<root>/ontorag.yaml``).

    Returns:
        ``(load_plan, extra_ignore)`` — an ordered list of
        ``(absolute_path, ontology_id, mode)`` triples, and the raw extra
        ignore patterns from the manifest (may be empty).

    Raises:
        ValueError: If the manifest is present but structurally invalid,
            references a missing file, or contains an invalid ontology id.
    """
    plan_obj = load_manifest(root)
    if plan_obj is None:
        # Caller must check manifest_path.exists() before calling — but be safe.
        return [], []

    # Manifest declares mode explicitly, so no pre-parse here — graph is None
    # and load_rdf will parse each file itself.
    load_plan: list[_PlanEntry] = [
        (entry.path, entry.ontology_id, entry.mode, None)
        for entry in plan_obj.entries
    ]
    return load_plan, plan_obj.extra_ignore


async def load_directory(
    store: GraphStore,
    root: str | Path,
    *,
    ontology: str | None = None,
    replace: bool = False,
    recursive: bool = True,
    ignore: frozenset[str] = DEFAULT_IGNORE,
    on_file: Callable[[FileLoadOutcome], None] | None = None,
) -> BatchLoadResult:
    """Scan *root* and load its RDF files into *store* (see module docstring).

    Args:
        store: Target graph store (any backend satisfying the Protocol).
        root: Directory to scan.
        ontology: Flat-merge every file into this scope (§3-1); None applies
            the sub-directory mapping rules (§3-3).
        replace: Replace each scope's data graph once on its first data file,
            then append (§7); False = pure append.
        recursive: Walk sub-directories (True) or 1-depth only.
        ignore: Directory names to skip.
        on_file: Optional progress callback invoked with each outcome.

    Returns:
        Aggregated :class:`BatchLoadResult`.

    Raises:
        NotADirectoryError: If *root* is not a directory.
        ValueError: If a manifest exists and ``ontology`` is also provided
            (conflict, §3-2); or if a manifest is present but invalid; or if
            ``ontology`` or a sub-directory name is an invalid slug.  All
            :class:`ValueError` cases abort before any load — fail-fast on
            configuration.
    """
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    # Validate the flat-merge id up front (fail-fast on configuration).
    if ontology is not None:
        ontology = validate_ontology_id(ontology)

    # ── Manifest path (§3-2 / §8) ────────────────────────────────────────────
    manifest_path = root / "ontorag.yaml"
    use_manifest = manifest_path.exists()

    if use_manifest and ontology is not None:
        raise ValueError(
            "Cannot use both a manifest (ontorag.yaml) and --ontology at the "
            "same time — they specify conflicting scope mappings (§3-2).  "
            "Either remove ontorag.yaml or omit --ontology."
        )

    outcomes: list[FileLoadOutcome] = []

    if use_manifest:
        logger.debug("Manifest found at %s — overriding default sub-dir mapping", manifest_path)
        manifest_load_plan, extra_ignore = _build_plan_from_manifest(root)
        # Merge manifest's extra_ignore into the active ignore set.
        effective_ignore = ignore | frozenset(extra_ignore)
        load_plan = manifest_load_plan
        logger.debug(
            "Manifest load plan: %d file entries, extra_ignore=%r",
            len(load_plan),
            extra_ignore,
        )
    else:
        effective_ignore = ignore
        files = _collect_files(root, recursive, effective_ignore)
        # _classify_and_order may raise ValueError on a bad sub-dir slug — that
        # is a configuration error and must abort before any load (fail-fast).
        load_plan, outcomes = _classify_and_order(files, root, ontology)

    # Report parse-stage failures through the callback too, so a caller's
    # progress counter advances for every collected file (not just loaded ones).
    if on_file:
        for outcome in outcomes:
            on_file(outcome)

    # Scopes whose schema failed to load → their data files are skipped.
    failed_schema_scopes: set[str | None] = set()
    # Scopes whose data graph has already been replaced once (replace policy).
    replaced_scopes: set[str | None] = set()

    for file, scope, mode, pre_graph in load_plan:
        rel = str(file.relative_to(root))

        if mode == "data" and scope in failed_schema_scopes:
            outcome = FileLoadOutcome(
                source=rel,
                ontology=scope,
                status="skipped",
                reason="schema load failed in scope",
            )
            outcomes.append(outcome)
            if on_file:
                on_file(outcome)
            continue

        # Replace only the first data file of a scope; schema is PUT anyway.
        effective_replace = False
        if replace and mode == "data" and scope not in replaced_scopes:
            effective_replace = True
            replaced_scopes.add(scope)

        try:
            result = await store.load_rdf(
                str(file),
                mode,
                replace=effective_replace,
                ontology=scope,
                graph=pre_graph,
            )
            outcome = FileLoadOutcome(
                source=rel,
                ontology=scope,
                status="loaded",
                mode=mode,
                triples_loaded=result.triples_loaded,
            )
        except Exception as exc:  # noqa: BLE001 — continue-and-report
            logger.warning("Load failed for %s: %s", file, exc)
            if mode == "schema":
                failed_schema_scopes.add(scope)
            outcome = FileLoadOutcome(
                source=rel,
                ontology=scope,
                status="failed",
                reason=f"load error: {exc}",
            )

        outcomes.append(outcome)
        if on_file:
            on_file(outcome)

    loaded = sum(1 for o in outcomes if o.status == "loaded")
    skipped = sum(1 for o in outcomes if o.status == "skipped")
    failed = sum(1 for o in outcomes if o.status == "failed")
    return BatchLoadResult(
        root=str(root),
        total_files=len(outcomes),
        loaded=loaded,
        skipped=skipped,
        failed=failed,
        total_triples=sum(o.triples_loaded for o in outcomes),
        outcomes=outcomes,
    )
