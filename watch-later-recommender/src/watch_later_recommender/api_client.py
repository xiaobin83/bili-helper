"""API client wrapping bili-core for 3 video sources + toview operations.

Usage:
    async with BiliAPIClient(creds) as client:
        popular = await client.fetch_popular(ps=5)
        ranking = await client.fetch_ranking()
"""

from __future__ import annotations

import logging
from typing import Any

from bili_core.auth import Credentials
from bili_core.http_client import BiliHTTPClient
from bili_core.signing import sign_params

from watch_later_recommender.models import Folder, VideoItem

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bilibili.com"


class BiliAPIClient:
    """Async client for B站 video source and toview APIs.

    Args:
        creds: Optional ``Credentials``. When ``None``, creates an anonymous
            client (public endpoints only — ``fetch_rcmd``, ``fetch_toview_list``,
            and ``add_to_toview`` will gracefully return ``[]`` or error).
    """

    def __init__(self, creds: Credentials | None = None) -> None:
        sessdata = creds.sessdata if creds else ""
        bili_jct = creds.bili_jct if creds else ""
        buvid3 = creds.buvid3 if creds else ""
        self._has_auth = bool(creds and creds.sessdata)
        self._bili_jct = bili_jct
        self._client = BiliHTTPClient(
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
            min_interval=2.0,
        )

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> BiliAPIClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Source 1: Popular (全站热门)
    # ------------------------------------------------------------------

    async def fetch_popular(self, pn: int = 1, ps: int = 50) -> list[VideoItem]:
        """Fetch current trending/popular videos. No auth required."""
        try:
            raw = await self._client.get(
                f"{BASE_URL}/x/web-interface/popular",
                params={"pn": pn, "ps": ps},
            )
            if raw.get("code") != 0:
                logger.warning("fetch_popular: code=%s", raw.get("code"))
                return []
            items = (raw.get("data") or {}).get("list") or []
            return [self._parse_video_item(item) for item in items]
        except Exception as e:
            logger.warning("fetch_popular failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Source 2: Ranking (分区排行榜)
    # ------------------------------------------------------------------

    async def fetch_ranking(self, rid: int | None = None, type: str = "all") -> list[VideoItem]:
        """Fetch top 100 ranked videos. No auth required.

        Args:
            rid: Optional partition ID. When ``None``, fetches 全站排行榜.
            type: Always ``"all"`` (only known valid value).
        """
        try:
            params: dict[str, Any] = {"type": type}
            if rid is not None:
                params["rid"] = rid
            raw = await self._client.get(
                f"{BASE_URL}/x/web-interface/ranking/v2",
                params=params,
            )
            if raw.get("code") != 0:
                logger.warning("fetch_ranking: code=%s", raw.get("code"))
                return []
            items = (raw.get("data") or {}).get("list") or []
            return [self._parse_video_item(item) for item in items]
        except Exception as e:
            logger.warning("fetch_ranking failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Source 3: Recommended (首页个性化推荐)
    # ------------------------------------------------------------------

    async def fetch_rcmd(self, fresh_type: int = 3, ps: int = 14) -> list[VideoItem]:
        """Fetch personalized recommendations. Requires auth (Cookie).

        Returns ``[]`` if no credentials available (graceful degradation).

        **CRITICAL**: This API returns ``id`` instead of ``aid``.
        Stat is truncated (only view/like/danmaku).
        Only items with ``goto == "av"`` (video type) are included.
        """
        if not self._has_auth:
            logger.info("fetch_rcmd: skipped (no auth)")
            return []

        try:
            raw = await self._client.get(
                f"{BASE_URL}/x/web-interface/index/top/rcmd",
                params={"fresh_type": fresh_type, "version": 1, "ps": ps},
            )
            if raw.get("code") != 0:
                logger.warning("fetch_rcmd: code=%s", raw.get("code"))
                return []
            data = raw.get("data") or {}
            items = data.get("item") or []
            results: list[VideoItem] = []
            for item in items:
                if item.get("goto") != "av":
                    continue  # skip non-video entries
                results.append(self._parse_rcmd_item(item))
            return results
        except Exception as e:
            logger.warning("fetch_rcmd failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Toview: List (获取稍后再看列表，用于去重)
    # ------------------------------------------------------------------

    async def fetch_toview_list(self) -> list[dict]:
        """Fetch current watch-later list for deduplication. Requires auth.

        Returns ``[]`` if no credentials available.

        Each item dict has ``aid`` and ``bvid`` keys.
        """
        if not self._has_auth:
            logger.info("fetch_toview_list: skipped (no auth)")
            return []

        try:
            raw = await self._client.get(
                f"{BASE_URL}/x/v2/history/toview",
            )
            if raw.get("code") != 0:
                return []
            return (raw.get("data") or {}).get("list") or []
        except Exception as e:
            logger.warning("fetch_toview_list failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Toview: Add (添加到稍后再看)
    # ------------------------------------------------------------------

    async def add_to_toview(self, aid: int) -> dict:
        """Add a video to the watch-later list. Requires auth.

        Args:
            aid: Video avid to add.

        Returns:
            Response dict with ``code`` and ``message`` keys.
            Error codes: 90001 (list full), 90003 (video deleted).
        """
        if not self._has_auth:
            return {"code": -1, "message": "未登录，无法添加到稍后再看"}

        try:
            raw = await self._client.post(
                f"{BASE_URL}/x/v2/history/toview/add",
                data={"aid": aid},
            )
            return {"code": raw.get("code", -1), "message": raw.get("message", "")}
        except Exception as e:
            logger.warning("add_to_toview failed: %s", e)
            return {"code": -1, "message": str(e)}

    # ------------------------------------------------------------------
    # Signed GET helper
    # ------------------------------------------------------------------

    async def _signed_get(self, path: str, params: dict | None = None) -> dict:
        """GET with Wbi signature. Requires auth."""
        raw_params = dict(params or {})
        signed = sign_params(raw_params)
        return await self._client.get(f"{BASE_URL}{path}", params=signed)

    # ------------------------------------------------------------------
    # User info
    # ------------------------------------------------------------------

    async def get_current_mid(self) -> int:
        """Get current user's numeric mid from nav endpoint. Requires auth."""
        if not self._has_auth:
            return 0
        try:
            raw = await self._client.get(f"{BASE_URL}/x/web-interface/nav")
            data = raw.get("data") or {}
            return data.get("mid") or data.get("mid_plus") or 0
        except Exception as e:
            logger.warning("get_current_mid failed: %s", e)
            return 0

    # ------------------------------------------------------------------
    # Favorites folder operations
    # ------------------------------------------------------------------

    async def list_fav_folders(self, up_mid: int) -> list[Folder]:
        """Fetch all favorites folders for the user. Requires auth + Wbi.

        Args:
            up_mid: User's numeric mid.

        Returns:
            List of Folder objects. Empty list on error.
        """
        if not self._has_auth:
            logger.info("list_fav_folders: skipped (no auth)")
            return []

        try:
            raw = await self._signed_get(
                "/x/v3/fav/folder/created/list-all",
                {"up_mid": up_mid},
            )
            if raw.get("code") != 0:
                logger.warning("list_fav_folders: code=%s", raw.get("code"))
                return []
            data = raw.get("data")
            if not data or not isinstance(data, dict):
                return []
            folder_dicts = data.get("list", []) or []
            return [Folder(**f) for f in folder_dicts]
        except Exception as e:
            logger.warning("list_fav_folders failed: %s", e)
            return []

    async def add_to_fav_folder(self, aid: int, add_media_ids: list[int]) -> dict:
        """Add a video to one or more favorites folders. Requires auth.

        Args:
            aid: Video avid.
            add_media_ids: Target folder media_id(s).

        Returns:
            Dict with code and message.
        """
        if not self._has_auth:
            return {"code": -1, "message": "未登录，无法添加到收藏夹"}

        try:
            raw = await self._client.post(
                f"{BASE_URL}/x/v3/fav/resource/add",
                data={
                    "resources": f"{aid}:2",  # type=2 for video
                    "add_media_ids": ",".join(str(m) for m in add_media_ids),
                },
            )
            return {"code": raw.get("code", -1), "message": raw.get("message", "")}
        except Exception as e:
            logger.warning("add_to_fav_folder failed: %s", e)
            return {"code": -1, "message": str(e)}

    async def create_fav_folder(self, name: str, intro: str = "", privacy: int = 0) -> dict:
        """Create a new favorites folder. Requires auth.

        Args:
            name: Folder title.
            intro: Optional description.
            privacy: 0=public, 1=private.

        Returns:
            Dict with code, message, data (may contain media_id).
        """
        if not self._has_auth:
            return {"code": -1, "message": "未登录，无法创建收藏夹"}

        try:
            raw = await self._client.post(
                f"{BASE_URL}/x/v3/fav/folder/add",
                data={"title": name, "intro": intro, "privacy": privacy},
            )
            return {
                "code": raw.get("code", -1),
                "message": raw.get("message", ""),
                "data": raw.get("data") or {},
            }
        except Exception as e:
            logger.warning("create_fav_folder failed: %s", e)
            return {"code": -1, "message": str(e)}

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_video_item(item: dict) -> VideoItem:
        """Parse a video item from popular or ranking API response."""
        stat = item.get("stat") or {}
        owner = item.get("owner") or {}
        return VideoItem(
            aid=item.get("aid", 0),
            bvid=item.get("bvid", ""),
            title=item.get("title", ""),
            tid=item.get("tid", 0),
            tname=item.get("tname", ""),
            desc=item.get("desc", ""),
            duration=item.get("duration", 0),
            owner_name=owner.get("name", ""),
            owner_mid=owner.get("mid", 0),
            view=stat.get("view", 0),
            like=stat.get("like", 0),
            pubdate=item.get("pubdate", 0),
            pic=item.get("pic", ""),
            rcmd_reason=item.get("rcmd_reason", {}).get("content") if isinstance(item.get("rcmd_reason"), dict) else item.get("rcmd_reason"),
            is_commercial=False,
        )

    @staticmethod
    def _parse_rcmd_item(item: dict) -> VideoItem:
        """Parse a video item from index/top/rcmd response.

        **Important**: This API uses ``id`` instead of ``aid``.
        Stat is truncated — only view/like/danmaku available.
        """
        stat = item.get("stat") or {}
        owner = item.get("owner") or {}
        rcmd_reason_obj = item.get("rcmdreason") or {}
        return VideoItem(
            aid=item.get("id", 0),  # field name is "id", not "aid"
            bvid=item.get("bvid", ""),
            title=item.get("title", ""),
            tid=(item.get("args") or {}).get("rid", 0) if item.get("args") else 0,
            tname=(item.get("args") or {}).get("rname", "") if item.get("args") else "",
            desc="",
            duration=item.get("duration", 0),
            owner_name=owner.get("name", ""),
            owner_mid=owner.get("mid", 0),
            view=stat.get("view", 0),
            like=stat.get("like", 0),
            pubdate=item.get("pubdate", 0),
            pic=item.get("pic", ""),
            rcmd_reason=rcmd_reason_obj.get("content") if isinstance(rcmd_reason_obj, dict) else None,
            is_commercial=False,
        )
