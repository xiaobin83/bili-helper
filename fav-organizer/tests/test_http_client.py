"""Tests for BiliHTTPClient — auth, rate limiting, retry, error handling."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from bili_core.errors import AuthError, CSRFError, RateLimitError
from bili_core.http_client import MAX_RETRIES, MIN_INTERVAL, RETRY_WAIT, BiliHTTPClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    """Create a mock httpx.Response with the given status and JSON body."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {"code": 0}
    return resp


def _new_client(sessdata: str = "test_sess", bili_jct: str = "test_jct") -> BiliHTTPClient:
    """Create a fresh BiliHTTPClient for testing."""
    return BiliHTTPClient(sessdata=sessdata, bili_jct=bili_jct)


# ---------------------------------------------------------------------------
# GET  —  success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_success() -> None:
    """Normal GET returns parsed JSON dict."""
    client = _new_client()
    client._session.get = AsyncMock(return_value=_make_response(json_data={"code": 0, "data": {"ok": True}}))

    result = await client.get("https://api.bilibili.com/x/test")
    assert result == {"code": 0, "data": {"ok": True}}
    client._session.get.assert_awaited_once_with("https://api.bilibili.com/x/test", params=None)


@pytest.mark.asyncio
async def test_get_with_params() -> None:
    """GET forwards query params."""
    client = _new_client()
    client._session.get = AsyncMock(return_value=_make_response())

    await client.get("https://api.bilibili.com/x/test", params={"pn": 1})
    client._session.get.assert_awaited_once_with(
        "https://api.bilibili.com/x/test", params={"pn": 1}
    )


# ---------------------------------------------------------------------------
# POST  —  success + CSRF injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_success_injects_csrf() -> None:
    """POST merges csrf into the data payload."""
    client = _new_client(bili_jct="jct_abc")
    client._session.post = AsyncMock(return_value=_make_response())

    await client.post("https://api.bilibili.com/x/test", data={"key": "val"})

    client._session.post.assert_awaited_once_with(
        "https://api.bilibili.com/x/test",
        data={"csrf": "jct_abc", "key": "val"},
    )


@pytest.mark.asyncio
async def test_post_none_data_injects_csrf() -> None:
    """POST with data=None still sends csrf."""
    client = _new_client(bili_jct="jct_xyz")
    client._session.post = AsyncMock(return_value=_make_response())

    await client.post("https://api.bilibili.com/x/test")

    client._session.post.assert_awaited_once_with(
        "https://api.bilibili.com/x/test",
        data={"csrf": "jct_xyz"},
    )


# ---------------------------------------------------------------------------
# Auth errors  (code=-101 / -111)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_error_code_neg_101() -> None:
    """code=-101 raises AuthError immediately (no retry)."""
    client = _new_client()
    client._session.get = AsyncMock(
        return_value=_make_response(json_data={"code": -101, "message": "账号未登录"})
    )

    with pytest.raises(AuthError, match="登录已过期"):
        await client.get("https://api.bilibili.com/x/test")

    # Auth errors must NOT be retried
    client._session.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_csrf_error_code_neg_111() -> None:
    """code=-111 raises CSRFError immediately (no retry)."""
    client = _new_client()
    client._session.post = AsyncMock(
        return_value=_make_response(json_data={"code": -111, "message": "csrf 校验失败"})
    )

    with pytest.raises(CSRFError, match="CSRF 校验失败"):
        await client.post("https://api.bilibili.com/x/test", data={"a": 1})

    client._session.post.assert_awaited_once()


# ---------------------------------------------------------------------------
# Rate-limit retry  (HTTP 412 / 429)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_exhausts_retries_412() -> None:
    """HTTP 412 triggers retry; after MAX_RETRIES retries RateLimitError is raised."""
    client = _new_client()

    # Every attempt returns 412
    client._session.get = AsyncMock(return_value=_make_response(status_code=412))

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(RateLimitError, match="412"):
            await client.get("https://api.bilibili.com/x/test")

    # Initial attempt + MAX_RETRIES retries = 4 total attempts
    assert client._session.get.await_count == MAX_RETRIES + 1

    # Sleep should be called for rate-limit wait + interval enforcement
    # At minimum, each retry triggers a 60s sleep
    retry_sleeps = [c for c in mock_sleep.await_args_list if c == call(RETRY_WAIT)]
    assert len(retry_sleeps) == MAX_RETRIES


@pytest.mark.asyncio
async def test_rate_limit_succeeds_after_one_retry() -> None:
    """412 on 1st attempt, success on retry — returns the final response."""
    client = _new_client()
    client._session.get = AsyncMock(
        side_effect=[
            _make_response(status_code=429),
            _make_response(status_code=429),
            _make_response(json_data={"code": 0, "data": "ok"}),
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await client.get("https://api.bilibili.com/x/test")

    assert result == {"code": 0, "data": "ok"}
    assert client._session.get.await_count == 3  # 2 failures + 1 success


# ---------------------------------------------------------------------------
# Network-error retry  (httpx.HTTPError)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_network_error_retries_then_fails() -> None:
    """httpx.HTTPError triggers retry; after exhaustion it re-raises."""
    client = _new_client()
    client._session.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.ConnectError):
            await client.get("https://api.bilibili.com/x/test")

    assert client._session.get.await_count == MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_network_error_succeeds_on_retry() -> None:
    """Network error on first attempt, success on second."""
    client = _new_client()
    client._session.get = AsyncMock(
        side_effect=[
            httpx.ReadTimeout("timeout"),
            _make_response(json_data={"code": 0}),
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await client.get("https://api.bilibili.com/x/test")

    assert result == {"code": 0}
    assert client._session.get.await_count == 2


# ---------------------------------------------------------------------------
# Non-retryable status codes pass through
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_retryable_status_code() -> None:
    """A non-412/429 status code is treated as success (body checked for code)."""
    client = _new_client()
    client._session.get = AsyncMock(
        return_value=_make_response(status_code=400, json_data={"code": -400, "message": "bad request"})
    )

    # Returns the body dict as-is (no error because code != -101/-111)
    result = await client.get("https://api.bilibili.com/x/test")
    assert result == {"code": -400, "message": "bad request"}
    client._session.get.assert_awaited_once()


# ---------------------------------------------------------------------------
# Request interval enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_interval_sleeps_when_too_fast() -> None:
    """When two requests are made in quick succession, asyncio.sleep bridges the gap."""
    client = _new_client()
    client._session.get = AsyncMock(return_value=_make_response())

    # Force _last_request_time to "now" so the next request must wait
    client._last_request_time = time.monotonic()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await client.get("https://api.bilibili.com/x/test")

    # asyncio.sleep should have been called with ≈MIN_INTERVAL + jitter (0-1s)
    sleep_calls = mock_sleep.await_args_list
    interval_sleeps = [c for c in sleep_calls if MIN_INTERVAL <= c.args[0] <= MIN_INTERVAL + 1.0]
    assert len(interval_sleeps) >= 1, f"Expected a sleep ≈{MIN_INTERVAL}s, got {sleep_calls}"


@pytest.mark.asyncio
async def test_request_interval_no_sleep_when_enough_time() -> None:
    """When enough time has passed, no sleep is inserted."""
    client = _new_client()
    client._session.get = AsyncMock(return_value=_make_response())

    # Simulate last request far in the past
    client._last_request_time = 0.0  # epoch — definitely enough time has passed

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await client.get("https://api.bilibili.com/x/test")

    # No sleep for interval enforcement (though rate-limit retry sleeps aren't relevant here)
    interval_sleeps = [c for c in mock_sleep.await_args_list if c.args and abs(c.args[0] - MIN_INTERVAL) < 0.1]
    assert len(interval_sleeps) == 0


# ---------------------------------------------------------------------------
# Async context manager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_context_manager_closes_client() -> None:
    """__aexit__ calls close() on the underlying httpx client."""
    client = _new_client()
    client._session.aclose = AsyncMock()

    async with client:
        pass

    client._session.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Cookie header construction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cookies_set_in_headers() -> None:
    """SESSDATA and bili_jct are included in the Cookie header."""
    client = BiliHTTPClient(sessdata="abc123", bili_jct="jct456")

    headers = client._session.headers
    assert "SESSDATA=abc123" in headers["Cookie"]
    assert "bili_jct=jct456" in headers["Cookie"]


@pytest.mark.asyncio
async def test_user_agent_set() -> None:
    """Browser User-Agent is automatically attached to every request."""
    client = _new_client()

    assert "Mozilla/5.0" in client._session.headers["User-Agent"]
    assert "AppleWebKit/537.36" in client._session.headers["User-Agent"]
