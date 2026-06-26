"""B站 API error types — auth, CSRF, rate-limit, generic API, and publish errors."""

from __future__ import annotations

# ── Error code constants (publish) ──────────────────────────

PUBLISH_ERR_NO_IMAGE = -1          # 无图片
PUBLISH_ERR_PARAM = -2             # 参数错误
PUBLISH_ERR_IMAGE_TOO_SMALL = -3   # 图片太小 (<420px)
PUBLISH_ERR_NOT_LOGIN = -4         # 未登录

# ── Exception classes ───────────────────────────────────────


class BiliAPIError(Exception):
    """Generic B站 API error.

    Attributes:
        code: B站 API response code (e.g. -101, -111).
        message: Human-readable error message.
    """

    def __init__(self, code: int, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}" if message else f"B站 API 错误 (code={code})")


class AuthError(Exception):
    """Raised when SESSDATA is expired or invalid.

    The user must re-authenticate to obtain a fresh SESSDATA.
    """

    def __init__(self, message: str = "登录已过期，请重新登录") -> None:
        self.message = message
        super().__init__(message)


class CSRFError(Exception):
    """Raised when CSRF validation fails.

    The BILI_JCT token is invalid or missing from the request.
    """

    def __init__(self, message: str = "CSRF 校验失败，请更新 bili_jct") -> None:
        self.message = message
        super().__init__(message)


class RateLimitError(Exception):
    """Raised when rate-limiting retries are exhausted (HTTP 412/429).

    Indicates the client has been rate-limited and all retry attempts failed.
    """

    def __init__(self, status_code: int, message: str = "请求过于频繁（已达最大重试次数）") -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class PublishError(BiliAPIError):
    """Raised when publishing a dynamic fails.

    Attributes:
        code: Publish-specific error code (see PUBLISH_ERR_* constants).
        message: Human-readable error description.
    """

    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(code, message)
