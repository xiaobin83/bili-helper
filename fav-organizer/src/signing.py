"""Wbi signing utility for Bilibili API authentication.

Implements the Wbi signature algorithm used by Bilibili's web API for
requests that require signed parameters. Keys are cached for 24 hours
to avoid redundant fetches from the nav endpoint.

Usage:
    from src.signing import sign_params

    params = {"foo": "bar", "keyword": "你好"}
    signed = sign_params(params)
    # signed = {"foo": "bar", "keyword": "你好", "w_rid": "...", "wts": 1702204169}
    # Use signed directly as URL params.
"""

import hashlib
import time
import urllib.parse
from typing import Any

import httpx

# ── constants ────────────────────────────────────────────────────────────────

MIXIN_KEY_ENC_TAB: list[int] = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
_CACHE_DURATION = 86400  # 24 hours in seconds

# ── cache ────────────────────────────────────────────────────────────────────

_cache: dict[str, Any] = {
    "mixin_key": None,
    "timestamp": 0,
}

# ── key helpers ──────────────────────────────────────────────────────────────


def clear_cache() -> None:
    """Reset the cached mixin key (useful for testing)."""
    _cache["mixin_key"] = None
    _cache["timestamp"] = 0


def _extract_key_from_url(url: str) -> str:
    """Extract the key portion from a Bilibili wbi image URL.

    URL format: ``https://i0.hdslb.com/bfs/wbi/<32-hex-chars>.png``
    """
    _, key_part = url.split("wbi/", 1)
    return key_part.rsplit(".", 1)[0]


def _compute_mixin_key(img_key: str, sub_key: str) -> str:
    """Scramble *img_key* + *sub_key* via MIXIN_KEY_ENC_TAB, take first 32 chars."""
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _fetch_keys_from_api() -> tuple[str, str]:
    """Fetch *img_key* and *sub_key* from the Bilibili nav endpoint."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com",
    }
    resp = httpx.get(_NAV_URL, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    wbi_img = body["data"]["wbi_img"]
    return (
        _extract_key_from_url(wbi_img["img_url"]),
        _extract_key_from_url(wbi_img["sub_url"]),
    )


def _get_mixin_key() -> str:
    """Return the current mixin key, fetching & caching if necessary."""
    now = time.time()
    if _cache["mixin_key"] is not None and (now - _cache["timestamp"]) < _CACHE_DURATION:
        return _cache["mixin_key"]

    img_key, sub_key = _fetch_keys_from_api()
    mixin_key = _compute_mixin_key(img_key, sub_key)

    _cache["mixin_key"] = mixin_key
    _cache["timestamp"] = now
    return mixin_key


# ── value encoding ───────────────────────────────────────────────────────────


def _encode_value(value: str) -> str:
    """URL-encode *value* with uppercase hex, after stripping ``!'()*`` chars."""
    for ch in "!'()*":
        value = value.replace(ch, "")

    encoded = urllib.parse.quote(value, safe="")

    # Promote lowercase hex to uppercase:  %e4  →  %E4
    result: list[str] = []
    i = 0
    while i < len(encoded):
        if encoded[i] == "%" and i + 2 < len(encoded):
            result.append("%")
            result.append(encoded[i + 1].upper())
            result.append(encoded[i + 2].upper())
            i += 3
        else:
            result.append(encoded[i])
            i += 1
    return "".join(result)


# ── public API ───────────────────────────────────────────────────────────────


def sign_params(params: dict) -> dict:
    """Return a copy of *params* with ``w_rid`` and ``wts`` added.

    Steps performed:
    1. Fetch / use cached mixin key
    2. Add a ``wts`` (unix timestamp) parameter
    3. Sort parameters alphabetically by key name
    4. URL-encode values (uppercase hex, ``!'()*`` filtered out)
    5. Concatenate as ``key1=val1&key2=val2&...&wts=<ts>``
    6. Append mixin key, MD5 the whole string → ``w_rid``
    7. Return original params with ``w_rid`` and ``wts`` added

    The returned dict can be used directly as URL query parameters.
    """
    mixin_key = _get_mixin_key()
    wts = int(time.time())

    # ── build the string to sign ──
    signing_params = dict(params)
    signing_params["wts"] = wts

    sorted_items = sorted(signing_params.items(), key=lambda x: x[0])

    query_parts: list[str] = []
    for key, value in sorted_items:
        encoded_value = _encode_value(str(value))
        query_parts.append(f"{key}={encoded_value}")

    query_string = "&".join(query_parts)

    # ── MD5 ──
    to_sign = query_string + mixin_key
    w_rid = hashlib.md5(to_sign.encode("utf-8")).hexdigest()

    # ── result ──
    result = dict(params)
    result["w_rid"] = w_rid
    result["wts"] = wts
    return result
