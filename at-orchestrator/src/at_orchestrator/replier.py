"""Replier module — sends comment replies and private messages via B站 APIs.

TDD: see tests/test_replier.py and tests/test_replier_pm.py for the test suites.
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


# ── Private message API ──────────────────────────────────────────────────

_PM_SEND_URL = "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg"
_PM_SESSION_URL = "https://api.vc.bilibili.com/web_im/v1/web_im/session_detail"


async def reply_pm(
    client: object,  # BiliHTTPClient (avoid circular import)
    sender_uid: int,
    receiver_id: int,
    content: str,
) -> bool:
    """Send a private message via the B站 web_im API.

    Parameters
    ----------
    client:
        A ``BiliHTTPClient`` instance with valid credentials.
    sender_uid:
        The UID of the sender (the logged-in user).
    receiver_id:
        The UID of the message recipient.
    content:
        Raw message text.  Automatically truncated to 600 characters
        (~2000-byte safety margin for CJK multi-byte encoding).

    Returns
    -------
    bool
        ``True`` when the request succeeds (code=0).

    Raises
    ------
    CSRFError
        CSRF token is invalid (code=-111).  The caller must refresh
        credentials.

    Returns ``False`` for all other recoverable failures, including
    authentication expiry (code=-101), 1-msg limit (21047), no phone
    bound (21015), and unknown error codes.
    """
    import json
    import time
    import urllib.parse
    import uuid

    from bili_core.signing import sign_params

    # -- generate dev_id ---------------------------------------------------
    dev_id: str = str(uuid.uuid4())

    # -- truncate content --------------------------------------------------
    truncated: str = _truncate_for_comment(content, max_chars=600)

    # -- build content JSON ------------------------------------------------
    content_json: str = json.dumps({"content": truncated}, ensure_ascii=False)

    # -- sign URL params ---------------------------------------------------
    url_params: dict[str, object] = {
        "w_sender_uid": sender_uid,
        "w_receiver_id": receiver_id,
        "w_dev_id": dev_id,
    }
    signed = sign_params(url_params)
    signed_query: str = urllib.parse.urlencode(signed)
    signed_url: str = f"{_PM_SEND_URL}?{signed_query}"

    # -- build POST body ---------------------------------------------------
    body: dict[str, object] = {
        "msg[sender_uid]": sender_uid,
        "msg[receiver_id]": receiver_id,
        "msg[receiver_type]": 1,
        "msg[msg_type]": 1,
        "msg[dev_id]": dev_id,
        "msg[timestamp]": int(time.time()),
        "msg[content]": content_json,
        "msg[new_face_version]": 1,
        "csrf": client.bili_jct,  # type: ignore[union-attr]
        "csrf_token": client.bili_jct,  # type: ignore[union-attr]
    }

    # -- execute -----------------------------------------------------------
    try:
        result: dict = await client.post(signed_url, data=body)  # type: ignore[union-attr]
    except CSRFError:
        raise  # fatal — caller must refresh credentials
    except AuthError:
        logger.warning("reply_pm: auth expired")
        return False

    code: int = result.get("code", -1)

    if code == 0:
        return True

    logger.warning(
        "reply_pm: code %s — %s",
        code,
        result.get("message", ""),
    )
    return False


async def check_session_detail(
    client: object,  # BiliHTTPClient (avoid circular import)
    sender_uid: int,
    receiver_id: int,
) -> bool:
    """Check if a private message conversation already exists between two users.

    Calling this before ``reply_pm()`` avoids the 1-msg limit (error 21047):
    when no session exists, prefer a comment reply over a private message.

    Parameters
    ----------
    client:
        A ``BiliHTTPClient`` instance with valid credentials.
    sender_uid:
        The UID of the sender (the logged-in user).
    receiver_id:
        The UID of the message recipient.

    Returns
    -------
    bool
        ``True`` if a session already exists between the two users.
        ``False`` if no session exists or the check fails (e.g. auth expired).

    Raises
    ------
    CSRFError
        CSRF token is invalid (code=-111).  The caller must refresh
        credentials.
    """
    params: dict[str, object] = {
        "uid1": sender_uid,
        "uid2": receiver_id,
        "build": 0,
        "mobi_app": "web",
    }

    try:
        result: dict = await client.get(  # type: ignore[union-attr]
            _PM_SESSION_URL, params=params
        )
    except CSRFError:
        raise  # fatal — caller must refresh credentials
    except AuthError:
        logger.warning("check_session_detail: auth expired")
        return False

    data = result.get("data")
    if data and isinstance(data, dict) and len(data) > 0:
        return True
    return False
