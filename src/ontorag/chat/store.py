"""SQLite-backed chat session storage for the Playground UI.

Stores sessions and their full AgentLoop._history so conversations
survive page refresh and server restart.

No external dependencies — uses Python stdlib sqlite3 via asyncio.to_thread.
Each connection lazily creates the table (CREATE TABLE IF NOT EXISTS), so
explicit init_db() is optional (but provided for explicit startup calls).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

_DB_PATH: Path = Path("chat.db")

_CREATE_SQL = """
    CREATE TABLE IF NOT EXISTS sessions (
        id      TEXT PRIMARY KEY,
        title   TEXT NOT NULL,
        history TEXT NOT NULL DEFAULT '[]',
        created TEXT NOT NULL,
        updated TEXT NOT NULL
    )
"""


def set_db_path(path: str | Path) -> None:
    """Override the default DB path. Takes effect on the next connection."""
    global _DB_PATH
    _DB_PATH = Path(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _open() -> Generator[sqlite3.Connection, None, None]:
    """Open a connection, ensure the table exists, and yield with auto-commit."""
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(_CREATE_SQL)
        yield conn


# ── sync helpers (run inside asyncio.to_thread) ────────────────────────────────


def _init() -> None:
    with _open():
        pass  # just ensure the table exists


def _list_sessions() -> list[dict[str, Any]]:
    with _open() as conn:
        rows = conn.execute(
            "SELECT id, title, created, updated FROM sessions ORDER BY updated DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


def _create_session(title: str) -> str:
    sid = uuid.uuid4().hex[:12]
    now = _now()
    with _open() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, history, created, updated) VALUES (?,?,?,?,?)",
            (sid, title, "[]", now, now),
        )
    return sid


def _get_history(session_id: str) -> list[dict[str, Any]]:
    with _open() as conn:
        row = conn.execute(
            "SELECT history FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
    return json.loads(row["history"]) if row else []


def _save_session(
    session_id: str,
    history: list[dict[str, Any]],
    title: str | None = None,
) -> None:
    now = _now()
    h = json.dumps(history, default=str, ensure_ascii=False)
    with _open() as conn:
        if title:
            conn.execute(
                "UPDATE sessions SET history=?, updated=?, title=? WHERE id=?",
                (h, now, title, session_id),
            )
        else:
            conn.execute(
                "UPDATE sessions SET history=?, updated=? WHERE id=?",
                (h, now, session_id),
            )


def _delete_session(session_id: str) -> None:
    with _open() as conn:
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))


# ── async public API ──────────────────────────────────────────────────────────


async def init_db() -> None:
    """Ensure the sessions table exists. Idempotent — safe to call repeatedly."""
    await asyncio.to_thread(_init)


async def list_sessions() -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_sessions)


async def create_session(title: str = "새 대화") -> str:
    return await asyncio.to_thread(_create_session, title)


async def get_history(session_id: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_get_history, session_id)


async def save_session(
    session_id: str,
    history: list[dict[str, Any]],
    title: str | None = None,
) -> None:
    await asyncio.to_thread(_save_session, session_id, history, title)


async def delete_session(session_id: str) -> None:
    await asyncio.to_thread(_delete_session, session_id)


def extract_display_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract user/assistant text pairs from AgentLoop history for UI display.

    Skips tool_use/tool_result message blocks — returns only what the user sees.
    """
    result: list[dict[str, str]] = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and isinstance(content, str):
            result.append({"role": "user", "text": content})
        elif role == "assistant" and isinstance(content, list):
            texts = [
                b["text"]
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                result.append({"role": "assistant", "text": "".join(texts)})
    return result
