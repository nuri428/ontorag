"""Small shared configuration helpers (v1.0).

Currently: environment-driven timeout parsing, used by every graph-store and LLM
adapter so the parse-and-validate logic lives in one place.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def env_timeout(name: str, default: float | None) -> float | None:
    """Read a timeout (seconds) from an env var, falling back to *default*.

    Accepts a positive number. A value of ``0`` or an empty string means "no
    timeout" → returns ``None`` (unbounded). A malformed value logs a warning
    and falls back to *default* rather than crashing startup.

    Args:
        name: Environment variable name (e.g. ``NEO4J_QUERY_TIMEOUT``).
        default: Fallback when the var is unset or malformed.

    Returns:
        The parsed timeout in seconds, or ``None`` for unbounded.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using default %r.", name, raw, default)
        return default
    if value <= 0:
        return None  # explicit opt-out → unbounded
    return value
