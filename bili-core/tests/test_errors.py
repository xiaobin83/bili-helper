"""Tests for bili_core.errors exception classes."""

from bili_core.errors import (
    PUBLISH_ERR_NO_IMAGE,
    AuthError,
    BiliAPIError,
    CSRFError,
    PublishError,
    RateLimitError,
)


class TestAuthError:
    def test_auth_error_message(self) -> None:
        e = AuthError()
        assert str(e) == "登录已过期，请重新登录"


class TestCSRFError:
    def test_csrf_error_message(self) -> None:
        e = CSRFError()
        assert str(e) == "CSRF 校验失败，请更新 bili_jct"


class TestRateLimitError:
    def test_rate_limit_error(self) -> None:
        e = RateLimitError(429, "too many")
        assert e.status_code == 429


class TestPublishError:
    def test_publish_error_with_code(self) -> None:
        pe = PublishError(PUBLISH_ERR_NO_IMAGE, "no image")
        assert pe.code == -1
        assert isinstance(pe, BiliAPIError)
