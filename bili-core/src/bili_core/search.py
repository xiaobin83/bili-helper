"""Bilibili search API client.

All methods return raw API response dicts so downstream consumers can
construct their own models without bili-core depending on any specific
model library.

Usage::

    from bili_core.search import SearchClient
    from bili_core.http_client import BiliHTTPClient
    from bili_core.signing import sign_params

    http = BiliHTTPClient(sessdata="...", bili_jct="...")
    search = SearchClient(http_client=http, signing=sign_params)

    results = await search.search_videos("Python教程", page=1)
"""

from __future__ import annotations

import logging

from bili_core.api_base import BaseBiliClient

logger = logging.getLogger(__name__)


class SearchClient(BaseBiliClient):
    """Bilibili search client — video search via Wbi-signed API.

    Extends ``BaseBiliClient``.  All public methods return raw
    ``list[dict]`` from the API — no model classes.

    Parameters
    ----------
    http_client:
        Pre-configured ``BiliHTTPClient`` instance.
    signing:
        Wbi signing callable (``sign_params``).
    """

    async def search_videos(
        self,
        keyword: str,
        page: int = 1,
        order: str = "totalrank",
    ) -> list[dict]:
        """Search videos by keyword.

        Calls ``GET /x/web-interface/wbi/search/type`` (Wbi-signed).
        Returns up to 20 video results per page.  Only items with
        ``type == "video"`` are included.

        Returns ``[]`` on error or missing auth.

        Parameters
        ----------
        keyword:
            Search query string.
        page:
            Page number (default 1).
        order:
            Sort order — ``"totalrank"`` (comprehensive), ``"click"``
            (most played), ``"pubdate"`` (newest), ``"dm"`` (most
            danmaku), ``"stow"`` (most favorited).
        """
        if not self._has_auth:
            logger.info("search_videos: skipped (no auth)")
            return []
        try:
            raw = await self._signed_get("/x/web-interface/wbi/search/type", {
                "search_type": "video",
                "keyword": keyword,
                "order": order,
                "duration": 0,
                "tids": 0,
                "page": page,
            })
            if raw.get("code") != 0:
                logger.warning("search_videos: code=%s", raw.get("code"))
                return []
            data = raw.get("data") or {}
            result = data.get("result") or []
            # Only keep video-type results
            return [item for item in result if item.get("type") == "video"]
        except Exception as e:
            logger.warning("search_videos failed: %s", e)
            return []
