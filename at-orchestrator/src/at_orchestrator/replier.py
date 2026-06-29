"""Replier module — sends comment replies to B站 via the comment API.

TDD: see tests/test_replier.py for the full test suite.
"""

from __future__ import annotations

import logging

from bili_core.errors import AuthError, CSRFError

logger = logging.getLogger(__name__)

# ── Business ID → comment type mapping ────────────────────────────────
# B站 uses different `type` values depending on the source:
#   video (business_id=1) → type=1
#   dynamic (business_id=11, 17) → type=17
_BUSINESS_ID_TO_TYPE: dict[int, int] = {
    1: 1,
    11: 17,
    17: 17,
}

_COMMENT_API_URL = "https://api.bilibili.com/x/v2/reply/add"

# Error codes that indicate a recoverable / transient failure rather than a
# permanent error.  See the B站 public error code documentation for details.
_NON_FATAL_CODES: frozenset[int] = frozenset({
    # 12015: frequency limit (level 2 — too many comments in a short window)
    # 12035: content blocked by anti-spam / keyword filter
    12015,
    12035,
})


# ── Pure helpers ──────────────────────────────────────────────────────


def _truncate_for_comment(text: str, max_chars: int = 1000) -> str:
    """Truncate *text* to *max_chars*, appending ``…`` if truncated.

    The implementation relies on Python 3 string indexing, which counts
    Unicode code points rather than bytes — safe for CJK, emoji, and
    other multi-byte characters.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ── Public API ────────────────────────────────────────────────────────


async def reply_comment(
    client: object,   # BiliHTTPClient (avoid circular import)
    task_dict: dict,
    content: str,
) -> bool:
    """Post a comment reply to B站.

    Parameters
    ----------
    client:
        A ``BiliHTTPClient`` instance with valid credentials.
    task_dict:
        A *Task* dict containing at least ``business_id``, ``subject_id``,
        ``root_id`` and ``source_id``.
    content:
        Raw reply text.  Automatically truncated to 1000 characters.

    Returns
    -------
    bool
        ``True`` when the request succeeds (code=0).

    Raises
    ------
    CSRFError
        CSRF token is invalid (code=-111).  The caller must refresh
        credentials.
    ValueError
        *business_id* is not one of the recognised values (1, 11, 17).

    Returns ``False`` for all other recoverable failures, including
    authentication expiry (code=-101, raised as ``AuthError`` by the
    HTTP client) and transient errors like rate-limiting (12015) or
    content filtering (12035).
    """
    # -- resolve comment type -------------------------------------------
    business_id = task_dict["business_id"]
    comment_type = _BUSINESS_ID_TO_TYPE.get(business_id)
    if comment_type is None:
        raise ValueError(
            f"Unknown business_id: {business_id} (expected 1, 11, or 17)"
        )

    # -- build request body ---------------------------------------------
    message = _truncate_for_comment(content)

    body: dict = {
        "type": comment_type,
        "oid": task_dict["subject_id"],
        "message": message,
    }

    root_id = task_dict.get("root_id") or 0
    if root_id:
        body["root"] = root_id

    # parent: prefer source_id; fall back to root_id
    parent_id = (task_dict.get("source_id") or 0) or root_id
    if parent_id:
        body["parent"] = parent_id

    # -- execute --------------------------------------------------------
    try:
        result: dict = await client.post(_COMMENT_API_URL, data=body)  # type: ignore[union-attr]
    except CSRFError:
        raise   # fatal — caller must refresh credentials
    except AuthError:
        logger.warning("reply_comment: auth expired")
        return False

    code: int = result.get("code", -1)

    if code == 0:
        return True

    if code in _NON_FATAL_CODES:
        logger.warning(
            "reply_comment: non-fatal code %s — %s",
            code,
            result.get("message", ""),
        )
        return False

    logger.warning(
        "reply_comment: unhandled code %s — %s",
        code,
        result.get("message", ""),
    )
    return False
