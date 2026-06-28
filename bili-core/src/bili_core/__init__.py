# bili-core - Shared B站 utility library

from bili_core.api_base import BaseBiliClient
from bili_core.auth import Credentials, DEFAULT_AUTH_FILE, get_credentials, check_expired, login_flow
from bili_core.errors import AuthError, CSRFError, RateLimitError, BiliAPIError, PublishError
from bili_core.errors import (
    PUBLISH_ERR_NO_IMAGE,
    PUBLISH_ERR_PARAM,
    PUBLISH_ERR_IMAGE_TOO_SMALL,
    PUBLISH_ERR_NOT_LOGIN,
)
from bili_core.fav import FavClient
from bili_core.http_client import BiliHTTPClient
from bili_core.search import SearchClient
from bili_core.signing import sign_params, clear_cache

__all__ = [
    # Auth
    "Credentials",
    "DEFAULT_AUTH_FILE",
    "get_credentials",
    "check_expired",
    "login_flow",
    # HTTP
    "BiliHTTPClient",
    # API clients
    "BaseBiliClient",
    "FavClient",
    "SearchClient",
    # Signing
    "sign_params",
    "clear_cache",
    # Errors
    "AuthError",
    "CSRFError",
    "RateLimitError",
    "BiliAPIError",
    "PublishError",
    "PUBLISH_ERR_NO_IMAGE",
    "PUBLISH_ERR_PARAM",
    "PUBLISH_ERR_IMAGE_TOO_SMALL",
    "PUBLISH_ERR_NOT_LOGIN",
]
