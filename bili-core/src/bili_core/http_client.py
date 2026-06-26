"""Async HTTP client with curl_cffi Chrome impersonation, rate limiting, and retry."""

from __future__ import annotations

import asyncio
import random
import time

from curl_cffi import requests as curl_requests

from bili_core.errors import AuthError, CSRFError, RateLimitError

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "sec-ch-ua": (
        '"Google Chrome";v="131", "Not=A?Brand";v="8", "Chromium";v="131"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

IMPERSONATE = "chrome131"
MIN_INTERVAL = 2.0
MAX_RETRIES = 3
RETRY_WAIT = 120.0
RETRY_STATUSES = frozenset({403, 412, 429})


class BiliHTTPClient:
    """Async HTTP client for B站 API with curl_cffi Chrome impersonation.

    Uses curl_cffi with ``chrome131`` TLS fingerprint impersonation to bypass
    B站's anti-bot WAF, plus jitter-based rate limiting and automatic retry.
    """

    def __init__(
        self,
        sessdata: str,
        bili_jct: str,
        buvid3: str = "",
        min_interval: float = 2.0,
    ) -> None:
        self._sessdata = sessdata
        self._bili_jct = bili_jct
        self._min_interval = min_interval
        self._last_request_time: float = 0.0

        cookie_parts = [f"SESSDATA={sessdata}", f"bili_jct={bili_jct}"]
        if buvid3:
            cookie_parts.append(f"buvid3={buvid3}")
        cookie_str = "; ".join(cookie_parts)

        headers = {**DEFAULT_HEADERS, "Cookie": cookie_str}

        self._session = curl_requests.AsyncSession(
            headers=headers,
            timeout=60,
            impersonate=IMPERSONATE,
        )

    async def close(self) -> None:
        await self._session.close()

    async def __aenter__(self) -> BiliHTTPClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get(self, url: str, params: dict | None = None) -> dict:
        return await self._request("GET", url, params=params)

    async def post(self, url: str, data: dict | None = None) -> dict:
        payload: dict = {"csrf": self._bili_jct}
        if data:
            payload.update(data)
        return await self._request("POST", url, data=payload)

    async def post_json(
        self, url: str, data: dict | None = None, csrf_in_url: bool = True
    ) -> dict:
        """POST with JSON body. CSRF optionally injected as URL query param.

        B站's dynamic_svr/create endpoint requires CSRF in both the URL
        query string and the form body. Set *csrf_in_url* to ``True``
        (default) for endpoints that expect CSRF in the URL.
        """
        payload: dict = {}
        if data:
            payload.update(data)

        if csrf_in_url:
            csrf_param = f"csrf={self._bili_jct}"
            sep = "&" if "?" in url else "?"
            full_url = f"{url}{sep}{csrf_param}"
            return await self._request("POST", full_url, json=payload)
        else:
            payload["csrf"] = self._bili_jct
            return await self._request("POST", url, json=payload)

    async def upload_file(
        self, url: str, file_path: str, form_data: dict | None = None
    ) -> dict:
        """Upload a file via multipart/form-data POST (e.g. B站 image upload).

        The file is sent as the ``file_up`` field. Extra form fields can be
        passed via *form_data*; ``csrf`` is injected automatically if missing.
        """
        files = {"file_up": open(file_path, "rb")}
        fd: dict = {}
        if form_data:
            fd.update(form_data)
        if "csrf" not in fd:
            fd["csrf"] = self._bili_jct
        return await self._request("POST", url, data=fd, files=files)

    async def _ensure_interval(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            jitter = random.uniform(0, 1.0)
            await asyncio.sleep(self._min_interval - elapsed + jitter)
        self._last_request_time = time.monotonic()

    async def _request(self, method: str, url: str, **kwargs: object) -> dict:
        for attempt in range(MAX_RETRIES + 1):
            await self._ensure_interval()

            try:
                if method == "GET":
                    resp = await self._session.get(url, **(kwargs))  # type: ignore[arg-type]
                else:
                    resp = await self._session.post(url, **(kwargs))  # type: ignore[arg-type]
            except Exception:
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

            data: dict = resp.json()
            code = data.get("code", 0)

            if code == -101:
                raise AuthError()
            if code == -111:
                raise CSRFError()

            return data

        raise RuntimeError("_request: unreachable")
