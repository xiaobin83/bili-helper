"""Base API client for B站 — reusable signed-GET and CSRF-POST helpers.

All B站 API clients in bili-core extend this class to avoid duplicating
the Wbi-signing and CSRF-injection boilerplate across every endpoint wrapper.

Usage::

    from bili_core.api_base import BaseBiliClient
    from bili_core.http_client import BiliHTTPClient
    from bili_core.signing import sign_params

    class MyClient(BaseBiliClient):
        async def do_thing(self) -> dict:
            return await self._signed_get("/x/some/endpoint", {"key": "val"})
"""

from __future__ import annotations

from typing import Callable

from bili_core.http_client import BiliHTTPClient

BASE_URL = "https://api.bilibili.com"


class BaseBiliClient:
    """Shared base for B站 API clients.

    Provides ``_get``, ``_signed_get``, and ``_post`` helpers that
    wire together ``BiliHTTPClient`` (transport), Wbi signing, and
    CSRF injection so subclasses only declare endpoint logic.

    Parameters
    ----------
    http_client:
        Pre-configured ``BiliHTTPClient`` instance (handles auth,
        rate limiting, retries).  Constructed outside so consumers
        control session lifecycle.
    signing:
        Callable ``(params: dict) -> dict`` that appends ``w_rid``
        and ``wts``.  Typically ``sign_params`` from ``bili_core.signing``.
    """

    BASE_URL: str = BASE_URL

    def __init__(
        self,
        http_client: BiliHTTPClient,
        signing: Callable[..., dict],
    ) -> None:
        self._http = http_client
        self._sign = signing

    # ------------------------------------------------------------------
    # Auth convenience
    # ------------------------------------------------------------------

    @property
    def _has_auth(self) -> bool:
        """Return ``True`` when credentials are present (non-empty CSRF token)."""
        return bool(self._http.bili_jct)

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Issue a plain (unsigned) GET request.  Returns parsed JSON body."""
        return await self._http.get(f"{self.BASE_URL}{path}", params=params)

    async def _signed_get(self, path: str, params: dict | None = None) -> dict:
        """Issue a Wbi-signed GET request.  Returns parsed JSON body.

        The *signing* callable passed to ``__init__`` is applied to
        *params* before the request is sent.
        """
        raw_params: dict = dict(params or {})
        signed = self._sign(raw_params)
        return await self._http.get(f"{self.BASE_URL}{path}", params=signed)

    async def _post(self, path: str, data: dict | None = None) -> dict:
        """Issue a CSRF-authenticated POST request.  Returns parsed JSON body.

        ``csrf`` is auto-injected by ``BiliHTTPClient.post()`` — callers
        do **not** need to include it in *data*.
        """
        return await self._http.post(f"{self.BASE_URL}{path}", data=data)
