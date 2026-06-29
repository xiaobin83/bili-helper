"""Tests for at_orchestrator.replier — comment reply module.

TDD: these tests are written BEFORE the implementation.
Run once to see them fail (ImportError), then implement replier.py to make them pass.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bili_core.errors import AuthError, CSRFError


class TestTruncateForComment:
    """_truncate_for_comment() — truncates text to max_chars, CJK-safe."""

    def test_short_text_unchanged(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        assert _truncate_for_comment("hello") == "hello"

    def test_exact_max_unchanged(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        text = "a" * 1000
        result = _truncate_for_comment(text)
        assert result == text
        assert len(result) == 1000

    def test_long_text_truncated_with_ellipsis(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        text = "a" * 1500
        result = _truncate_for_comment(text)
        assert len(result) == 1001  # 1000 chars + "…"
        assert result.endswith("…")
        assert result[:1000] == "a" * 1000

    def test_default_max_chars_is_1000(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        text = "a" * 1001
        result = _truncate_for_comment(text)
        assert len(result) == 1001  # 1000 + "…"

    def test_custom_max_chars(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        result = _truncate_for_comment("hello world", max_chars=5)
        assert result == "hello…"
        assert len(result) == 6

    def test_cjk_text_not_broken(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        text = "你好世界" * 300  # 1200 chars
        result = _truncate_for_comment(text)
        assert len(result) == 1001
        assert result.endswith("…")
        # Verify no partial CJK character at the boundary
        truncated = result[:-1]  # remove the "…"
        assert all(ord(c) > 127 for c in truncated[-4:])  # last 4 chars are full CJK

    def test_empty_string(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        assert _truncate_for_comment("") == ""

    def test_mixed_ascii_cjk_under_limit(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        text = "Hello世界" * 90  # 810 chars, under 1000
        assert _truncate_for_comment(text) == text

    def test_unicode_emoji_preserved(self) -> None:
        from at_orchestrator.replier import _truncate_for_comment

        text = "🎉" * 800  # 800 emoji chars, under 1000
        assert _truncate_for_comment(text) == text


class TestReplyComment:
    """reply_comment() — sends comment reply to B站 API with proper params."""

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _make_client() -> MagicMock:
        """Return a mock BiliHTTPClient with AsyncMock post method."""
        client = MagicMock()
        client.post = AsyncMock()
        return client

    @staticmethod
    def _make_task(
        business_id: int = 1,
        subject_id: int = 12345,
        root_id: int | None = None,
        source_id: int | None = None,
    ) -> dict:
        return {
            "business_id": business_id,
            "subject_id": subject_id,
            "root_id": root_id,
            "source_id": source_id,
        }

    # -- success ----------------------------------------------------------------

    async def test_success_video_comment_type_1(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0, "message": "success"}
        task = self._make_task(business_id=1, subject_id=12345)

        result = await reply_comment(client, task, "感谢反馈！")

        assert result is True
        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == "https://api.bilibili.com/x/v2/reply/add"
        assert call_args[1]["data"]["type"] == 1
        assert call_args[1]["data"]["oid"] == 12345
        assert call_args[1]["data"]["message"] == "感谢反馈！"

    async def test_success_dynamic_comment_business_11_type_17(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task(business_id=11)

        result = await reply_comment(client, task, "动态回复")

        assert result is True
        assert client.post.call_args[1]["data"]["type"] == 17

    async def test_success_dynamic_comment_business_17_type_17(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task(business_id=17, subject_id=999)

        result = await reply_comment(client, task, "另一个动态回复")

        assert result is True
        data = client.post.call_args[1]["data"]
        assert data["type"] == 17
        assert data["oid"] == 999

    # -- CSRF error -------------------------------------------------------------

    async def test_csrf_error_raises(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.side_effect = CSRFError()
        task = self._make_task()

        with pytest.raises(CSRFError):
            await reply_comment(client, task, "test")

    # -- auth error returns False -----------------------------------------------

    async def test_auth_error_returns_false(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.side_effect = AuthError()
        task = self._make_task()

        result = await reply_comment(client, task, "test")
        assert result is False

    # -- recoverable error codes ------------------------------------------------

    async def test_code_12015_returns_false(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 12015, "message": "level 2 limit"}
        task = self._make_task()

        result = await reply_comment(client, task, "test")
        assert result is False

    async def test_code_12035_returns_false(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 12035, "message": "content violation"}
        task = self._make_task()

        result = await reply_comment(client, task, "test")
        assert result is False

    async def test_unknown_error_code_returns_false(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 99999, "message": "unknown error"}
        task = self._make_task()

        result = await reply_comment(client, task, "test")
        assert result is False

    # -- content truncation -----------------------------------------------------

    async def test_truncates_long_content_before_sending(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task()
        long_content = "a" * 2000

        result = await reply_comment(client, task, long_content)

        assert result is True
        sent_message = client.post.call_args[1]["data"]["message"]
        assert len(sent_message) == 1001
        assert sent_message.endswith("…")

    # -- root / parent params ---------------------------------------------------

    async def test_root_id_included_when_not_zero(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task(root_id=50)

        result = await reply_comment(client, task, "reply")

        assert result is True
        data = client.post.call_args[1]["data"]
        assert data["root"] == 50

    async def test_parent_uses_source_id_when_available(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task(root_id=50, source_id=60)

        result = await reply_comment(client, task, "reply")

        assert result is True
        data = client.post.call_args[1]["data"]
        assert data["root"] == 50
        assert data["parent"] == 60  # source_id takes precedence

    async def test_parent_falls_back_to_root_when_source_id_absent(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task(root_id=50, source_id=None)

        result = await reply_comment(client, task, "reply")

        assert result is True
        data = client.post.call_args[1]["data"]
        assert data["root"] == 50
        assert data["parent"] == 50  # falls back to root_id

    async def test_root_and_parent_omitted_when_zero_or_none(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task()  # root_id=None, source_id=None

        result = await reply_comment(client, task, "reply")

        assert result is True
        data = client.post.call_args[1]["data"]
        assert "root" not in data
        assert "parent" not in data

    async def test_root_id_zero_omitted(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task(root_id=0, source_id=0)

        result = await reply_comment(client, task, "reply")

        assert result is True
        data = client.post.call_args[1]["data"]
        assert "root" not in data
        assert "parent" not in data

    # -- invalid business_id ----------------------------------------------------

    async def test_invalid_business_id_raises_value_error(self) -> None:
        from at_orchestrator.replier import reply_comment

        client = self._make_client()
        client.post.return_value = {"code": 0}
        task = self._make_task(business_id=99)

        with pytest.raises(ValueError, match="99"):
            await reply_comment(client, task, "test")
