"""Video info API wrapper using Bilibili's /x/web-interface/view endpoint.

Caches results per BVID so the same video is never fetched twice.
"""

from __future__ import annotations

from .http_client import BiliHTTPClient

BASE_URL = "https://api.bilibili.com/x/web-interface/view"


class VideoInfoAPI:
    """Wrapper for the Bilibili video-info endpoint with BVID-based caching.

    Calls ``GET /x/web-interface/view?bvid=X`` to fetch full video metadata
    (including the partition ``tid``) and caches every response in memory so
    subsequent requests for the same BVID return instantly.

    Usage::

        async with BiliHTTPClient(sessdata="...", bili_jct="...") as http:
            api = VideoInfoAPI(http)
            info = await api.get_video_info("BV1xx411c7mD")
            print(info["tid"], info["tname"])
    """

    def __init__(self, http_client: BiliHTTPClient) -> None:
        self._http: BiliHTTPClient = http_client
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_video_info(self, bvid: str) -> dict:
        """Return the ``data`` dict from ``/x/web-interface/view?bvid={bvid}``.

        The first call for a given BVID performs an HTTP request; subsequent
        calls return the cached result immediately.
        """
        if bvid in self._cache:
            return self._cache[bvid]

        raw = await self._http.get(BASE_URL, params={"bvid": bvid})
        data: dict = raw.get("data", {})
        self._cache[bvid] = data
        return data

    def is_cached(self, bvid: str) -> bool:
        """Return ``True`` if the given BVID is already in the local cache."""
        return bvid in self._cache
