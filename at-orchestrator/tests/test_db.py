"""Tests for at_orchestrator.db — SQLite async CRUD module.

TDD: these tests are written BEFORE the implementation.
Run once to see them fail (ImportError), then implement db.py to make them pass.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from at_orchestrator.db import (
    get_cursor,
    get_pending_tasks,
    init_db,
    insert_task,
    set_cursor,
    update_task_reply,
    update_task_status,
)


# ──────────────────────────────────────────────────────────────────────
# Helper: build a valid task dict (matching the tasks table columns)
# ──────────────────────────────────────────────────────────────────────


def _make_task(**overrides: Any) -> dict[str, Any]:
    """Return a task dict with sensible defaults for all required columns."""
    data: dict[str, Any] = {
        "msg_id": 1001,
        "source": "reply",
        "user_mid": 12345678,
        "user_nickname": "测试用户",
        "content": "你好 @UP主",
        "business_id": 1,
        "subject_id": 20220101,
        "root_id": None,
        "source_id": None,
        "status": "pending",
        "created_at": 1750000000.0,
        "processed_at": None,
        "reply_method": None,
        "reply_error": None,
        "cursor_id": None,
        "cursor_time": None,
    }
    data.update(overrides)
    return data


# ──────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────


class TestInitDb:
    """init_db() — creates both tables + enables WAL mode."""

    async def test_creates_tasks_table(self, tmp_db_path: Path) -> None:
        """init_db() should create the 'tasks' table."""
        await init_db(tmp_db_path)

        conn = sqlite3.connect(str(tmp_db_path))
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "msg_id" in columns
        assert "source" in columns
        assert "user_mid" in columns
        assert "user_nickname" in columns
        assert "content" in columns
        assert "business_id" in columns
        assert "subject_id" in columns
        assert "status" in columns
        assert "created_at" in columns
        # nullable columns should exist too
        assert "root_id" in columns
        assert "source_id" in columns
        assert "processed_at" in columns
        assert "reply_method" in columns
        assert "reply_error" in columns
        assert "cursor_id" in columns
        assert "cursor_time" in columns

    async def test_creates_cursor_state_table(self, tmp_db_path: Path) -> None:
        """init_db() should create the 'cursor_state' table."""
        await init_db(tmp_db_path)

        conn = sqlite3.connect(str(tmp_db_path))
        cursor = conn.execute("PRAGMA table_info(cursor_state)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "source" in columns
        assert "cursor_id" in columns
        assert "cursor_time" in columns

    async def test_enables_wal_mode(self, tmp_db_path: Path) -> None:
        """init_db() should enable WAL journal mode."""
        await init_db(tmp_db_path)

        conn = sqlite3.connect(str(tmp_db_path))
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        conn.close()

        assert mode.upper() == "WAL"


class TestInsertTask:
    """insert_task() — inserts tasks and handles dedup."""

    async def test_insert_success_returns_true(self, tmp_db_path: Path) -> None:
        """insert_task() should return True on first insert."""
        await init_db(tmp_db_path)
        task = _make_task(msg_id=2001, source="reply")
        result = await insert_task(task)
        assert result is True

    async def test_duplicate_returns_false(self, tmp_db_path: Path) -> None:
        """insert_task() should return False on duplicate (msg_id, source)."""
        await init_db(tmp_db_path)
        task = _make_task(msg_id=2002, source="reply")
        await insert_task(task)
        result = await insert_task(task)
        assert result is False

    async def test_same_msg_id_different_source_inserts(self, tmp_db_path: Path) -> None:
        """Same msg_id with different source should insert (UNIQUE on both cols)."""
        await init_db(tmp_db_path)
        r1 = await insert_task(_make_task(msg_id=2003, source="reply"))
        r2 = await insert_task(_make_task(msg_id=2003, source="at"))
        assert r1 is True
        assert r2 is True

    async def test_insert_persists_data(self, tmp_db_path: Path) -> None:
        """Inserted task data should be retrievable from SQLite."""
        await init_db(tmp_db_path)
        task = _make_task(
            msg_id=2004,
            source="at",
            user_mid=999,
            user_nickname="张三",
            content="@UP主 帮忙分析",
            business_id=5,
            subject_id=888,
            root_id=100,
            source_id=200,
            status="pending",
            created_at=1750000001.0,
        )
        await insert_task(task)

        conn = sqlite3.connect(str(tmp_db_path))
        row = conn.execute(
            "SELECT msg_id, source, user_mid, user_nickname, content, "
            "business_id, subject_id, root_id, source_id, status, created_at "
            "FROM tasks WHERE msg_id = 2004 AND source = 'at'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 2004
        assert row[1] == "at"
        assert row[2] == 999
        assert row[3] == "张三"
        assert row[4] == "@UP主 帮忙分析"
        assert row[5] == 5
        assert row[6] == 888
        assert row[7] == 100
        assert row[8] == 200
        assert row[9] == "pending"
        assert row[10] == 1750000001.0


class TestGetPendingTasks:
    """get_pending_tasks() — retrieves pending tasks ordered by created_at."""

    async def test_returns_empty_list_when_no_tasks(self, tmp_db_path: Path) -> None:
        """Should return empty list when no tasks exist."""
        await init_db(tmp_db_path)
        tasks = await get_pending_tasks()
        assert tasks == []

    async def test_returns_only_pending_tasks(self, tmp_db_path: Path) -> None:
        """Should only return tasks with status='pending'."""
        await init_db(tmp_db_path)
        await insert_task(_make_task(msg_id=1, source="reply", status="pending", created_at=100.0))
        await insert_task(_make_task(msg_id=2, source="reply", status="classifying", created_at=200.0))
        await insert_task(_make_task(msg_id=3, source="at", status="pending", created_at=150.0))
        await insert_task(_make_task(msg_id=4, source="reply", status="replied", created_at=300.0))

        tasks = await get_pending_tasks()
        assert len(tasks) == 2
        msg_ids = [t["msg_id"] for t in tasks]
        assert msg_ids == [1, 3]  # ordered by created_at ASC

    async def test_respects_limit(self, tmp_db_path: Path) -> None:
        """Should respect the limit parameter."""
        await init_db(tmp_db_path)
        for i in range(10):
            await insert_task(_make_task(msg_id=i, source="reply", status="pending", created_at=float(i)))

        tasks = await get_pending_tasks(limit=3)
        assert len(tasks) == 3
        assert tasks[0]["msg_id"] == 0
        assert tasks[1]["msg_id"] == 1
        assert tasks[2]["msg_id"] == 2

    async def test_ordered_by_created_at_asc(self, tmp_db_path: Path) -> None:
        """Should return tasks ordered by created_at ASC."""
        await init_db(tmp_db_path)
        await insert_task(_make_task(msg_id=10, source="reply", status="pending", created_at=300.0))
        await insert_task(_make_task(msg_id=11, source="reply", status="pending", created_at=100.0))
        await insert_task(_make_task(msg_id=12, source="reply", status="pending", created_at=200.0))

        tasks = await get_pending_tasks()
        assert [t["msg_id"] for t in tasks] == [11, 12, 10]


class TestUpdateTaskStatus:
    """update_task_status() — updates the status and processed_at."""

    async def test_changes_status(self, tmp_db_path: Path) -> None:
        """Should change the status of a task."""
        await init_db(tmp_db_path)
        await insert_task(_make_task(msg_id=3001, source="reply", status="pending"))

        await update_task_status(3001, "reply", "classifying")

        conn = sqlite3.connect(str(tmp_db_path))
        row = conn.execute(
            "SELECT status FROM tasks WHERE msg_id = 3001 AND source = 'reply'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "classifying"

    async def test_noop_for_nonexistent_task(self, tmp_db_path: Path) -> None:
        """Should not raise when updating a non-existent task."""
        await init_db(tmp_db_path)
        # Should not raise
        await update_task_status(9999, "reply", "failed")


class TestUpdateTaskReply:
    """update_task_reply() — updates reply_method and reply_error."""

    async def test_updates_reply_fields(self, tmp_db_path: Path) -> None:
        """Should update reply_method and reply_error."""
        await init_db(tmp_db_path)
        await insert_task(_make_task(msg_id=4001, source="at", status="pending"))

        await update_task_reply(4001, "at", reply_method="comment", error=None)

        conn = sqlite3.connect(str(tmp_db_path))
        row = conn.execute(
            "SELECT reply_method, reply_error FROM tasks WHERE msg_id = 4001 AND source = 'at'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "comment"
        assert row[1] is None

    async def test_updates_reply_fields_with_error(self, tmp_db_path: Path) -> None:
        """Should set reply_method and reply_error when error occurs."""
        await init_db(tmp_db_path)
        await insert_task(_make_task(msg_id=4002, source="reply", status="pending"))

        await update_task_reply(4002, "reply", reply_method="pm", error="发送失败: 403")

        conn = sqlite3.connect(str(tmp_db_path))
        row = conn.execute(
            "SELECT reply_method, reply_error FROM tasks WHERE msg_id = 4002 AND source = 'reply'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "pm"
        assert row[1] == "发送失败: 403"

    async def test_noop_for_nonexistent_task(self, tmp_db_path: Path) -> None:
        """Should not raise when updating reply on non-existent task."""
        await init_db(tmp_db_path)
        # Should not raise
        await update_task_reply(9999, "reply", reply_method="comment")


class TestCursorState:
    """get_cursor() and set_cursor() — manage cursor state."""

    async def test_get_cursor_returns_none_when_empty(self, tmp_db_path: Path) -> None:
        """get_cursor() should return None when no cursor is set."""
        await init_db(tmp_db_path)
        result = await get_cursor("reply")
        assert result is None

    async def test_set_and_get_cursor_round_trip(self, tmp_db_path: Path) -> None:
        """set_cursor() then get_cursor() should return the same values."""
        await init_db(tmp_db_path)
        await set_cursor("reply", cursor_id=500, cursor_time=1750000000.5)

        result = await get_cursor("reply")
        assert result is not None
        assert result == (500, 1750000000.5)

    async def test_set_cursor_overwrites_existing(self, tmp_db_path: Path) -> None:
        """set_cursor() should overwrite existing cursor for the same source."""
        await init_db(tmp_db_path)
        await set_cursor("reply", cursor_id=100, cursor_time=100.0)
        await set_cursor("reply", cursor_id=200, cursor_time=200.0)

        result = await get_cursor("reply")
        assert result == (200, 200.0)

    async def test_different_sources_independent(self, tmp_db_path: Path) -> None:
        """Cursors for different sources should be independent."""
        await init_db(tmp_db_path)
        await set_cursor("reply", cursor_id=111, cursor_time=111.0)
        await set_cursor("at", cursor_id=222, cursor_time=222.0)

        r1 = await get_cursor("reply")
        r2 = await get_cursor("at")
        assert r1 == (111, 111.0)
        assert r2 == (222, 222.0)
