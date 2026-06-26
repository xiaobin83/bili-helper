"""API client wrapping bili-core's BiliHTTPClient for 6 B站 endpoints + orchestrator.

Usage:
    client = VideoAPIClient()
    result = await client.analyze_video("BV1xx411c7m9", set())
    print(result.video_detail.title)
"""

from __future__ import annotations

from typing import Any, Optional

from bili_core.auth import Credentials, DEFAULT_AUTH_FILE, get_credentials
from bili_core.errors import AuthError
from bili_core.http_client import BiliHTTPClient
from bili_core.signing import sign_params

from video_analyzer.models import (
    AISummary,
    Comment,
    PBP,
    PlayUrl,
    Screenshot,
    VideoAnalysisResult,
    VideoDetail,
)

BASE_URL = "https://api.bilibili.com"


class VideoAPIClient:
    """Aggregates 6 Bilibili API data sources into one VideoAnalysisResult.

    Constructor accepts an optional pre-configured ``BiliHTTPClient``.
    When omitted, loads credentials via ``get_credentials()`` (QR login
    if none found) and raises ``AuthError`` if no valid credentials available.
    """

    def __init__(self, http_client: Optional[BiliHTTPClient] = None) -> None:
        if http_client is not None:
            self._client = http_client
            return

        # Full credential flow: .auth.json → env vars → QR login (no public fallback)
        try:
            creds = get_credentials(auth_file=DEFAULT_AUTH_FILE)
        except (SystemExit, RuntimeError):
            creds = None

        if creds is None:
            raise AuthError("需要 B站 登录凭证，请扫码登录或设置 BILI_SESSDATA 环境变量")

        self._client = BiliHTTPClient(
            sessdata=creds.sessdata,
            bili_jct=creds.bili_jct,
            buvid3=creds.buvid3,
        )

    async def _get(self, url: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Issue a GET request via the authenticated client, return parsed JSON."""
        return await self._client.get(url, params=params)

    # ------------------------------------------------------------------
    # 6 fetch_* methods
    # ------------------------------------------------------------------

    async def fetch_video_detail(self, bvid: str) -> VideoDetail:
        """Fetch video metadata.  Raises ``ValueError`` if *bvid* is invalid."""
        raw = await self._get(
            f"{BASE_URL}/x/web-interface/view",
            params={"bvid": bvid},
        )
        code = raw.get("code")
        if code != 0:
            msg = raw.get("message", "unknown error")
            raise ValueError(f"Invalid bvid '{bvid}': [{code}] {msg}")
        return VideoDetail.model_validate(raw["data"])

    async def fetch_hot_comments(self, avid: int, bvid: str) -> list[Comment]:
        """Return up to 10 hot comments.

        Tries the Wbi-signed lazy-load endpoint first (``/reply/wbi/main``),
        then falls back to the classic ``/v2/reply`` with sort by likes.
        Returns ``[]`` on any error.
        """
        # Try Wbi-signed endpoint first (newer, returns hots + paginated replies)
        try:
            signed = sign_params({"type": 1, "oid": avid, "mode": 3, "ps": 10})
            raw = await self._get(
                f"{BASE_URL}/x/v2/reply/wbi/main",
                params=signed,
            )
            if raw.get("code") == 0:
                items: list[dict] = []
                data = raw.get("data") or {}
                hots = data.get("hots") or []
                items.extend(hots)
                replies = data.get("replies") or []
                items.extend(replies)
                return self._parse_comments(items[:10])
        except Exception:
            pass

        # Fallback: classic endpoint sorted by likes
        try:
            raw = await self._get(
                f"{BASE_URL}/x/v2/reply",
                params={"type": 1, "oid": avid, "sort": 1, "ps": 20, "pn": 1},
            )
            if raw.get("code") != 0:
                return []
            hots: list[dict] = raw.get("data", {}).get("hots") or []
            if hots:
                return self._parse_comments(hots[:10])
            replies: list[dict] = raw.get("data", {}).get("replies") or []
            return self._parse_comments(replies[:10])
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Comment parser helper
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_comments(items: list[dict]) -> list[Comment]:
        """Convert raw comment dicts to ``Comment`` models."""
        comments: list[Comment] = []
        for item in items:
            member = item.get("member", {}) or {}
            content = item.get("content", {}) or {}
            comments.append(
                Comment(
                    rpid=item.get("rpid", 0),
                    mid=str(member.get("mid", item.get("mid", ""))),
                    uname=member.get("uname", ""),
                    avatar=member.get("avatar", ""),
                    message=content.get("message", ""),
                    like=item.get("like", 0),
                    ctime=item.get("ctime", 0),
                    rcount=item.get("rcount", 0),
                )
            )
        return comments

    async def fetch_pbp(self, cid: int) -> Optional[PBP]:
        """Fetch high-energy progress bar (danmaku density) data.

        Uses ``bvc.bilivideo.com/pbp/data`` (the current endpoint).
        Response: ``{step_sec: <interval_in_seconds>, events: {default: [...]}}``
        """
        try:
            raw = await self._get(
                "https://bvc.bilivideo.com/pbp/data",
                params={"cid": cid},
            )
            # This endpoint returns the data at top-level (no "code" field)
            step_sec: list[float] = (
                raw.get("events", {}).get("default") or []
            )
            interval: int = raw.get("step_sec", 0) or 0
            return PBP(step_sec=step_sec, interval=interval)
        except Exception:
            return None

    async def fetch_ai_summary(self, aid: int, bvid: str, cid: int, up_mid: int) -> Optional[AISummary]:
        """Fetch AI-generated video summary via Wbi-signed endpoint.

        Uses ``/x/web-interface/view/conclusion/get`` (the current endpoint).
        Response: ``{code: 0, data: {model_result: {summary, outline, ...}}}``
        """
        try:
            params = {"aid": aid, "bvid": bvid, "cid": cid, "up_mid": up_mid}
            signed = sign_params(params)
            raw = await self._get(
                f"{BASE_URL}/x/web-interface/view/conclusion/get",
                params=signed,
            )
            if raw.get("code") != 0:
                return None
            data = raw.get("data") or {}
            if data.get("code", 0) != 0 and data.get("code") != 1:
                return None  # code=1 means "no audio recognized" — still ok
            model_result = data.get("model_result") or {}
            if not model_result:
                return None
            return AISummary.model_validate(model_result)
        except Exception:
            return None

    async def fetch_play_url(self, bvid: str, cid: int) -> Optional[PlayUrl]:
        """Fetch play URL at 1080P (qn=80)."""
        try:
            raw = await self._get(
                f"{BASE_URL}/x/player/playurl",
                params={"bvid": bvid, "cid": cid, "qn": 80},
            )
            if raw.get("code") != 0:
                return None
            data = raw.get("data", {}) or {}
            durls: list[dict] = data.get("durl") or []
            url = ""
            backup_urls: list[str] = []
            if durls:
                url = durls[0].get("url", "")
                backup_urls = durls[0].get("backup_url") or []
            quality = data.get("quality", 0)
            # Map quality code to human-readable description
            accept_quality: list[int] = data.get("accept_quality") or []
            accept_desc: list[str] = data.get("accept_description") or []
            quality_desc = ""
            if quality in accept_quality:
                idx = accept_quality.index(quality)
                if idx < len(accept_desc):
                    quality_desc = accept_desc[idx]
            return PlayUrl(
                url=url,
                backup_urls=backup_urls,
                quality=quality,
                quality_desc=quality_desc,
            )
        except Exception:
            return None

    async def fetch_screenshot(self, cid: int, bvid: str) -> Optional[Screenshot]:
        """Fetch screenshot/comic image URLs for a video.

        Requires ``bvid`` (or ``aid``) to identify the video —
        ``cid`` alone returns ``-400`` error.
        """
        try:
            raw = await self._get(
                f"{BASE_URL}/x/player/videoshot",
                params={"cid": cid, "bvid": bvid},
            )
            if raw.get("code") != 0:
                return None
            data = raw.get("data", {}) or {}
            images: list[str] = data.get("image") or []
            image_urls = [
                f"https:{url}" if url.startswith("//") else url for url in images
            ]
            return Screenshot(image_urls=image_urls)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------

    async def analyze_video(
        self,
        bvid: str,
        skip_flags: set[str],
    ) -> VideoAnalysisResult:
        """Fetch video_detail + up to 5 optional data sources.

        ``video_detail`` is **required** — its exception propagates on failure.
        The remaining 5 sources are skipped when their flag is present in
        *skip_flags*: ``"comments"``, ``"pbp"``, ``"summary"``, ``"playurl"``,
        ``"screenshot"``.

        Individual optional-source failures are silently swallowed (returning
        ``None`` or ``[]``) so a partial result is always returned for the
        successful fetches.
        """
        # 1. Required — propagate on failure
        video_detail = await self.fetch_video_detail(bvid)
        avid = video_detail.aid
        cid = video_detail.cid
        up_mid = video_detail.owner.get("mid", 0)

        result = VideoAnalysisResult(bvid=bvid, video_detail=video_detail)

        # 2. Optional calls — each guarded by skip_flags
        if "comments" not in skip_flags:
            result.hot_comments = await self.fetch_hot_comments(avid, bvid)

        if "pbp" not in skip_flags:
            result.pbp = await self.fetch_pbp(cid)

        if "summary" not in skip_flags:
            result.ai_summary = await self.fetch_ai_summary(avid, bvid, cid, up_mid)

        if "playurl" not in skip_flags:
            result.play_url = await self.fetch_play_url(bvid, cid)

        if "screenshot" not in skip_flags:
            result.screenshot = await self.fetch_screenshot(cid, bvid)

        return result
