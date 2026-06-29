"""Fetcher — pulls @ mention and reply notifications from Bilibili msgfeed API.

Returns task dicts ready for db insertion. Does NOT call db directly.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bili_core.http_client import BiliHTTPClient

_API_BASE = "https://api.bilibili.com"
_REPLY_URL = f"{_API_BASE}/x/msgfeed/reply"
_AT_URL = f"{_API_BASE}/x/msgfeed/at"


def _zero_to_none(value: int | None) -> int | None:
    """Convert ``0`` to ``None`` (Bilibili uses 0 for missing IDs)."""
    if value is None:
        return None
    return None if value == 0 else value


class Fetcher:
    """Polls Bilibili msgfeed endpoints for AT and reply notifications.

    Each method returns a list of ``dict`` objects matching the db schema,
    ready for ``insert_task()``.  The caller is responsible for db storage.
    """

    __slots__ = ("_client",)

    def __init__(self, client: BiliHTTPClient) -> None:
        self._client = client

    # ── Public API ────────────────────────────────────────────────────

    async def fetch_reply_messages(
        self,
        cursor_id: int | None = None,
        cursor_time: float | None = None,
    ) -> list[dict]:
        """Fetch reply notifications from ``/x/msgfeed/reply``.

        When *cursor_id* and *cursor_time* are ``None`` (first run), only
        the latest page is fetched.  Otherwise, pass them as query params
        for cursor-based pagination.
        """
        params: dict = {}
        if cursor_id is not None and cursor_time is not None:
            params["id"] = cursor_id
            params["reply_time"] = int(cursor_time)

        response = await self._client.get(_REPLY_URL, params=params if params else None)
        return self._parse_items(response, source="reply")

    async def fetch_at_messages(
        self,
        cursor_id: int | None = None,
        cursor_time: float | None = None,
    ) -> list[dict]:
        """Fetch AT (@) notifications from ``/x/msgfeed/at``.

        Pagination behaviour is the same as ``fetch_reply_messages()``.
        """
        params: dict = {}
        if cursor_id is not None and cursor_time is not None:
            params["id"] = cursor_id
            params["at_time"] = int(cursor_time)

        response = await self._client.get(_AT_URL, params=params if params else None)
        return self._parse_items(response, source="at")

    # ── Parsing helpers ───────────────────────────────────────────────

    def _parse_items(self, response: dict, *, source: str) -> list[dict]:
        """Parse a msgfeed response into a list of task dicts.

        Handles missing / malformed responses gracefully: returns ``[]``
        for any unexpected shape.
        """
        if response.get("code") != 0:
            return []

        data = response.get("data")
        if not isinstance(data, dict):
            return []

        items: list = data.get("items")  # type: ignore[assignment]
        if not isinstance(items, list):
            return []

        cursor: dict = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}  # type: ignore[assignment]
        cursor_id = cursor.get("id")
        cursor_time = cursor.get("time")

        now = time.time()
        tasks: list[dict] = []

        for item in items:
            if not isinstance(item, dict):
                continue

            user: dict = item.get("user") if isinstance(item.get("user"), dict) else {}  # type: ignore[assignment]
            detail: dict = item.get("item") if isinstance(item.get("item"), dict) else {}  # type: ignore[assignment]

            tasks.append(
                {
                    "msg_id": item.get("id"),
                    "source": source,
                    "user_mid": user.get("mid"),
                    "user_nickname": user.get("nickname"),
                    "content": detail.get("source_content", ""),
                    "business_id": detail.get("business_id", 0),
                    "subject_id": detail.get("subject_id", 0),
                    "root_id": _zero_to_none(detail.get("root_id")),
                    "source_id": _zero_to_none(detail.get("source_id")),
                    "status": "pending",
                    "created_at": now,
                    "processed_at": None,
                    "reply_method": None,
                    "reply_error": None,
                    "cursor_id": cursor_id,
                    "cursor_time": cursor_time,
                }
            )

        return tasks
