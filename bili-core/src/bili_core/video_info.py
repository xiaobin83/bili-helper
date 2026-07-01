"""Bilibili video info API client — fetch video metadata.

Provides ``VideoDetail`` (Pydantic v2 model) and ``VideoDetailClient``
so every skill can fetch video metadata without duplicating the endpoint
call or model definition.

Usage::

    from bili_core.http_client import BiliHTTPClient
    from bili_core.signing import sign_params
    from bili_core.video_info import VideoDetailClient

    http = BiliHTTPClient(sessdata="...", bili_jct="...", buvid3="...")
    client = VideoDetailClient(http_client=http, signing=sign_params)
    detail = await client.fetch_video_detail("BV1xx411c7mD")
    print(detail.title, detail.desc)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from bili_core.api_base import BaseBiliClient


# ── Model ──────────────────────────────────────────────────────────────


class VideoDetail(BaseModel):
    """Video metadata from the Bilibili video-info endpoint (``/x/web-interface/view``).

    Fields map to the top-level keys inside the API response ``data`` object.
    Extra fields from the API are silently ignored (``extra="ignore"``).
    """

    model_config = ConfigDict(extra="ignore")

    aid: int
    bvid: str
    cid: int
    title: str
    desc: str = ""
    duration: int  # seconds
    pubdate: int  # unix timestamp
    owner: dict[str, Any]  # {"mid": int, "name": str, "face": str}
    stat: dict[str, Any]  # {"view": int, "danmaku": int, ...}
    tname: str = ""  # zone / subzone name
    pic: str = ""  # cover URL
    dynamic: str = ""
    pub_location: str = ""  # upload location


# ── Client ─────────────────────────────────────────────────────────────


class VideoDetailClient(BaseBiliClient):
    """Lightweight client for fetching Bilibili video metadata.

    Provides a single public method ``fetch_video_detail`` that calls
    ``/x/web-interface/view`` and returns a typed ``VideoDetail`` model.

    Parameters
    ----------
    http_client:
        Pre-configured ``BiliHTTPClient`` instance.
    signing:
        Wbi signing callable (``sign_params``) — unused by this endpoint
        but required by ``BaseBiliClient`` for API consistency.
    """

    async def fetch_video_detail(self, bvid: str) -> VideoDetail:
        """Fetch video metadata.  Raises ``ValueError`` if *bvid* is invalid.

        Calls ``GET /x/web-interface/view?bvid={bvid}`` and validates the
        response ``data`` object into a ``VideoDetail`` model.
        """
        raw = await self._get(
            "/x/web-interface/view",
            params={"bvid": bvid},
        )
        code = raw.get("code")
        if code != 0:
            msg = raw.get("message", "unknown error")
            raise ValueError(f"Invalid bvid '{bvid}': [{code}] {msg}")
        return VideoDetail.model_validate(raw["data"])
