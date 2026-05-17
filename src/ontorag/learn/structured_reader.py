"""Read CSV / JSON / JSONL files into a flat list of row dicts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


class UnsupportedFormatError(ValueError):
    """Raised when the file extension is not csv, json, or jsonl."""


def flatten_dict(obj: dict[str, Any], sep: str = ".", prefix: str = "") -> dict[str, Any]:
    """Recursively flatten a nested dict using dotted keys.

    Lists are kept as-is (not expanded).
    """
    result: dict[str, Any] = {}
    for key, value in obj.items():
        full_key = f"{prefix}{sep}{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten_dict(value, sep=sep, prefix=full_key))
        else:
            result[full_key] = value
    return result


def read_structured(path: str | Path) -> list[dict[str, Any]]:
    """Dispatch to the appropriate reader based on file extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _read_csv(path)
    if suffix == ".json":
        return _read_json(path)
    if suffix == ".jsonl":
        return _read_jsonl(path)
    raise UnsupportedFormatError(
        f"Unsupported format '{suffix}'. Supported: .csv, .json, .jsonl"
    )


def _read_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if any(v.strip() for v in row.values()):
                rows.append(dict(row))
    return rows


def _read_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    return [flatten_dict(row) for row in data if isinstance(row, dict)]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(flatten_dict(json.loads(line)))
    return rows
