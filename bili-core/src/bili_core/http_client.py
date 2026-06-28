"""Async HTTP client with httpx, rate limiting, and retry."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path

import httpx

from bili_core.errors import AuthError, CSRFError, RateLimitError

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MIN_INTERVAL = 2.0
MAX_RETRIES = 3
RETRY_WAIT = 30.0
RETRY_STATUSES = frozenset({403, 412, 429})

_MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class BiliHTTPClient:
    """Async HTTP client for B站 API with rate limiting and retry.

    Uses httpx.AsyncClient with proper headers for B站 API requests.
    Rate-limited with jitter to avoid triggering anti-bot measures.
    """

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        buvid3: str = "",
        min_interval: float = 2.0,
    ) -> None:
        self._bili_jct = bili_jct
        self._min_interval = min_interval
        self._last_request_time: float = 0.0

        cookie_parts = [f"SESSDATA={sessdata}", f"bili_jct={bili_jct}"]
        if buvid3:
            cookie_parts.append(f"buvid3={buvid3}")
        cookie_str = "; ".join(cookie_parts)

        headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}

        self._session = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )

    @property
    def bili_jct(self) -> str:
        """Public access to the CSRF token."""
        return self._bili_jct

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        await self._session.aclose()

    async def __aenter__(self) -> BiliHTTPClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get(self, url: str, params: dict | None = None) -> dict:
        """Send a GET request. Returns parsed JSON dict."""
        return await self._request("GET", url, params=params)

    async def post(self, url: str, data: dict | None = None) -> dict:
        """POST with form-encoded data. CSRF is auto-injected."""
        payload: dict = {"csrf": self._bili_jct}
        if data:
            payload.update(data)
        return await self._request("POST", url, data=payload)

    async def post_json(
        self, url: str, data: dict | None = None, csrf_in_url: bool = True
    ) -> dict:
        """POST with JSON body. CSRF optionally injected as URL query param.

        B站's create/dyn endpoint requires CSRF in the URL query string.
        Set *csrf_in_url* to ``True`` (default) for such endpoints.
        """
        payload: dict = {}
        if data:
            payload.update(data)

        if csrf_in_url:
            sep = "&" if "?" in url else "?"
            full_url = f"{url}{sep}csrf={self._bili_jct}"
            return await self._request("POST", full_url, json=payload)
        else:
            payload["csrf"] = self._bili_jct
            return await self._request("POST", url, json=payload)

    async def upload_file(
        self, url: str, file_path: str, form_data: dict | None = None
    ) -> dict:
        """Upload a file via multipart/form-data POST (e.g. B站 image upload).

        File content is read into memory to avoid handle leaks.
        Sent as ``file_up`` field; ``csrf`` is auto-injected if missing.
        """
        path = Path(file_path)
        suffix = path.suffix.lower()
        mime_type = _MIME_MAP.get(suffix, "application/octet-stream")

        content = path.read_bytes()

        files = {"file_up": (path.name, content, mime_type)}

        fd: dict = {}
        if form_data:
            fd.update(form_data)
        if "csrf" not in fd:
            fd["csrf"] = self._bili_jct

        return await self._request("POST", url, data=fd, files=files)

    async def _ensure_interval(self) -> None:
        """Wait until at least *min_interval* seconds since last request."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            jitter = random.uniform(0, 1.0)
            await asyncio.sleep(self._min_interval - elapsed + jitter)
        self._last_request_time = time.monotonic()

    async def _request(self, method: str, url: str, **kwargs: object) -> dict:
        """Core request with retry, rate-limiting, and error translation."""
        for attempt in range(MAX_RETRIES + 1):
            await self._ensure_interval()

            try:
                if method == "GET":
                    resp = await self._session.get(url, **kwargs)  # type: ignore[arg-type]
                else:
                    resp = await self._session.post(url, **kwargs)  # type: ignore[arg-type]
            except httpx.RequestError:
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_WAIT)
                    continue
                raise

            if resp.status_code in RETRY_STATUSES:
                if attempt < MAX_RETRIES:
                    print(
                        f"  ⚠️  HTTP {resp.status_code}，"
                        f"等待 {RETRY_WAIT}s 后重试 ({attempt + 1}/{MAX_RETRIES})..."
                    )
                    await asyncio.sleep(RETRY_WAIT)
                    continue
                raise RateLimitError(
                    resp.status_code,
                    f"请求过于频繁，已达到最大重试次数 (HTTP {resp.status_code})",
                )

            try:
                data: dict = resp.json()
            except Exception:
                # Non-JSON response — log diagnostic info and return error
                logger.warning(
                    "non-JSON response: HTTP %s %s | body=%s",
                    resp.status_code, resp.reason_phrase,
                    resp.text[:300],
                )
                return {"code": -1, "message": f"非JSON响应: HTTP {resp.status_code}"}

            code = data.get("code", 0)

            if code == -101:
                raise AuthError()
            if code == -111:
                raise CSRFError()

            return data

        raise RuntimeError("_request: unreachable")
