"""Video info API wrapper with disk-backed cache (30-day TTL).

Caches results to ``~/.bili-helper/fav-organizer/video_cache.json`` so repeated
classify runs don't refetch video metadata.  Cache can be cleared
via ``StateManager.clear_video_cache()``.
"""

from __future__ import annotations

from bili_core.http_client import BiliHTTPClient
from .state_manager import StateManager

BASE_URL = "https://api.bilibili.com/x/web-interface/view"


class VideoInfoAPI:
    """Wrapper for the Bilibili video-info endpoint with two-level caching.

    Level 1: in-memory dict (instant, per-session).
    Level 2: disk cache via ``StateManager`` (30-day TTL, survives restarts).

    Usage::

        async with BiliHTTPClient(sessdata="...", bili_jct="...") as http:
            api = VideoInfoAPI(http)
            info = await api.get_video_info("BV1xx411c7mD")
            print(info.get("tid"), info.get("tname"))
    """

    def __init__(
        self,
        http_client: BiliHTTPClient,
        state_manager: StateManager | None = None,
    ) -> None:
        self._http: BiliHTTPClient = http_client
        self._state = state_manager or StateManager()
        # In-memory cache (fast, per-session)
        self._mem_cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_video_info(self, bvid: str) -> dict:
        """Return the ``data`` dict from ``/x/web-interface/view?bvid={bvid}``.

        Lookup order: memory cache → disk cache (30-day TTL) → API call.
        """
        # Level 1: memory
        if bvid in self._mem_cache:
            return self._mem_cache[bvid]

        # Level 2: disk (30-day TTL)
        cached = self._state.get_cached_video(bvid)
        if cached is not None:
            self._mem_cache[bvid] = cached
            return cached

        # Level 3: API call
        raw = await self._http.get(BASE_URL, params={"bvid": bvid})
        data: dict = raw.get("data", {})

        # Persist to both caches
        self._mem_cache[bvid] = data
        self._state.set_cached_video(bvid, data)

        return data

    # ------------------------------------------------------------------
    # Cache utilities
    # ------------------------------------------------------------------

    def is_cached(self, bvid: str) -> bool:
        """Return ``True`` if the given BVID is in the memory cache."""
        return bvid in self._mem_cache

    def preload_from_disk(self, bvids: list[str]) -> int:
        """Preload disk cache entries into memory. Returns count loaded."""
        count = 0
        for bvid in bvids:
            if bvid not in self._mem_cache:
                cached = self._state.get_cached_video(bvid)
                if cached is not None:
                    self._mem_cache[bvid] = cached
                    count += 1
        return count

    def clear_disk_cache(self) -> None:
        """Delete the disk cache file."""
        self._state.clear_video_cache()
