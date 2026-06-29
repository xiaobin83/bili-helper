"""Integration tests for Fetcher + SQLite + msgfeed mock API.

Tests the full flow: Fetcher polls the B站 msgfeed API (mocked), db module
stores/retrieves tasks and cursors.  No real B站 API calls are made.

Coverage:
    1. Fetch reply/at → write to db (count, all fields)
    2. Duplicate fetch dedup (same cursor → no duplicate rows)
    3. Dual source independence (reply + at cursors, source field)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from at_orchestrator.db import (
    get_cursor,
    get_pending_tasks,
    init_db,
    insert_task,
    set_cursor,
)
from at_orchestrator.fetcher import Fetcher


# ──────────────────────────────────────────────────────────────────────
# Mock response builders (self-contained, matching real API shapes)
# ──────────────────────────────────────────────────────────────────────


def _make_reply_response(
    *,
    cursor_id: int = 100,
    cursor_time: float = 1700000000.0,
    is_end: bool = True,
    items: list | None = None,
) -> dict:
    """Build a msgfeed/reply API response dict."""
    return {
        "code": 0,
        "message": "0",
        "data": {
            "cursor": {
                "id": cursor_id,
                "time": cursor_time,
                "is_end": is_end,
            },
            "items": items if items is not None else [],
        },
    }


def _make_at_response(
    *,
    cursor_id: int = 200,
    cursor_time: float = 1700000100.0,
    is_end: bool = True,
    items: list | None = None,
) -> dict:
    """Build a msgfeed/at API response dict."""
    return {
        "code": 0,
        "message": "OK",
        "data": {
            "cursor": {
                "id": cursor_id,
                "time": cursor_time,
                "is_end": is_end,
            },
            "items": items if items is not None else [],
        },
    }


def _make_reply_item(
    *,
    msg_id: int = 1,
    user_mid: int = 123,
    user_nickname: str = "用户A",
    source_content: str = "帮我分析这个视频",
    business_id: int = 1,
    subject_id: int = 456,
    root_id: int = 0,
    source_id: int = 0,
    reply_time: float = 1700000000.0,
) -> dict:
    """Build a single reply item dict matching the msgfeed reply schema."""
    return {
        "id": msg_id,
        "user": {
            "mid": user_mid,
            "fans": 0,
            "nickname": user_nickname,
            "avatar": "",
            "mid_link": "",
            "follow": False,
        },
        "item": {
            "subject_id": subject_id,
            "root_id": root_id,
            "source_id": source_id,
            "target_id": 0,
            "type": "reply",
            "business_id": business_id,
            "business": "评论",
            "title": "视频标题",
            "image": "",
            "uri": f"https://www.bilibili.com/video/BV1xx{subject_id}",
            "source_content": source_content,
            "at_details": [],
            "topic_details": [],
            "hide_reply_button": False,
            "hide_like_button": False,
            "like_state": 0,
            "danmu": None,
            "message": "",
        },
        "counts": 1,
        "is_multi": 0,
        "reply_time": reply_time,
    }


def _make_at_item(
    *,
    msg_id: int = 2,
    user_mid: int = 456,
    user_nickname: str = "AT用户",
    source_content: str = "@我 看看这个",
    business_id: int = 1,
    subject_id: int = 789,
    root_id: int = 0,
    source_id: int = 0,
    at_time: float = 1700000100.0,
) -> dict:
    """Build a single at item dict matching the msgfeed at schema."""
    return {
        "id": msg_id,
        "user": {
            "mid": user_mid,
            "fans": 0,
            "nickname": user_nickname,
            "avatar": "",
            "mid_link": "",
            "follow": False,
        },
        "item": {
            "subject_id": subject_id,
            "root_id": root_id,
            "source_id": source_id,
            "target_id": 0,
            "type": "reply",
            "business_id": business_id,
            "business": "评论",
            "title": "AT视频标题",
            "image": "",
            "uri": f"https://www.bilibili.com/video/BV1xx{subject_id}",
            "native_uri": f"bilibili://video/{subject_id}",
            "source_content": source_content,
            "at_details": [
                {
                    "mid": user_mid,
                    "fans": 0,
                    "nickname": user_nickname,
                    "avatar": "",
                    "mid_link": "",
                    "follow": False,
                }
            ],
            "topic_details": [],
            "hide_reply_button": False,
        },
        "at_time": at_time,
    }


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _count_tasks(db_path: Path) -> int:
    """Return the total number of rows in the tasks table."""
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_http() -> MagicMock:
    """Return a MagicMock BiliHTTPClient with an async ``get`` method."""
    client = MagicMock()
    client.get = AsyncMock()
    return client


# ══════════════════════════════════════════════════════════════════════
# Scenario 1: Fetcher + SQLite integration — tasks written correctly
# ══════════════════════════════════════════════════════════════════════


class TestFetchWriteToDb:
    """Fetcher returns task dicts → db writes them with all fields intact."""

    @pytest.mark.asyncio
    async def test_fetch_reply_writes_all_fields(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """Reply tasks fetched are inserted into db with every field correct."""
        await init_db(tmp_db_path)

        item = _make_reply_item(
            msg_id=42,
            user_mid=999,
            user_nickname="小明",
            source_content="帮我分析这个视频",
            business_id=1,
            subject_id=88888,
        )
        mock_http.get.return_value = _make_reply_response(
            cursor_id=555,
            cursor_time=1710000000.0,
            items=[item],
        )
        fetcher = Fetcher(mock_http)

        tasks = await fetcher.fetch_reply_messages()
        assert len(tasks) == 1

        inserted = await insert_task(tasks[0])
        assert inserted is True

        # Verify via db public API
        pending = await get_pending_tasks()
        assert len(pending) == 1
        t = pending[0]
        assert t["msg_id"] == 42
        assert t["source"] == "reply"
        assert t["user_mid"] == 999
        assert t["user_nickname"] == "小明"
        assert t["content"] == "帮我分析这个视频"
        assert t["business_id"] == 1
        assert t["subject_id"] == 88888
        assert t["root_id"] is None  # 0 → None
        assert t["source_id"] is None  # 0 → None
        assert t["status"] == "pending"
        assert isinstance(t["created_at"], float)
        assert t["processed_at"] is None
        assert t["reply_method"] is None
        assert t["reply_error"] is None
        assert t["cursor_id"] == 555
        assert t["cursor_time"] == 1710000000.0

    @pytest.mark.asyncio
    async def test_fetch_reply_multiple_items(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """Multiple reply items → all inserted, correct count."""
        await init_db(tmp_db_path)

        items = [
            _make_reply_item(msg_id=1, user_mid=100, source_content="第一条"),
            _make_reply_item(msg_id=2, user_mid=200, source_content="第二条"),
            _make_reply_item(msg_id=3, user_mid=300, source_content="第三条"),
        ]
        mock_http.get.return_value = _make_reply_response(items=items)
        fetcher = Fetcher(mock_http)

        tasks = await fetcher.fetch_reply_messages()
        assert len(tasks) == 3

        for task in tasks:
            result = await insert_task(task)
            assert result is True

        pending = await get_pending_tasks(limit=10)
        assert len(pending) == 3
        assert {t["msg_id"] for t in pending} == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_fetch_at_writes_correct_source(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """AT tasks have source='at' in db, not 'reply'."""
        await init_db(tmp_db_path)

        item = _make_at_item(
            msg_id=7,
            user_mid=444,
            user_nickname="AT用户",
            source_content="@我 测试",
            business_id=11,
            subject_id=22222,
        )
        mock_http.get.return_value = _make_at_response(
            cursor_id=300,
            cursor_time=1720000000.0,
            items=[item],
        )
        fetcher = Fetcher(mock_http)

        tasks = await fetcher.fetch_at_messages()
        assert len(tasks) == 1

        await insert_task(tasks[0])

        pending = await get_pending_tasks()
        assert len(pending) == 1
        t = pending[0]
        assert t["msg_id"] == 7
        assert t["source"] == "at"
        assert t["user_nickname"] == "AT用户"
        assert t["content"] == "@我 测试"
        assert t["cursor_id"] == 300
        assert t["cursor_time"] == 1720000000.0

    @pytest.mark.asyncio
    async def test_nonzero_root_and_source_ids_preserved(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """Non-zero root_id and source_id are stored; zero → None."""
        await init_db(tmp_db_path)

        item = _make_reply_item(msg_id=99, root_id=12345, source_id=67890)
        mock_http.get.return_value = _make_reply_response(items=[item])
        fetcher = Fetcher(mock_http)

        tasks = await fetcher.fetch_reply_messages()
        await insert_task(tasks[0])

        pending = await get_pending_tasks()
        assert pending[0]["root_id"] == 12345
        assert pending[0]["source_id"] == 67890


# ══════════════════════════════════════════════════════════════════════
# Scenario 2: Duplicate fetch dedup — second fetch doesn't create dupes
# ══════════════════════════════════════════════════════════════════════


class TestDuplicateFetchDedup:
    """Second fetch with same data → db ignores duplicates (UNIQUE constraint)."""

    @pytest.mark.asyncio
    async def test_second_fetch_same_cursor_no_duplicates(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """Fetch → insert → fetch again (same data) → count unchanged."""
        await init_db(tmp_db_path)

        items = [
            _make_reply_item(msg_id=10, source_content="消息A"),
            _make_reply_item(msg_id=11, source_content="消息B"),
        ]
        mock_http.get.return_value = _make_reply_response(
            cursor_id=100, cursor_time=1700000000.0, items=items,
        )
        fetcher = Fetcher(mock_http)

        # First fetch — insert everything
        tasks_1 = await fetcher.fetch_reply_messages()
        for task in tasks_1:
            await insert_task(task)

        assert await get_pending_tasks()  # confirm something was inserted
        first_count = _count_tasks(tmp_db_path)
        assert first_count == 2

        # Second fetch — same mock response, try inserting again
        tasks_2 = await fetcher.fetch_reply_messages()
        for task in tasks_2:
            result = await insert_task(task)
            assert result is False  # each insert should be rejected

        second_count = _count_tasks(tmp_db_path)
        assert second_count == 2  # no new rows

    @pytest.mark.asyncio
    async def test_insert_task_returns_false_on_duplicate(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """insert_task() returns False when (msg_id, source) already exists."""
        await init_db(tmp_db_path)

        item = _make_reply_item(msg_id=55, source_content="唯一消息")
        mock_http.get.return_value = _make_reply_response(items=[item])
        fetcher = Fetcher(mock_http)

        task = (await fetcher.fetch_reply_messages())[0]

        r1 = await insert_task(task)
        r2 = await insert_task(task)  # same (msg_id, source)

        assert r1 is True
        assert r2 is False
        assert _count_tasks(tmp_db_path) == 1

    @pytest.mark.asyncio
    async def test_partial_overlap_only_inserts_new(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """When some items are new and some are dupes, only new ones go in."""
        await init_db(tmp_db_path)

        mock_http.get.return_value = _make_reply_response(
            cursor_id=100,
            cursor_time=1700000000.0,
            items=[
                _make_reply_item(msg_id=100, source_content="旧消息"),
                _make_reply_item(msg_id=101, source_content="也旧"),
            ],
        )
        fetcher = Fetcher(mock_http)

        # First fetch: insert both
        for task in await fetcher.fetch_reply_messages():
            await insert_task(task)
        assert _count_tasks(tmp_db_path) == 2

        # Second fetch: one old + one new
        mock_http.get.return_value = _make_reply_response(
            cursor_id=200,
            cursor_time=1710000000.0,
            items=[
                _make_reply_item(msg_id=100, source_content="旧消息"),  # dupe
                _make_reply_item(msg_id=102, source_content="新消息"),  # new
            ],
        )

        results = []
        for task in await fetcher.fetch_reply_messages():
            results.append(await insert_task(task))

        assert results == [False, True]  # first dupe, second new
        assert _count_tasks(tmp_db_path) == 3


# ══════════════════════════════════════════════════════════════════════
# Scenario 3: Dual source (reply + at) — source field + cursor independence
# ══════════════════════════════════════════════════════════════════════


class TestDualSourceIndependence:
    """Reply and AT sources write independently, with separate cursors."""

    @pytest.mark.asyncio
    async def test_reply_and_at_tasks_have_correct_source(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """After fetching both sources, each task records its correct source."""
        await init_db(tmp_db_path)

        # Set up mock to return different responses per URL
        async def _mock_get(url, **kwargs):
            if "reply" in url:
                return _make_reply_response(
                    cursor_id=100,
                    cursor_time=1700000000.0,
                    items=[
                        _make_reply_item(msg_id=1, source_content="回复消息"),
                        _make_reply_item(msg_id=2, source_content="另一条回复"),
                    ],
                )
            if "at" in url:
                return _make_at_response(
                    cursor_id=200,
                    cursor_time=1700000100.0,
                    items=[
                        _make_at_item(msg_id=3, source_content="@我 消息"),
                    ],
                )
            return {"code": -1}

        mock_http.get = _mock_get
        fetcher = Fetcher(mock_http)

        # Fetch both sources
        reply_tasks = await fetcher.fetch_reply_messages()
        at_tasks = await fetcher.fetch_at_messages()

        assert len(reply_tasks) == 2
        assert len(at_tasks) == 1

        for task in reply_tasks + at_tasks:
            await insert_task(task)

        pending = await get_pending_tasks(limit=10)
        assert len(pending) == 3

        # Verify source field
        sources = {t["source"] for t in pending}
        assert sources == {"reply", "at"}

        reply_msgs = [t for t in pending if t["source"] == "reply"]
        at_msgs = [t for t in pending if t["source"] == "at"]
        assert len(reply_msgs) == 2
        assert len(at_msgs) == 1
        assert at_msgs[0]["msg_id"] == 3
        assert at_msgs[0]["content"] == "@我 消息"

    @pytest.mark.asyncio
    async def test_reply_and_at_cursors_are_independent(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """Setting cursors for reply and at stores them independently."""
        await init_db(tmp_db_path)

        # Simulate fetching reply → save its cursor
        item = _make_reply_item(msg_id=1)
        mock_http.get.return_value = _make_reply_response(
            cursor_id=111, cursor_time=1711111111.0, items=[item],
        )
        fetcher = Fetcher(mock_http)
        tasks = await fetcher.fetch_reply_messages()
        await insert_task(tasks[0])
        await set_cursor("reply", cursor_id=111, cursor_time=1711111111.0)

        # Simulate fetching at → save its cursor (different values)
        item2 = _make_at_item(msg_id=2)
        mock_http.get.return_value = _make_at_response(
            cursor_id=222, cursor_time=1722222222.0, items=[item2],
        )
        tasks = await fetcher.fetch_at_messages()
        await insert_task(tasks[0])
        await set_cursor("at", cursor_id=222, cursor_time=1722222222.0)

        # Cursors are independent
        reply_cursor = await get_cursor("reply")
        at_cursor = await get_cursor("at")

        assert reply_cursor == (111, 1711111111.0)
        assert at_cursor == (222, 1722222222.0)
        assert reply_cursor != at_cursor

    @pytest.mark.asyncio
    async def test_same_msg_id_different_source_not_duplicate(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """Same msg_id with different source → both inserted (UNIQUE on both cols)."""
        await init_db(tmp_db_path)

        msg_id = 9999

        # Insert reply task
        mock_http.get.return_value = _make_reply_response(
            items=[_make_reply_item(msg_id=msg_id, source_content="回复")]
        )
        fetcher = Fetcher(mock_http)
        reply_task = (await fetcher.fetch_reply_messages())[0]
        r1 = await insert_task(reply_task)
        assert r1 is True

        # Insert at task with same msg_id but source="at"
        mock_http.get.return_value = _make_at_response(
            items=[_make_at_item(msg_id=msg_id, source_content="@消息")]
        )
        at_task = (await fetcher.fetch_at_messages())[0]
        r2 = await insert_task(at_task)
        assert r2 is True  # NOT a duplicate — different source

        assert _count_tasks(tmp_db_path) == 2

    @pytest.mark.asyncio
    async def test_cursor_overwrite_per_source(
        self, tmp_db_path: Path
    ) -> None:
        """set_cursor overwrites existing cursor for same source only."""
        await init_db(tmp_db_path)

        await set_cursor("reply", cursor_id=100, cursor_time=100.0)
        await set_cursor("at", cursor_id=200, cursor_time=200.0)

        # Overwrite reply cursor
        await set_cursor("reply", cursor_id=150, cursor_time=150.0)

        # reply cursor updated, at cursor unchanged
        assert await get_cursor("reply") == (150, 150.0)
        assert await get_cursor("at") == (200, 200.0)

    @pytest.mark.asyncio
    async def test_empty_fetch_does_not_crash_db(
        self, tmp_db_path: Path, mock_http: MagicMock
    ) -> None:
        """Empty response from both sources → no tasks inserted, no crash."""
        await init_db(tmp_db_path)

        mock_http.get.return_value = _make_reply_response(items=[])
        fetcher = Fetcher(mock_http)

        reply_tasks = await fetcher.fetch_reply_messages()
        assert reply_tasks == []

        mock_http.get.return_value = _make_at_response(items=[])
        at_tasks = await fetcher.fetch_at_messages()
        assert at_tasks == []

        # Nothing crashed, no tasks inserted
        pending = await get_pending_tasks()
        assert pending == []
