"""Tests for at_orchestrator.fetcher — msgfeed reply/at polling."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from at_orchestrator.fetcher import Fetcher

# ──────────────────────────────────────────────────────────────────────
# Mock response builders
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
    user_mid: int = 12345,
    user_nickname: str = "测试用户",
    source_content: str = "帮我看看这个视频",
    business_id: int = 1,
    subject_id: int = 67890,
    root_id: int = 0,
    source_id: int = 0,
    reply_time: float = 1700000000.0,
) -> dict:
    """Build a single reply item dict."""
    return {
        "id": msg_id,
        "user": {
            "mid": user_mid,
            "fans": 0,
            "nickname": user_nickname,
            "avatar": "http://example.com/avatar.jpg",
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
    user_mid: int = 54321,
    user_nickname: str = "AT用户",
    source_content: str = "@我看看这个",
    business_id: int = 1,
    subject_id: int = 11111,
    root_id: int = 0,
    source_id: int = 0,
    at_time: float = 1700000100.0,
) -> dict:
    """Build a single at item dict."""
    return {
        "id": msg_id,
        "user": {
            "mid": user_mid,
            "fans": 0,
            "nickname": user_nickname,
            "avatar": "http://example.com/avatar2.jpg",
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
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_client():
    """Return a MagicMock BiliHTTPClient with an async ``get`` method."""
    client = MagicMock()
    client.get = AsyncMock()
    return client


@pytest.fixture
def fetcher(mock_client) -> Fetcher:
    """Return a Fetcher wrapping the mock client."""
    return Fetcher(mock_client)


# ──────────────────────────────────────────────────────────────────────
# AC 1: First fetch (no cursor) returns correct task dicts
# ──────────────────────────────────────────────────────────────────────


class TestFetchReplyMessagesFirstFetch:
    """Acceptance Criterion 1a: First fetch returns correct task dicts."""

    @pytest.mark.asyncio
    async def test_first_fetch_returns_tasks(self, fetcher, mock_client):
        """First fetch (no cursor) parses reply items into task dicts."""
        item = _make_reply_item(
            msg_id=42,
            user_mid=999,
            user_nickname="小明",
            source_content="看看这个视频",
            business_id=1,
            subject_id=88888,
        )
        mock_client.get.return_value = _make_reply_response(items=[item])

        tasks = await fetcher.fetch_reply_messages()

        assert len(tasks) == 1
        task = tasks[0]

        assert task["msg_id"] == 42
        assert task["source"] == "reply"
        assert task["user_mid"] == 999
        assert task["user_nickname"] == "小明"
        assert task["content"] == "看看这个视频"
        assert task["business_id"] == 1
        assert task["subject_id"] == 88888

    @pytest.mark.asyncio
    async def test_first_fetch_no_cursor_passed(self, fetcher, mock_client):
        """First fetch does NOT pass cursor params (fetches latest page)."""
        mock_client.get.return_value = _make_reply_response(items=[])

        await fetcher.fetch_reply_messages()

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        params = call_args[1].get("params")
        # First fetch: params should be None (no cursor) — fetches latest page
        assert params is None

    @pytest.mark.asyncio
    async def test_first_fetch_preserves_optional_fields(self, fetcher, mock_client):
        """Non-zero root_id and source_id are preserved; zero → None."""
        item = _make_reply_item(root_id=555, source_id=0)
        mock_client.get.return_value = _make_reply_response(items=[item])

        tasks = await fetcher.fetch_reply_messages()

        assert tasks[0]["root_id"] == 555
        assert tasks[0]["source_id"] is None  # 0 → None

    @pytest.mark.asyncio
    async def test_first_fetch_sets_status_and_timestamps(self, fetcher, mock_client):
        """Returned dicts include status='pending' and a float created_at."""
        mock_client.get.return_value = _make_reply_response(
            items=[_make_reply_item()]
        )

        before = time.time()
        tasks = await fetcher.fetch_reply_messages()
        after = time.time()

        task = tasks[0]
        assert task["status"] == "pending"
        assert isinstance(task["created_at"], float)
        assert before - 1 <= task["created_at"] <= after + 1
        assert task["processed_at"] is None
        assert task["reply_method"] is None
        assert task["reply_error"] is None


class TestFetchAtMessagesFirstFetch:
    """Acceptance Criterion 1b: First fetch at messages."""

    @pytest.mark.asyncio
    async def test_first_fetch_returns_tasks(self, fetcher, mock_client):
        """First fetch (no cursor) parses at items into task dicts."""
        item = _make_at_item(
            msg_id=7,
            user_mid=444,
            user_nickname="AT用户",
            source_content="@我测试",
            business_id=11,
            subject_id=22222,
        )
        mock_client.get.return_value = _make_at_response(items=[item])

        tasks = await fetcher.fetch_at_messages()

        assert len(tasks) == 1
        task = tasks[0]

        assert task["msg_id"] == 7
        assert task["source"] == "at"
        assert task["user_mid"] == 444
        assert task["user_nickname"] == "AT用户"
        assert task["content"] == "@我测试"
        assert task["business_id"] == 11
        assert task["subject_id"] == 22222

    @pytest.mark.asyncio
    async def test_first_fetch_no_cursor_passed(self, fetcher, mock_client):
        """First fetch does NOT pass cursor params to at endpoint."""
        mock_client.get.return_value = _make_at_response(items=[])

        await fetcher.fetch_at_messages()

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        params = call_args[1].get("params")
        assert params is None


# ──────────────────────────────────────────────────────────────────────
# AC 2: Cursor pagination
# ──────────────────────────────────────────────────────────────────────


class TestFetchReplyCursorPagination:
    """Acceptance Criterion 2a: Cursor pagination for reply messages."""

    @pytest.mark.asyncio
    async def test_passes_cursor_params(self, fetcher, mock_client):
        """When cursor_id and cursor_time are provided, pass them as params."""
        mock_client.get.return_value = _make_reply_response(items=[])

        await fetcher.fetch_reply_messages(cursor_id=123, cursor_time=1700000000.0)

        call_args = mock_client.get.call_args
        params = call_args[1]["params"]
        assert params["id"] == 123
        assert params["reply_time"] == 1700000000.0

    @pytest.mark.asyncio
    async def test_returns_cursor_info_in_tasks(self, fetcher, mock_client):
        """Each task dict records cursor_id and cursor_time from response."""
        mock_client.get.return_value = _make_reply_response(
            cursor_id=555,
            cursor_time=1710000000.0,
            items=[_make_reply_item()],
        )

        tasks = await fetcher.fetch_reply_messages(cursor_id=100, cursor_time=1700000000.0)

        task = tasks[0]
        assert task["cursor_id"] == 555
        assert task["cursor_time"] == 1710000000.0


class TestFetchAtCursorPagination:
    """Acceptance Criterion 2b: Cursor pagination for at messages."""

    @pytest.mark.asyncio
    async def test_passes_cursor_params(self, fetcher, mock_client):
        """When cursor_id and cursor_time are provided, pass them as params."""
        mock_client.get.return_value = _make_at_response(items=[])

        await fetcher.fetch_at_messages(cursor_id=456, cursor_time=1710000000.0)

        call_args = mock_client.get.call_args
        params = call_args[1]["params"]
        assert params["id"] == 456
        assert params["at_time"] == 1710000000.0


# ──────────────────────────────────────────────────────────────────────
# AC 3: Empty / missing responses
# ──────────────────────────────────────────────────────────────────────


class TestEmptyResponses:
    """Acceptance Criterion 3: Handle empty responses gracefully."""

    @pytest.mark.asyncio
    async def test_no_items_returns_empty_list_reply(self, fetcher, mock_client):
        """Empty items → empty list, no crash."""
        mock_client.get.return_value = _make_reply_response(items=[])

        tasks = await fetcher.fetch_reply_messages()

        assert tasks == []

    @pytest.mark.asyncio
    async def test_no_items_returns_empty_list_at(self, fetcher, mock_client):
        """Empty items → empty list for at endpoint."""
        mock_client.get.return_value = _make_at_response(items=[])

        tasks = await fetcher.fetch_at_messages()

        assert tasks == []

    @pytest.mark.asyncio
    async def test_no_data_key_returns_empty_list(self, fetcher, mock_client):
        """Response missing 'data' key → empty list."""
        mock_client.get.return_value = {"code": 0, "message": "ok"}

        tasks = await fetcher.fetch_reply_messages()

        assert tasks == []

    @pytest.mark.asyncio
    async def test_data_not_a_dict_returns_empty_list(self, fetcher, mock_client):
        """Response data is None → empty list."""
        mock_client.get.return_value = {"code": 0, "data": None}

        tasks = await fetcher.fetch_reply_messages()

        assert tasks == []

    @pytest.mark.asyncio
    async def test_nonzero_error_code_returns_empty_list(self, fetcher, mock_client):
        """Nonzero error code → empty list."""
        mock_client.get.return_value = {
            "code": -101,
            "message": "not logged in",
        }

        tasks = await fetcher.fetch_reply_messages()

        assert tasks == []


# ──────────────────────────────────────────────────────────────────────
# AC 4: Graceful handling of missing item fields (.get fallback)
# ──────────────────────────────────────────────────────────────────────


class TestMissingFields:
    """Acceptance Criterion 4: Missing fields → safe defaults via .get()."""

    @pytest.mark.asyncio
    async def test_missing_item_id(self, fetcher, mock_client):
        """Missing item id → None (no crash)."""
        items = [{"user": {"mid": 1, "nickname": "test"}, "item": {"source_content": "x", "business_id": 1, "subject_id": 1, "root_id": 0, "source_id": 0}}]
        mock_client.get.return_value = _make_reply_response(items=items)

        tasks = await fetcher.fetch_reply_messages()

        assert tasks[0]["msg_id"] is None

    @pytest.mark.asyncio
    async def test_missing_user_fields(self, fetcher, mock_client):
        """Missing user.mid and user.nickname → None defaults."""
        items = [{"id": 1, "user": {}, "item": {"source_content": "x", "business_id": 1, "subject_id": 1, "root_id": 0, "source_id": 0}}]
        mock_client.get.return_value = _make_reply_response(items=items)

        tasks = await fetcher.fetch_reply_messages()

        assert tasks[0]["user_mid"] is None
        assert tasks[0]["user_nickname"] is None

    @pytest.mark.asyncio
    async def test_missing_source_content(self, fetcher, mock_client):
        """Missing item.source_content → empty string."""
        items = [{"id": 1, "user": {"mid": 1, "nickname": "a"}, "item": {"business_id": 1, "subject_id": 1, "root_id": 0, "source_id": 0}}]
        mock_client.get.return_value = _make_reply_response(items=items)

        tasks = await fetcher.fetch_reply_messages()

        assert tasks[0]["content"] == ""

    @pytest.mark.asyncio
    async def test_missing_business_id_and_subject_id(self, fetcher, mock_client):
        """Missing business_id/subject_id → 0."""
        items = [{"id": 1, "user": {"mid": 1, "nickname": "a"}, "item": {"source_content": "x", "root_id": 0, "source_id": 0}}]
        mock_client.get.return_value = _make_reply_response(items=items)

        tasks = await fetcher.fetch_reply_messages()

        assert tasks[0]["business_id"] == 0
        assert tasks[0]["subject_id"] == 0

    @pytest.mark.asyncio
    async def test_missing_root_id_and_source_id(self, fetcher, mock_client):
        """Missing root_id/source_id → None (same as 0 behaviour)."""
        items = [{"id": 1, "user": {"mid": 1, "nickname": "a"}, "item": {"source_content": "x", "business_id": 1, "subject_id": 1}}]
        mock_client.get.return_value = _make_reply_response(items=items)

        tasks = await fetcher.fetch_reply_messages()

        assert tasks[0]["root_id"] is None
        assert tasks[0]["source_id"] is None

    @pytest.mark.asyncio
    async def test_missing_cursor_in_response(self, fetcher, mock_client):
        """Missing cursor in response → cursor_id/cursor_time are None."""
        mock_client.get.return_value = {
            "code": 0,
            "data": {"items": [_make_reply_item()]},
        }

        tasks = await fetcher.fetch_reply_messages()

        assert tasks[0]["cursor_id"] is None
        assert tasks[0]["cursor_time"] is None
