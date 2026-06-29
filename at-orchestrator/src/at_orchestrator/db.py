"""SQLite async database module for at-orchestrator.

Uses stdlib ``sqlite3`` wrapped with ``asyncio.to_thread()`` for async
compatibility.  WAL mode is enabled for concurrent access.

Schema
------
**tasks** — inbound AT/reply notifications from Bilibili.
**cursor_state** — pagination cursors for polling endpoints.

Usage::

    from at_orchestrator.db import init_db, insert_task, get_pending_tasks

    await init_db("data.db")          # must be called first — sets db_path
    await insert_task(task_dict)      # all other functions use the same db
    ...

All functions open a connection, execute, and close — no connection pooling.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────
# Module-level state
# ──────────────────────────────────────────────────────────────────────

_db_path: str = ":memory:"


def _get_db() -> str:
    """Return the current database path (set by ``init_db()``)."""
    return _db_path


# ──────────────────────────────────────────────────────────────────────
# Table schema (column lists)
# ──────────────────────────────────────────────────────────────────────

_TASK_COLUMNS: tuple[str, ...] = (
    "msg_id",
    "source",
    "user_mid",
    "user_nickname",
    "content",
    "business_id",
    "subject_id",
    "root_id",
    "source_id",
    "status",
    "created_at",
    "processed_at",
    "reply_method",
    "reply_error",
    "classification_result",
    "skill_result",
    "cursor_id",
    "cursor_time",
)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


async def init_db(db_path: str | Path) -> None:
    """Create tables (if not exist) and enable WAL journal mode.

    Must be called before any other db function — sets the database path
    used by all subsequent operations.
    """
    global _db_path
    _db_path = str(db_path)

    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
        """Add a column to *table* if it does not already exist."""
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

    def _init() -> None:
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    msg_id          INTEGER NOT NULL,
                    source          TEXT    NOT NULL,
                    user_mid        INTEGER NOT NULL,
                    user_nickname   TEXT    NOT NULL,
                    content         TEXT    NOT NULL,
                    business_id     INTEGER NOT NULL,
                    subject_id      INTEGER NOT NULL,
                    root_id         INTEGER,
                    source_id       INTEGER,
                    status          TEXT    NOT NULL DEFAULT 'pending',
                    created_at      REAL    NOT NULL,
                    processed_at    REAL,
                    reply_method    TEXT,
                    reply_error     TEXT,
                    classification_result  TEXT,
                    skill_result    TEXT,
                    cursor_id       INTEGER,
                    cursor_time     REAL,
                    UNIQUE(msg_id, source)
                )
            """)

            _ensure_column(conn, "tasks", "classification_result", "TEXT")
            _ensure_column(conn, "tasks", "skill_result", "TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS cursor_state (
                    source      TEXT PRIMARY KEY,
                    cursor_id   INTEGER NOT NULL,
                    cursor_time REAL    NOT NULL
                )
            """)

            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_init)


async def insert_task(task: dict[str, Any]) -> bool:
    """Insert a task row.  Returns ``True`` if inserted, ``False`` if a
    duplicate ``(msg_id, source)`` already existed (INSERT OR IGNORE)."""

    def _insert() -> bool:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            # Build ordered values from known columns only
            values = tuple(task.get(col) for col in _TASK_COLUMNS)
            placeholders = ", ".join("?" for _ in _TASK_COLUMNS)
            columns_sql = ", ".join(_TASK_COLUMNS)

            conn.execute(
                f"INSERT OR IGNORE INTO tasks ({columns_sql}) VALUES ({placeholders})",
                values,
            )
            inserted = conn.total_changes > 0  # accurate in autocommit mode
            conn.commit()
            return inserted
        finally:
            conn.close()

    return await asyncio.to_thread(_insert)


async def get_pending_tasks(
    limit: int = 10, source: str | None = None
) -> list[dict[str, Any]]:
    """Return up to *limit* pending tasks ordered by ``created_at`` ASC.

    When *source* is provided (e.g. ``"reply"`` or ``"at"``), only tasks
    matching that source type are returned.

    Returns empty list when no pending tasks exist.
    """

    def _get() -> list[dict[str, Any]]:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            if source is not None:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = 'pending' AND source = ? "
                    "ORDER BY created_at ASC LIMIT ?",
                    (source, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = 'pending' "
                    "ORDER BY created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    return await asyncio.to_thread(_get)


async def update_task_status(
    msg_id: int, source: str, status: str, error: str | None = None
) -> None:
    """Update the status (and optionally reply_error) for a task.

    Also sets ``processed_at`` to the current timestamp.
    """

    def _update() -> None:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            if error is not None:
                conn.execute(
                    "UPDATE tasks SET status = ?, processed_at = ?, reply_error = ? "
                    "WHERE msg_id = ? AND source = ?",
                    (status, time.time(), error, msg_id, source),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status = ?, processed_at = ? "
                    "WHERE msg_id = ? AND source = ?",
                    (status, time.time(), msg_id, source),
                )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_update)


async def update_task_reply(
    msg_id: int,
    source: str,
    reply_method: str,
    error: str | None = None,
) -> None:
    """Update ``reply_method`` and optionally ``reply_error`` for a task."""

    def _update() -> None:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            conn.execute(
                "UPDATE tasks SET reply_method = ?, reply_error = ? "
                "WHERE msg_id = ? AND source = ?",
                (reply_method, error, msg_id, source),
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_update)


async def get_tasks_by_status(
    status: str | tuple[str, ...], limit: int = 10, source: str | None = None
) -> list[dict[str, Any]]:
    """Return up to *limit* tasks matching *status* (single or multiple),
    ordered by ``created_at`` ASC.

    ``status`` can be a single string (e.g. ``"pending"``) or a tuple of
    strings (e.g. ``("pending", "classifying")``).

    When *source* is provided, only tasks matching that source type are returned.

    Returns empty list when no matching tasks exist.
    """

    def _get() -> list[dict[str, Any]]:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            if isinstance(status, tuple):
                placeholders = ", ".join("?" for _ in status)
                if source is not None:
                    sql = (
                        f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
                        "AND source = ? ORDER BY created_at ASC LIMIT ?"
                    )
                    params = (*status, source, limit)
                else:
                    sql = (
                        f"SELECT * FROM tasks WHERE status IN ({placeholders}) "
                        "ORDER BY created_at ASC LIMIT ?"
                    )
                    params = (*status, limit)
            else:
                if source is not None:
                    sql = (
                        "SELECT * FROM tasks WHERE status = ? AND source = ? "
                        "ORDER BY created_at ASC LIMIT ?"
                    )
                    params = (status, source, limit)
                else:
                    sql = (
                        "SELECT * FROM tasks WHERE status = ? "
                        "ORDER BY created_at ASC LIMIT ?"
                    )
                    params = (status, limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    return await asyncio.to_thread(_get)


async def update_classification(
    msg_id: int, source: str, classification_json: str
) -> None:
    """Set ``classification_result`` JSON text and change status to 'classified'."""

    def _update() -> None:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            conn.execute(
                "UPDATE tasks SET classification_result = ?, status = 'classified', "
                "processed_at = ? WHERE msg_id = ? AND source = ?",
                (classification_json, time.time(), msg_id, source),
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_update)


async def update_skill_result(
    msg_id: int, source: str, skill_json: str, reply_content: str
) -> None:
    """Set ``skill_result`` JSON text and change status to 'pending_reply'.

    The *reply_content* is stored as the ``reply_method`` field for use by
    the reply phase.
    """

    def _update() -> None:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            conn.execute(
                "UPDATE tasks SET skill_result = ?, reply_method = ?, "
                "status = 'pending_reply', processed_at = ? "
                "WHERE msg_id = ? AND source = ?",
                (skill_json, reply_content, time.time(), msg_id, source),
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_update)


async def get_cursor(source: str) -> tuple[int, float] | None:
    """Return ``(cursor_id, cursor_time)`` for *source*, or ``None`` if
    no cursor has been set."""

    def _get() -> tuple[int, float] | None:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            row = conn.execute(
                "SELECT cursor_id, cursor_time FROM cursor_state WHERE source = ?",
                (source,),
            ).fetchone()
            return None if row is None else (row[0], row[1])
        finally:
            conn.close()

    return await asyncio.to_thread(_get)


async def set_cursor(source: str, cursor_id: int, cursor_time: float) -> None:
    """Insert or replace the cursor for *source*."""

    def _set() -> None:
        conn = sqlite3.connect(_get_db(), check_same_thread=False)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cursor_state (source, cursor_id, cursor_time) "
                "VALUES (?, ?, ?)",
                (source, cursor_id, cursor_time),
            )
            conn.commit()
        finally:
            conn.close()

    await asyncio.to_thread(_set)
