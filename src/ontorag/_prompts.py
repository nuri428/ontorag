from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def load(package: str, filename: str) -> str:
    return (files(package) / filename).read_text(encoding="utf-8")
