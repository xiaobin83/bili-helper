"""Tests for BiliHTTPClient — get/post, CSRF injection, JSON posting, error
handling, buvid3 cookie, and rate-limiting configurability."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bili_core.errors import AuthError, CSRFError
from bili_core.http_client import BiliHTTPClient


def _make_mock_response(code: int = 0, status: int = 200) -> MagicMock:
    """Return a MagicMock response with ``.json()`` and ``.status_code``."""
    resp = MagicMock()
    resp.json.return_value = {"code": code}
    resp.status_code = status
    return resp


class TestBiliHTTPClient:
    """Unit tests for BiliHTTPClient — all curl_cffi calls are mocked."""

    # ── test 1: get() delegates to session.get ────────────────────────────

    @patch("bili_core.http_client.curl_requests.AsyncSession")
    async def test_get_request(self, mock_async_session: MagicMock) -> None:
        """Verify get() calls session.get once with the correct URL."""
        mock_instance = mock_async_session.return_value
        mock_instance.get = AsyncMock(return_value=_make_mock_response())

        client = BiliHTTPClient("sess", "jct", min_interval=0)
        result = await client.get("https://api.bilibili.com/test")

        mock_instance.get.assert_called_once_with(
            "https://api.bilibili.com/test", params=None
        )
        assert result == {"code": 0}

    # ── test 2: post() injects csrf into form data ────────────────────────

    @patch("bili_core.http_client.curl_requests.AsyncSession")
    async def test_post_adds_csrf(self, mock_async_session: MagicMock) -> None:
        """post() must inject the ``csrf`` field into the POST body."""
        mock_instance = mock_async_session.return_value
        mock_instance.post = AsyncMock(return_value=_make_mock_response())

        client = BiliHTTPClient("sess", "jct", min_interval=0)
        await client.post("https://api.bilibili.com/test", data={"key": "val"})

        call_kwargs = mock_instance.post.call_args.kwargs
        assert call_kwargs["data"] is not None
        assert call_kwargs["data"]["csrf"] == "jct"
        assert call_kwargs["data"]["key"] == "val"

    # ── test 3: post_json() with csrf_in_url=True ─────────────────────────

    @patch("bili_core.http_client.curl_requests.AsyncSession")
    async def test_post_json_with_url_csrf(self, mock_async_session: MagicMock) -> None:
        """post_json(csrf_in_url=True) must inject csrf as URL query param
        and send a JSON body (``json=``, not ``data=``)."""
        mock_instance = mock_async_session.return_value
        mock_instance.post = AsyncMock(return_value=_make_mock_response())

        client = BiliHTTPClient("sess", "jct", min_interval=0)
        await client.post_json(
            "https://api.bilibili.com/test", data={"key": "val"}, csrf_in_url=True
        )

        # The URL passed to session.post must contain the csrf param
        called_url: str = mock_instance.post.call_args.args[0]
        assert "csrf=jct" in called_url
        assert "?" in called_url

        # The body must use json=, not data=
        call_kwargs = mock_instance.post.call_args.kwargs
        assert "json" in call_kwargs
        assert "data" not in call_kwargs
        assert call_kwargs["json"] == {"key": "val"}

    # ── test 4: code -101 raises AuthError ────────────────────────────────

    @patch("bili_core.http_client.curl_requests.AsyncSession")
    async def test_auth_error_raised(self, mock_async_session: MagicMock) -> None:
        """Response code -101 must raise AuthError."""
        mock_instance = mock_async_session.return_value
        mock_instance.get = AsyncMock(return_value=_make_mock_response(code=-101))

        client = BiliHTTPClient("sess", "jct", min_interval=0)

        with pytest.raises(AuthError):
            await client.get("https://api.bilibili.com/test")

    # ── test 5: code -111 raises CSRFError ────────────────────────────────

    @patch("bili_core.http_client.curl_requests.AsyncSession")
    async def test_csrf_error_raised(self, mock_async_session: MagicMock) -> None:
        """Response code -111 must raise CSRFError."""
        mock_instance = mock_async_session.return_value
        mock_instance.get = AsyncMock(return_value=_make_mock_response(code=-111))

        client = BiliHTTPClient("sess", "jct", min_interval=0)

        with pytest.raises(CSRFError):
            await client.get("https://api.bilibili.com/test")

    # ── test 6: buvid3 appears in Cookie header ───────────────────────────

    @patch("bili_core.http_client.curl_requests.AsyncSession")
    async def test_buvid3_in_cookie(self, mock_async_session: MagicMock) -> None:
        """When buvid3 is provided it must appear in the Cookie header;
        when omitted it must not."""
        # -- with buvid3
        BiliHTTPClient("sess", "jct", buvid3="BV123456")
        call_headers_with = mock_async_session.call_args.kwargs["headers"]
        assert "buvid3=BV123456" in call_headers_with["Cookie"]

        mock_async_session.reset_mock()

        # -- without buvid3
        BiliHTTPClient("sess", "jct")
        call_headers_without = mock_async_session.call_args.kwargs["headers"]
        assert "buvid3" not in call_headers_without["Cookie"]
