"""Custom exception classes for B站 API interactions."""

from __future__ import annotations


class BiliAPIError(Exception):
    """Base exception for B站 API errors.

    Attributes:
        code: B站 API response code (e.g. -101, -111).
        message: Human-readable error message.
    """

    def __init__(self, code: int, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}" if message else f"B站 API 错误 (code={code})")


class AuthError(BiliAPIError):
    """Raised when SESSDATA is expired or invalid (code=-101).

    The user must re-authenticate to obtain a fresh SESSDATA.
    """

    def __init__(self, code: int = -101, message: str = "登录已过期，请重新获取 SESSDATA") -> None:
        super().__init__(code, message)


class CSRFError(BiliAPIError):
    """Raised when CSRF validation fails (code=-111).

    The BILI_JCT token is invalid or missing from the request.
    """

    def __init__(self, code: int = -111, message: str = "CSRF 校验失败") -> None:
        super().__init__(code, message)


class RateLimitError(BiliAPIError):
    """Raised when rate-limiting retries are exhausted (HTTP 412/429).

    Indicates the client has been rate-limited and all retry attempts failed.
    """

    def __init__(self, code: int, message: str = "请求过于频繁（已达最大重试次数）") -> None:
        super().__init__(code, message)
