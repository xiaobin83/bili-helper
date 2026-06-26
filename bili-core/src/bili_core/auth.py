"""
Combined authentication module for B站 utilities.

Credential priority: .auth.json → env vars → QR login flow.
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import httpx
import qrcode
import qrcode.image.svg

# B站 API endpoints
_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
_NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# Polling constants
_POLL_INTERVAL = 2  # seconds
_QR_EXPIRE_SECONDS = 60
_MAX_NO_SCAN_POLLS = 5  # exit early after this many NOT_SCANNED polls

# Poll response codes (from data.code)
_POLL_NOT_SCANNED = 86101
_POLL_SCANNED = 86090
_POLL_EXPIRED = 86038
_POLL_SUCCESS = 0

_AUTH_FILE: Path = Path.cwd() / ".auth.json"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@dataclass
class Credentials:
    """B站 login credentials."""

    sessdata: str
    bili_jct: str
    buvid3: str = ""
    mid: int = 0

    def __repr__(self) -> str:
        """Mask sensitive fields in repr to prevent accidental logging."""
        return (
            f"Credentials(sessdata='{_mask(self.sessdata)}', "
            f"bili_jct='{_mask(self.bili_jct)}', "
            f"buvid3='{_mask(self.buvid3)}', mid={self.mid})"
        )

    def __str__(self) -> str:
        return repr(self)

    @property
    def cookie_str(self) -> str:
        """Return the cookie string for HTTP requests."""
        parts = [f"SESSDATA={self.sessdata}", f"bili_jct={self.bili_jct}"]
        if self.buvid3:
            parts.append(f"buvid3={self.buvid3}")
        return "; ".join(parts)


def _mask(value: str, visible: int = 4) -> str:
    """Mask a credential string, showing only the first and last few chars."""
    if not value:
        return "<empty>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


# ---------------------------------------------------------------------------
# Credential loading (priority: file → env → QR login)
# ---------------------------------------------------------------------------


def _load_from_file(auth_file: Optional[Path] = None) -> Optional[Credentials]:
    """Try loading credentials from .auth.json."""
    if auth_file is None:
        auth_file = _AUTH_FILE
    if not auth_file.exists():
        return None
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
        return Credentials(
            sessdata=data["sessdata"],
            bili_jct=data["bili_jct"],
            buvid3=data.get("buvid3", ""),
            mid=data.get("mid", 0),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _load_from_env(env_prefix: str = "BILI_") -> Optional[Credentials]:
    """Try loading credentials from environment variables with given prefix.

    When env_prefix is ``"BILI_"``, also checks the deprecated ``FAV_*``
    variables as a fallback and emits a deprecation warning.
    """
    sessdata = os.environ.get(f"{env_prefix}SESSDATA")
    bili_jct = os.environ.get(f"{env_prefix}BILI_JCT")

    # Dual-read FAV_* during migration (only when prefix is BILI_)
    if not sessdata and env_prefix == "BILI_":
        sessdata = os.environ.get("FAV_SESSDATA")
        if sessdata:
            warnings.warn("FAV_SESSDATA is deprecated, use BILI_SESSDATA instead", stacklevel=2)
    if not bili_jct and env_prefix == "BILI_":
        bili_jct = os.environ.get("FAV_BILI_JCT")
        if bili_jct:
            warnings.warn("FAV_BILI_JCT is deprecated, use BILI_BILI_JCT instead", stacklevel=2)

    if not sessdata or not bili_jct:
        return None

    buvid3 = os.environ.get(f"{env_prefix}BUVID3")
    mid = os.environ.get(f"{env_prefix}MID")

    if not buvid3 and env_prefix == "BILI_":
        buvid3 = os.environ.get("FAV_BUVID3")
        if buvid3:
            warnings.warn("FAV_BUVID3 is deprecated, use BILI_BUVID3 instead", stacklevel=2)
    if not mid and env_prefix == "BILI_":
        mid = os.environ.get("FAV_MID")
        if mid:
            warnings.warn("FAV_MID is deprecated, use BILI_MID instead", stacklevel=2)

    return Credentials(
        sessdata=sessdata,
        bili_jct=bili_jct,
        buvid3=buvid3 or "",
        mid=int(mid) if mid else 0,
    )


def get_credentials(
    env_prefix: str = "BILI_",
    auth_file: Optional[Path] = None,
) -> Credentials:
    """Return valid credentials, triggering QR login if necessary.

    Priority:
        1. ``auth_file`` (defaults to ``.auth.json`` in CWD)
        2. ``{env_prefix}SESSDATA`` / ``{env_prefix}BILI_JCT`` env vars
        3. Interactive QR code login flow
    """
    if auth_file is None:
        auth_file = _AUTH_FILE

    # Priority 1: file
    creds = _load_from_file(auth_file)
    if creds is not None:
        return creds

    # Priority 2: environment
    creds = _load_from_env(env_prefix)
    if creds is not None:
        return creds

    # Priority 3: QR login
    return login_flow(auth_file=auth_file)


# ---------------------------------------------------------------------------
# Helper: navigate API for user info + expiry check
# ---------------------------------------------------------------------------


def _nav_api(creds: Credentials) -> httpx.Response:
    """Call /x/web-interface/nav with the given credentials."""
    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        return client.get(
            _NAV_URL,
            headers={**{"Cookie": creds.cookie_str}, **_AUTH_HEADERS},
        )


# ---------------------------------------------------------------------------
# Expiry check
# ---------------------------------------------------------------------------


def check_expired(
    creds: Optional[Credentials] = None,
    env_prefix: str = "BILI_",
) -> bool:
    """Check whether the SESSDATA credential is expired (or invalid).

    Returns True if the credential is expired/invalid, False if valid.
    If no creds provided, attempts to load from file or env.
    """
    if creds is None:
        creds = _load_from_file(_AUTH_FILE)
        if creds is None:
            creds = _load_from_env(env_prefix)
        if creds is None:
            # No credentials at all → consider expired
            return True

    try:
        resp = _nav_api(creds)
        data = resp.json()
        return data.get("code") == -101
    except (httpx.RequestError, json.JSONDecodeError, ValueError):
        # Network error → treat as unknown, return False (assume valid)
        return False


# ---------------------------------------------------------------------------
# QR login flow
# ---------------------------------------------------------------------------

_AUTH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _generate_qr(client: httpx.Client) -> tuple[str, str]:
    """Generate a QR code for login. Returns (qrcode_key, url)."""
    resp = client.get(_GENERATE_URL, headers=_AUTH_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"QR generate failed: {data.get('message', 'unknown')}")
    return data["data"]["qrcode_key"], data["data"]["url"]


def _display_qr(url: str) -> None:
    """Display QR code: print URL and ASCII QR to terminal."""
    print(f"\n📋 二维码链接: {url}\n")
    _print_ascii_qr(url)
    print("👆 请使用B站APP扫描二维码登录\n")


def _print_ascii_qr(url: str) -> None:
    """Print an ASCII QR code to the terminal."""
    qr = qrcode.QRCode(box_size=1, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    # Use the terminal-friendly ASCII renderer
    qr.print_ascii(invert=True)


def _poll_login(client: httpx.Client, qrcode_key: str) -> Credentials:
    """Poll the login status until success or expiry. Returns credentials on success."""
    deadline = time.monotonic() + _QR_EXPIRE_SECONDS
    prev_status: Optional[int] = None
    no_scan_count = 0

    while time.monotonic() < deadline:
        resp = client.get(
            _POLL_URL, params={"qrcode_key": qrcode_key}, headers=_AUTH_HEADERS
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", {})
        status = data.get("code")

        # Only print on status change to reduce noise
        if status != prev_status:
            prev_status = status
            if status == _POLL_NOT_SCANNED:
                print("⏳ 等待扫码...")
            elif status == _POLL_SCANNED:
                print("📱 已扫描，请在手机上确认...")
            elif status == _POLL_EXPIRED:
                print("❌ 二维码已失效，请重新运行")
                sys.exit(1)
            elif status == _POLL_SUCCESS:
                print("✅ 登录成功！")
                return _extract_credentials(resp)
            else:
                msg = data.get("message", f"未知状态码: {status}")
                print(f"⚠️  {msg}")

        # Early exit: too many consecutive NOT_SCANNED polls
        if status == _POLL_NOT_SCANNED:
            no_scan_count += 1
            if no_scan_count >= _MAX_NO_SCAN_POLLS:
                remaining = int(deadline - time.monotonic())
                print(
                    f"⏰ 已等待 {_MAX_NO_SCAN_POLLS * _POLL_INTERVAL}s 仍未扫码，"
                    f"二维码还剩 {max(0, remaining)}s 有效"
                )
                print(
                    "💡 提示：浏览器可能已有登录态导致未显示二维码，"
                    "请复制上方链接到隐私/无痕窗口打开，或直接在终端扫描 ASCII 二维码"
                )
                no_scan_count = 0  # reset to keep polling
        else:
            no_scan_count = 0

        if status == _POLL_SUCCESS:
            return _extract_credentials(resp)

        time.sleep(_POLL_INTERVAL)

    print("❌ 二维码已超时，请重新运行")
    sys.exit(1)


def _extract_credentials(resp: httpx.Response) -> Credentials:
    """Extract SESSDATA, bili_jct, and mid from the poll success response."""
    sessdata = resp.cookies.get("SESSDATA") or ""
    bili_jct = resp.cookies.get("bili_jct") or ""

    if not sessdata:
        raise RuntimeError("登录成功但未获取到 SESSDATA cookie")

    # Get mid via nav API
    creds = Credentials(sessdata=sessdata, bili_jct=bili_jct)
    mid = 0
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            nav_headers = {**{"Cookie": creds.cookie_str}, **_AUTH_HEADERS}
            nav_resp = client.get(_NAV_URL, headers=nav_headers)
            nav_data = nav_resp.json()
            if nav_data.get("code") == 0:
                mid = nav_data["data"].get("mid", 0)
    except Exception:
        pass  # mid is best-effort

    creds.mid = mid
    return creds


def _save_credentials(creds: Credentials, auth_file: Optional[Path] = None) -> None:
    """Save credentials to auth file."""
    if auth_file is None:
        auth_file = _AUTH_FILE
    data = asdict(creds)
    auth_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(auth_file, 0o600)  # owner read/write only
    print(f"🔐 凭据已保存到 {auth_file}")


def login_flow(*, auth_file: Optional[Path] = None) -> Credentials:
    """Run the complete QR code login flow and return credentials.

    Steps:
        1. Generate QR code
        2. Display QR (browser → terminal fallback)
        3. Poll for scan confirmation
        4. Extract and save credentials
    """
    with httpx.Client(
        timeout=httpx.Timeout(30.0), follow_redirects=True
    ) as client:
        print("🔑 正在申请二维码...")
        qrcode_key, url = _generate_qr(client)

        _display_qr(url)

        creds = _poll_login(client, qrcode_key)

    _save_credentials(creds, auth_file)
    return creds
