"""Tests for at_orchestrator.replier — private message module.

TDD: tests written BEFORE implementation. Run once to see them fail, then implement.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bili_core.errors import AuthError, CSRFError


# ── Test helpers ────────────────────────────────────────────────────────────


def _make_client(bili_jct: str = "mock_jct_123") -> MagicMock:
    """Return a mock BiliHTTPClient with AsyncMock get/post methods."""
    client = MagicMock()
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.bili_jct = bili_jct
    return client


FIXED_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FIXED_TS = 1700000000
SIGNED_PARAMS = {
    "w_sender_uid": 111,
    "w_receiver_id": 222,
    "w_dev_id": FIXED_UUID,
    "w_rid": "abc123def456",
    "wts": FIXED_TS,
}

# Expected URL segment after urlencode of SIGNED_PARAMS (dict insertion order)
_EXPECTED_QUERY = (
    "w_sender_uid=111"
    "&w_receiver_id=222"
    "&w_dev_id=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    "&w_rid=abc123def456"
    "&wts=1700000000"
)
_EXPECTED_PM_URL = (
    "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg?"
    + _EXPECTED_QUERY
)


# ── reply_pm ────────────────────────────────────────────────────────────────


class TestReplyPm:
    """reply_pm() — sends private message via B站 web_im API."""

    # ── success ─────────────────────────────────────────────────────────────

    async def test_success_returns_true(self) -> None:
        """Code 0 → True, verify URL, body, and signed params."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {"code": 0, "message": "success"}

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            result = await reply_pm(client, 111, 222, "Hello PM!")
            assert result is True

        # Verify URL
        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == _EXPECTED_PM_URL

        # Verify body fields
        data = call_args[1]["data"]
        assert data["msg[sender_uid]"] == 111
        assert data["msg[receiver_id]"] == 222
        assert data["msg[receiver_type]"] == 1
        assert data["msg[msg_type]"] == 1
        assert data["msg[dev_id]"] == FIXED_UUID
        assert data["msg[timestamp]"] == FIXED_TS
        assert data["msg[new_face_version]"] == 1
        assert data["csrf"] == "mock_jct_123"
        assert data["csrf_token"] == "mock_jct_123"
        # Verify content is JSON string
        assert '"content"' in data["msg[content]"]

    # ── error codes ─────────────────────────────────────────────────────────

    async def test_code_21047_one_msg_limit_returns_false(self) -> None:
        """21047 (1-msg limit for users without session) → False."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {
            "code": 21047,
            "message": "对方尚未关注你，24小时内仅能发送1条消息",
        }

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            result = await reply_pm(client, 111, 222, "hello")
            assert result is False

    async def test_code_21015_no_phone_returns_false(self) -> None:
        """21015 (user has no phone bound) → False."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {
            "code": 21015,
            "message": "对方尚未绑定手机",
        }

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            result = await reply_pm(client, 111, 222, "hello")
            assert result is False

    async def test_unknown_error_code_returns_false(self) -> None:
        """Arbitrary non-zero code → False."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {"code": 99999, "message": "unknown"}

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            result = await reply_pm(client, 111, 222, "hello")
            assert result is False

    # ── auth / csrf ─────────────────────────────────────────────────────────

    async def test_auth_error_returns_false(self) -> None:
        """AuthError from client → False."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.side_effect = AuthError()

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            result = await reply_pm(client, 111, 222, "hello")
            assert result is False

    async def test_csrf_error_raises(self) -> None:
        """CSRFError → re-raised (fatal)."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.side_effect = CSRFError()

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            with pytest.raises(CSRFError):
                await reply_pm(client, 111, 222, "hello")

    # ── content truncation ──────────────────────────────────────────────────

    async def test_truncates_long_content_to_600_chars(self) -> None:
        """Content > 600 chars truncated with ellipsis."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {"code": 0}

        long_text = "哈" * 800  # 800 CJK chars

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            result = await reply_pm(client, 111, 222, long_text)
            assert result is True

        data = client.post.call_args[1]["data"]
        content_json = data["msg[content]"]
        # The content inside the JSON should be truncated
        import json

        content_inner = json.loads(content_json)["content"]
        assert len(content_inner) == 601  # 600 chars + "…"
        assert content_inner.endswith("…")

    async def test_short_content_not_truncated(self) -> None:
        """Content ≤ 600 chars left unchanged."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {"code": 0}

        short_text = "你好世界"  # 4 chars

        with (
            patch("bili_core.signing.sign_params", return_value=SIGNED_PARAMS),
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=FIXED_TS),
        ):
            result = await reply_pm(client, 111, 222, short_text)
            assert result is True

        data = client.post.call_args[1]["data"]
        import json

        content_inner = json.loads(data["msg[content]"])["content"]
        assert content_inner == short_text

    # ── sign_params integration ─────────────────────────────────────────────

    async def test_sign_params_called_with_correct_args(self) -> None:
        """sign_params receives correct sender_uid, receiver_id, dev_id."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {"code": 0}

        with (
            patch("bili_core.signing.sign_params") as mock_sign,
            patch("uuid.uuid4", return_value=FIXED_UUID),
            patch("time.time", return_value=0),  # time.time called differently
        ):
            mock_sign.return_value = SIGNED_PARAMS
            await reply_pm(client, 999, 888, "test")

            mock_sign.assert_called_once_with({
                "w_sender_uid": 999,
                "w_receiver_id": 888,
                "w_dev_id": FIXED_UUID,
            })

    async def test_different_users_produce_different_dev_ids(self) -> None:
        """Each call generates a fresh UUID v4 dev_id."""
        from at_orchestrator.replier import reply_pm

        client = _make_client()
        client.post.return_value = {"code": 0}

        captured_params: list[dict] = []

        def _capture(params: dict) -> dict:
            captured_params.append(dict(params))
            return SIGNED_PARAMS

        with (
            patch("bili_core.signing.sign_params", side_effect=_capture),
            patch("uuid.uuid4", side_effect=["uuid-1", "uuid-2"]),
            patch("time.time", return_value=FIXED_TS),
        ):
            await reply_pm(client, 111, 222, "msg1")
            await reply_pm(client, 333, 444, "msg2")

        assert captured_params[0]["w_dev_id"] == "uuid-1"
        assert captured_params[1]["w_dev_id"] == "uuid-2"


# ── check_session_detail ────────────────────────────────────────────────────


class TestCheckSessionDetail:
    """check_session_detail() — checks if a PM conversation exists."""

    async def test_session_exists_returns_true(self) -> None:
        """data has session fields → True."""
        from at_orchestrator.replier import check_session_detail

        client = _make_client()
        client.get.return_value = {
            "code": 0,
            "data": {
                "session_id": 123,
                "unread_count": 0,
                "last_msg": {},
            },
        }

        result = await check_session_detail(client, 111, 222)
        assert result is True

        client.get.assert_called_once_with(
            "https://api.vc.bilibili.com/web_im/v1/web_im/session_detail",
            params={"uid1": 111, "uid2": 222, "build": 0, "mobi_app": "web"},
        )

    async def test_no_session_returns_false(self) -> None:
        """data is empty dict → False."""
        from at_orchestrator.replier import check_session_detail

        client = _make_client()
        client.get.return_value = {"code": 0, "data": {}}

        result = await check_session_detail(client, 111, 222)
        assert result is False

    async def test_missing_data_key_returns_false(self) -> None:
        """No 'data' key in response → False."""
        from at_orchestrator.replier import check_session_detail

        client = _make_client()
        client.get.return_value = {"code": 0}

        result = await check_session_detail(client, 111, 222)
        assert result is False

    async def test_auth_error_returns_false(self) -> None:
        """AuthError from client → False."""
        from at_orchestrator.replier import check_session_detail

        client = _make_client()
        client.get.side_effect = AuthError()

        result = await check_session_detail(client, 111, 222)
        assert result is False

    async def test_csrf_error_raises(self) -> None:
        """CSRFError → re-raised."""
        from at_orchestrator.replier import check_session_detail

        client = _make_client()
        client.get.side_effect = CSRFError()

        with pytest.raises(CSRFError):
            await check_session_detail(client, 111, 222)
