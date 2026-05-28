"""Parser and validator for the optional ``ontorag.yaml`` manifest.

When ``<root>/ontorag.yaml`` exists it overrides the default sub-directory →
ontology mapping (§3-2 / §8 of ``docs/design/directory-loader.md``).

The manifest format::

    ontologies:
      - id: foaf
        schema: [foaf/foaf.ttl]
        data:   [foaf/people.ttl, foaf/orgs.ttl]
      - id: pokemon
        schema: [pokemon/schema.ttl]
        data:   [pokemon/*.ttl]            # glob allowed
    ignore: ["drafts/**"]

Rules
-----
- Every ``id`` must pass :func:`~ontorag.core.ontology.validate_ontology_id`.
- Every file reference (after glob expansion, relative to *root*) must exist on
  disk — missing files are a configuration error and cause a :class:`ValueError`
  **before any load begins** (fail-fast).
- ``schema`` entries are loaded before ``data`` entries within each ontology
  (the manifest structure already separates them; listed order within each list
  is preserved).
- The ``ignore`` list is returned as additional patterns so the caller can merge
  them with its own default-ignore set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from ontorag.core.ontology import validate_ontology_id

logger = logging.getLogger(__name__)

#: Name of the manifest file looked up at the root of a directory load.
MANIFEST_FILENAME = "ontorag.yaml"


@dataclass(frozen=True)
class ManifestFileEntry:
    """A single file entry resolved from the manifest.

    Attributes:
        path: Absolute path to the RDF file.
        ontology_id: Ontology scope this file belongs to.
        mode: Load mode — ``"schema"`` or ``"data"``.
    """

    path: Path
    ontology_id: str
    mode: Literal["schema", "data"]


@dataclass
class ManifestLoadPlan:
    """Parsed and validated manifest ready for the loader to consume.

    Attributes:
        entries: Ordered list of files to load.  Schema entries come before
            data entries within each ontology; ontology ordering follows the
            manifest's ``ontologies`` list order.
        extra_ignore: Patterns from the ``ignore`` key (may be empty).
    """

    entries: list[ManifestFileEntry] = field(default_factory=list)
    extra_ignore: list[str] = field(default_factory=list)


def load_manifest(root: Path) -> ManifestLoadPlan | None:
    """Parse and validate ``<root>/ontorag.yaml``, or return None if absent.

    Args:
        root: Directory to look for the manifest in.

    Returns:
        A :class:`ManifestLoadPlan` when the manifest exists and is valid,
        or ``None`` when no manifest file is found.

    Raises:
        ValueError: If the manifest is present but invalid — bad ontology id,
            referenced file does not exist after glob expansion, or the YAML
            structure is unexpected.
    """
    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None

    logger.debug("Loading manifest from %s", manifest_path)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"ontorag.yaml must be a YAML mapping at the top level, got {type(raw).__name__}"
        )

    entries = _parse_ontologies(raw, root)
    extra_ignore = _parse_ignore(raw)

    plan = ManifestLoadPlan(entries=entries, extra_ignore=extra_ignore)
    logger.debug(
        "Manifest loaded: %d entries across %d ontologies",
        len(entries),
        len({e.ontology_id for e in entries}),
    )
    return plan


# ── private helpers ───────────────────────────────────────────────────────────


def _parse_ontologies(raw: dict, root: Path) -> list[ManifestFileEntry]:
    """Parse the ``ontologies:`` block and expand/validate each file reference.

    Args:
        raw: Top-level parsed YAML dict.
        root: Scan root (used to resolve relative paths and glob patterns).

    Returns:
        Ordered list of :class:`ManifestFileEntry` — schema-before-data within
        each ontology, ontologies in listed order.

    Raises:
        ValueError: On invalid id, missing file, or malformed structure.
    """
    ontologies_raw = raw.get("ontologies")
    if ontologies_raw is None:
        return []
    if not isinstance(ontologies_raw, list):
        raise ValueError(
            "'ontologies' must be a YAML list, "
            f"got {type(ontologies_raw).__name__}"
        )

    entries: list[ManifestFileEntry] = []
    for idx, block in enumerate(ontologies_raw):
        if not isinstance(block, dict):
            raise ValueError(
                f"ontologies[{idx}] must be a mapping, got {type(block).__name__}"
            )
        ont_id = _require_string(block, "id", f"ontologies[{idx}]")
        # validate_ontology_id raises ValueError on bad slug — exactly the
        # fail-fast contract the design doc requires.
        validate_ontology_id(ont_id)

        schema_files = _expand_file_refs(
            block.get("schema") or [],
            root,
            ont_id,
            "schema",
        )
        data_files = _expand_file_refs(
            block.get("data") or [],
            root,
            ont_id,
            "data",
        )
        entries.extend(schema_files)
        entries.extend(data_files)

    return entries


def _parse_ignore(raw: dict) -> list[str]:
    """Parse the optional ``ignore:`` key.

    Args:
        raw: Top-level parsed YAML dict.

    Returns:
        List of ignore-glob strings (may be empty).

    Raises:
        ValueError: If ``ignore`` is present but is not a list of strings.
    """
    ignore_raw = raw.get("ignore")
    if ignore_raw is None:
        return []
    if not isinstance(ignore_raw, list):
        raise ValueError(
            f"'ignore' must be a YAML list, got {type(ignore_raw).__name__}"
        )
    bad = [item for item in ignore_raw if not isinstance(item, str)]
    if bad:
        raise ValueError(f"'ignore' entries must be strings, got: {bad!r}")
    return ignore_raw


def _expand_file_refs(
    refs: list,
    root: Path,
    ont_id: str,
    mode: Literal["schema", "data"],
) -> list[ManifestFileEntry]:
    """Expand a list of file-reference strings (globs allowed) to entries.

    Args:
        refs: Raw list from the YAML (``schema:`` or ``data:`` value).
        root: Scan root for resolving relative paths.
        ont_id: Ontology id (already validated).
        mode: ``"schema"`` or ``"data"``.

    Returns:
        Ordered list of :class:`ManifestFileEntry` with absolute paths.

    Raises:
        ValueError: If a reference is not a string, if a glob expands to zero
            files, or if a literal path does not exist.
    """
    if not isinstance(refs, list):
        raise ValueError(
            f"'{mode}' for ontology '{ont_id}' must be a list, "
            f"got {type(refs).__name__}"
        )

    result: list[ManifestFileEntry] = []
    for ref in refs:
        if not isinstance(ref, str):
            raise ValueError(
                f"'{mode}' entries for ontology '{ont_id}' must be strings, "
                f"got {type(ref).__name__}: {ref!r}"
            )
        # Treat any ref containing a glob metacharacter as a pattern.
        if any(ch in ref for ch in ("*", "?", "[")):
            matched = sorted(root.glob(ref))
            if not matched:
                raise ValueError(
                    f"Manifest glob '{ref}' for ontology '{ont_id}' [{mode}] "
                    f"matched no files under {root}"
                )
            for p in matched:
                result.append(ManifestFileEntry(path=p.resolve(), ontology_id=ont_id, mode=mode))
        else:
            p = (root / ref).resolve()
            if not p.exists():
                raise ValueError(
                    f"Manifest references '{ref}' for ontology '{ont_id}' [{mode}] "
                    f"but the file does not exist: {p}"
                )
            result.append(ManifestFileEntry(path=p, ontology_id=ont_id, mode=mode))
    return result


def _require_string(block: dict, key: str, context: str) -> str:
    """Extract a required string key from a mapping.

    Args:
        block: YAML mapping to extract from.
        key: Key name.
        context: Human-readable location string for error messages.

    Returns:
        The string value.

    Raises:
        ValueError: If the key is absent or not a string.
    """
    value = block.get(key)
    if value is None:
        raise ValueError(f"'{key}' is required in {context}")
    if not isinstance(value, str):
        raise ValueError(
            f"'{key}' in {context} must be a string, got {type(value).__name__}: {value!r}"
        )
    return value
