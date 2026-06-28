"""
Tests for fav-organizer/src/auth.py

Tests cover:
- Credentials dataclass (creation, repr masking, cookie_str)
- get_credentials() priority: file → env → QR login
- check_expired() validity detection
- login_flow() QR generate, poll states, credential saving
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch, call

import pytest

from bili_core.auth import (  # noqa: E402
    Credentials,
    _load_from_file,
    _load_from_env,
    _save_credentials,
    get_credentials,
    check_expired,
    login_flow,
    _AUTH_FILE_DEFAULT,
    _NAV_URL,
    _GENERATE_URL,
    _POLL_URL,
    _extract_credentials,
    _POLL_NOT_SCANNED,
    _POLL_SCANNED,
    _POLL_EXPIRED,
    _POLL_SUCCESS,
    _mask,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resp(json_data: dict, status_code: int = 200, cookies: dict | None = None):
    """Create a mock httpx.Response with given JSON body and cookies."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if cookies:
        resp.cookies = MagicMock()
        resp.cookies.get.side_effect = lambda name, default=None: cookies.get(name, default)
    else:
        resp.cookies = MagicMock()
        resp.cookies.get.return_value = None
    return resp


def _mock_client(responses: list):
    """Create a mock httpx.Client that returns staged responses on .get()."""
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    # Return responses in order
    client.get = MagicMock(side_effect=responses)
    return client


# ---------------------------------------------------------------------------
# Credentials dataclass
# ---------------------------------------------------------------------------

class TestCredentials:
    """Tests for the Credentials dataclass."""

    def test_create(self):
        c = Credentials(sessdata="abc123", bili_jct="xyz789", buvid3="buv001", mid=42)
        assert c.sessdata == "abc123"
        assert c.bili_jct == "xyz789"
        assert c.buvid3 == "buv001"
        assert c.mid == 42

    def test_create_defaults(self):
        c = Credentials(sessdata="abc", bili_jct="xyz")
        assert c.buvid3 == ""
        assert c.mid == 0

    def test_repr_masks_credentials(self):
        c = Credentials(sessdata="verylongsessdata123456", bili_jct="secret_jct_value", buvid3="buv12345")
        r = repr(c)
        # Must not contain the raw values
        assert "verylongsessdata123456" not in r
        assert "secret_jct_value" not in r
        assert "buv12345" not in r
        # Should show prefix+suffix with dots
        assert "very..." in r or "very" in r
        # second: "secr...alue" (4+4 visible)
        assert "secr..." in r or "secr" in r

    def test_str_same_as_repr(self):
        c = Credentials(sessdata="a", bili_jct="b")
        assert str(c) == repr(c)

    def test_cookie_str(self):
        c = Credentials(sessdata="s", bili_jct="j", buvid3="b")
        cs = c.cookie_str
        assert "SESSDATA=s" in cs
        assert "bili_jct=j" in cs
        assert "buvid3=b" in cs

    def test_cookie_str_no_buvid3(self):
        c = Credentials(sessdata="s", bili_jct="j", buvid3="")
        cs = c.cookie_str
        assert "buvid3" not in cs

    def test_mask_empty(self):
        assert _mask("") == "<empty>"

    def test_mask_short(self):
        assert _mask("abc") == "***"

    def test_mask_normal(self):
        masked = _mask("abcdefgh1234", visible=3)
        assert masked.startswith("abc")
        assert masked.endswith("234")
        assert "..." in masked


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

class TestLoadFromFile:
    """Tests for _load_from_file()."""

    def test_file_not_found(self, tmp_path):
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", tmp_path / "nonexistent.json"):
            result = _load_from_file()
            assert result is None

    def test_valid_file(self, tmp_path):
        auth_file = tmp_path / ".auth.json"
        data = {"sessdata": "sess123", "bili_jct": "jct456", "buvid3": "buv789", "mid": 10}
        auth_file.write_text(json.dumps(data))
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file):
            creds = _load_from_file()
            assert creds is not None
            assert creds.sessdata == "sess123"
            assert creds.bili_jct == "jct456"
            assert creds.buvid3 == "buv789"
            assert creds.mid == 10

    def test_file_no_buvid3_mid(self, tmp_path):
        auth_file = tmp_path / ".auth.json"
        data = {"sessdata": "sess", "bili_jct": "jct"}
        auth_file.write_text(json.dumps(data))
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file):
            creds = _load_from_file()
            assert creds is not None
            assert creds.buvid3 == ""
            assert creds.mid == 0

    def test_invalid_json(self, tmp_path):
        auth_file = tmp_path / ".auth.json"
        auth_file.write_text("not valid json")
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file):
            result = _load_from_file()
            assert result is None

    def test_missing_keys(self, tmp_path):
        auth_file = tmp_path / ".auth.json"
        auth_file.write_text(json.dumps({"other": "value"}))
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file):
            result = _load_from_file()
            assert result is None


# ---------------------------------------------------------------------------
# Env loading
# ---------------------------------------------------------------------------

class TestLoadFromEnv:
    """Tests for _load_from_env()."""

    def test_no_env_vars(self, monkeypatch):
        monkeypatch.delenv("FAV_SESSDATA", raising=False)
        monkeypatch.delenv("FAV_BILI_JCT", raising=False)
        result = _load_from_env()
        assert result is None

    def test_only_sessdata(self, monkeypatch):
        monkeypatch.setenv("FAV_SESSDATA", "sess")
        monkeypatch.delenv("FAV_BILI_JCT", raising=False)
        result = _load_from_env()
        assert result is None

    def test_both_required_vars(self, monkeypatch):
        monkeypatch.setenv("FAV_SESSDATA", "sess")
        monkeypatch.setenv("FAV_BILI_JCT", "jct")
        monkeypatch.delenv("FAV_BUVID3", raising=False)
        monkeypatch.delenv("FAV_MID", raising=False)
        result = _load_from_env()
        assert result is not None
        assert result.sessdata == "sess"
        assert result.bili_jct == "jct"
        assert result.buvid3 == ""
        assert result.mid == 0

    def test_all_env_vars(self, monkeypatch):
        monkeypatch.setenv("FAV_SESSDATA", "sess")
        monkeypatch.setenv("FAV_BILI_JCT", "jct")
        monkeypatch.setenv("FAV_BUVID3", "buv")
        monkeypatch.setenv("FAV_MID", "99")
        result = _load_from_env()
        assert result is not None
        assert result.mid == 99
        assert result.buvid3 == "buv"


# ---------------------------------------------------------------------------
# get_credentials priority
# ---------------------------------------------------------------------------

class TestGetCredentialsPriority:
    """Tests for get_credentials() priority order."""

    def test_file_priority(self, tmp_path):
        """When .auth.json exists, it should be used even if env vars are set."""
        auth_file = tmp_path / ".auth.json"
        auth_file.write_text(json.dumps({"sessdata": "file_sess", "bili_jct": "file_jct"}))
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file), \
             patch("bili_core.auth._AUTH_FILE_CANDIDATES", [auth_file]):
            with patch("bili_core.auth._load_from_env") as mock_env:
                mock_env.return_value = None
                creds = get_credentials()
                assert creds.sessdata == "file_sess"

    def test_env_fallback(self, tmp_path):
        """When no file, env vars should be used."""
        no_file = tmp_path / ".auth.json"
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", no_file):
            with patch("bili_core.auth._load_from_env") as mock_env:
                mock_env.return_value = Credentials(sessdata="env_sess", bili_jct="env_jct")
                creds = get_credentials()
                assert creds.sessdata == "env_sess"

    def test_triggers_login_flow(self, tmp_path):
        """When neither file nor env, login_flow() should be called."""
        no_file = tmp_path / ".auth.json"
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", no_file), \
             patch("bili_core.auth._AUTH_FILE_CANDIDATES", [no_file]):
            with patch("bili_core.auth._load_from_env") as mock_env:
                mock_env.return_value = None
                with patch("bili_core.auth.login_flow") as mock_login:
                    mock_login.return_value = Credentials(sessdata="qr_sess", bili_jct="qr_jct")
                    creds = get_credentials()
                    mock_login.assert_called_once()
                    assert creds.sessdata == "qr_sess"


# ---------------------------------------------------------------------------
# check_expired
# ---------------------------------------------------------------------------

class TestCheckExpired:
    """Tests for check_expired()."""

    NAV_VALID = {"code": 0, "data": {"isLogin": True, "mid": 1}}
    NAV_EXPIRED = {"code": -101, "message": "账号未登录"}

    def test_valid_credential(self):
        """code=0 means valid → return False."""
        creds = Credentials(sessdata="valid", bili_jct="jct")
        resp = _make_resp(self.NAV_VALID)
        with patch("bili_core.auth.httpx.Client") as mock_client_class:
            mock_client_class.return_value = _mock_client([resp])
            assert check_expired(creds) is False

    def test_expired_credential(self):
        """code=-101 means expired → return True."""
        creds = Credentials(sessdata="expired", bili_jct="jct")
        resp = _make_resp(self.NAV_EXPIRED)
        with patch("bili_core.auth.httpx.Client") as mock_client_class:
            mock_client_class.return_value = _mock_client([resp])
            assert check_expired(creds) is True

    def test_no_credentials_at_all(self, tmp_path):
        """When no creds available via file or env → return True."""
        no_file = tmp_path / ".auth.json"
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", no_file), \
             patch("bili_core.auth._AUTH_FILE_CANDIDATES", [no_file]), \
             patch("bili_core.auth._load_from_env", return_value=None):
            assert check_expired(None) is True

    def test_network_error_treats_as_valid(self):
        """Network errors during check → return False (assume valid)."""
        import httpx
        creds = Credentials(sessdata="s", bili_jct="j")
        with patch("bili_core.auth._nav_api") as mock_nav:
            mock_nav.side_effect = httpx.RequestError("network down")
            assert check_expired(creds) is False

    def test_loads_from_file_when_none_passed(self, tmp_path):
        """When creds=None, it should load from file automatically."""
        auth_file = tmp_path / ".auth.json"
        auth_file.write_text(json.dumps({"sessdata": "file_sess", "bili_jct": "file_jct"}))
        resp = _make_resp(self.NAV_VALID)
        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file):
            with patch("bili_core.auth.httpx.Client") as mock_client_class:
                mock_client_class.return_value = _mock_client([resp])
                assert check_expired(None) is False


# ---------------------------------------------------------------------------
# login_flow — QR generation & polling
# ---------------------------------------------------------------------------

class TestLoginFlow:
    """Tests for login_flow()."""

    QR_GENERATE_RESP = {
        "code": 0,
        "data": {
            "url": "https://passport.bilibili.com/h5-app/passport/login/scan?qrcode_key=testkey123",
            "qrcode_key": "testkey123",
        },
    }

    def _make_poll_resp(self, poll_code: int, cookies: dict | None = None):
        return _make_resp(
            {"code": 0, "data": {"code": poll_code, "message": ""}},
            cookies=cookies,
        )

    def test_full_login_flow_success(self, tmp_path):
        """End-to-end: generate → poll(success) → save to file."""
        auth_file = tmp_path / ".auth.json"

        # Mock _display_qr to avoid terminal output and browser opening
        with patch("bili_core.auth._display_qr"), patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file):
            # Mock httpx.Client
            with patch("bili_core.auth.httpx.Client") as mock_client_class:
                # response 1: QR generate
                gen_resp = _make_resp(self.QR_GENERATE_RESP)
                # response 2: poll success (with cookies)
                poll_resp = _make_resp(
                    {"code": 0, "data": {"code": _POLL_SUCCESS, "message": ""}},
                    cookies={"SESSDATA": "qr_sessdata", "bili_jct": "qr_jct"},
                )
                # response 3: nav API (get mid)
                nav_resp = _make_resp({"code": 0, "data": {"mid": 999}})

                mock_client_class.return_value = _mock_client([gen_resp, poll_resp, nav_resp])

                creds = login_flow()

                assert creds.sessdata == "qr_sessdata"
                assert creds.bili_jct == "qr_jct"
                assert creds.mid == 999

        # Verify .auth.json was saved
        assert auth_file.exists()
        saved = json.loads(auth_file.read_text())
        assert saved["sessdata"] == "qr_sessdata"
        assert saved["bili_jct"] == "qr_jct"
        assert saved["mid"] == 999

    def test_poll_not_scanned_then_success(self, tmp_path):
        """Poll returns 86101 (not scanned), then 0 (success)."""
        auth_file = tmp_path / ".auth.json"

        with patch("bili_core.auth._display_qr"), patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file), \
             patch("bili_core.auth.time.sleep"):  # skip sleep

            with patch("bili_core.auth.httpx.Client") as mock_client_class:
                gen_resp = _make_resp(self.QR_GENERATE_RESP)
                poll_not_scanned = self._make_poll_resp(_POLL_NOT_SCANNED)
                poll_scanned = self._make_poll_resp(_POLL_SCANNED)
                poll_success = self._make_poll_resp(
                    _POLL_SUCCESS,
                    cookies={"SESSDATA": "sess", "bili_jct": "jct"},
                )
                nav_resp = _make_resp({"code": 0, "data": {"mid": 1}})

                mock_client_class.return_value = _mock_client([
                    gen_resp, poll_not_scanned, poll_scanned, poll_success, nav_resp
                ])

                creds = login_flow()

                assert creds.sessdata == "sess"
                assert creds.bili_jct == "jct"

    def test_poll_expired_exits(self, tmp_path):
        """Poll returns 86038 (expired) → sys.exit(1)."""
        auth_file = tmp_path / ".auth.json"

        with patch("bili_core.auth._display_qr"), patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file), \
             patch("bili_core.auth.time.sleep"):

            with patch("bili_core.auth.httpx.Client") as mock_client_class:
                gen_resp = _make_resp(self.QR_GENERATE_RESP)
                poll_expired = self._make_poll_resp(_POLL_EXPIRED)

                mock_client_class.return_value = _mock_client([gen_resp, poll_expired])

                with pytest.raises(SystemExit) as exc_info:
                    login_flow()
                assert exc_info.value.code == 1

    def test_generate_fails(self):
        """QR generate endpoint returns error code."""
        with patch("bili_core.auth._display_qr"):
            with patch("bili_core.auth.httpx.Client") as mock_client_class:
                fail_resp = _make_resp({"code": -1, "message": "server error"})
                mock_client_class.return_value = _mock_client([fail_resp])

                with pytest.raises(RuntimeError, match="QR generate failed"):
                    login_flow()

    def test_poll_timeout(self, tmp_path):
        """Poll times out after 3 minutes (simulated)."""
        auth_file = tmp_path / ".auth.json"

        with patch("bili_core.auth._display_qr"), patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file), \
             patch("bili_core.auth.time.sleep"):
            # Patch time.monotonic to simulate timeout
            with patch("bili_core.auth.time.monotonic", side_effect=[0, 999999]):
                with patch("bili_core.auth.httpx.Client") as mock_client_class:
                    gen_resp = _make_resp(self.QR_GENERATE_RESP)
                    # After 999999 seconds, it's beyond the 180s deadline
                    mock_client_class.return_value = _mock_client([gen_resp])

                    with pytest.raises(SystemExit) as exc_info:
                        login_flow()
                    assert exc_info.value.code == 1

    def test_save_credentials_file_permissions(self, tmp_path):
        """Verify _save_credentials writes correct JSON with tight permissions."""
        auth_file = tmp_path / ".auth.json"
        creds = Credentials(sessdata="sess", bili_jct="jct", buvid3="buv", mid=5)

        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file):
            _save_credentials(creds)

        saved = json.loads(auth_file.read_text())
        assert saved["sessdata"] == "sess"
        assert saved["bili_jct"] == "jct"
        assert saved["buvid3"] == "buv"
        assert saved["mid"] == 5

        # Check file permissions (0o600)
        mode = auth_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# _extract_credentials edge cases
# ---------------------------------------------------------------------------

class TestExtractCredentials:
    """Tests for _extract_credentials()."""

    def test_missing_sessdata_raises(self):
        """If no SESSDATA in cookies, RuntimeError should be raised."""
        resp = _make_resp({"code": 0}, cookies={})
        with pytest.raises(RuntimeError, match="未获取到 SESSDATA"):
            _extract_credentials(resp)

    def test_nav_api_failure_graceful(self):
        """If nav API fails, mid should be 0 but credentials still returned."""
        resp = _make_resp({}, cookies={"SESSDATA": "s", "bili_jct": "j"})
        with patch("bili_core.auth.httpx.Client") as mock_client_class:
            # Nav API call fails
            fail_nav = _make_resp({"code": -500}, status_code=500)
            mock_client_class.return_value = _mock_client([fail_nav])
            creds = _extract_credentials(resp)
            assert creds.sessdata == "s"
            assert creds.bili_jct == "j"
            assert creds.mid == 0

    def test_nav_api_network_error_graceful(self):
        """If nav API raises, credentials still returned with mid=0."""
        resp = _make_resp({}, cookies={"SESSDATA": "s", "bili_jct": "j"})
        with patch("bili_core.auth.httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.__exit__.return_value = False
            mock_client.get.side_effect = Exception("network error")
            mock_client_class.return_value = mock_client
            creds = _extract_credentials(resp)
            assert creds.sessdata == "s"
            assert creds.mid == 0


# ---------------------------------------------------------------------------
# Integration: file priority over env
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration-style tests combining multiple components."""

    def test_file_wins_over_env(self, tmp_path, monkeypatch):
        """get_credentials uses file even when env has valid credentials."""
        auth_file = tmp_path / ".auth.json"
        auth_file.write_text(json.dumps({"sessdata": "file", "bili_jct": "file_jct", "mid": 1}))
        monkeypatch.setenv("FAV_SESSDATA", "env_sess")
        monkeypatch.setenv("FAV_BILI_JCT", "env_jct")

        with patch("bili_core.auth._AUTH_FILE_DEFAULT", auth_file), \
             patch("bili_core.auth._AUTH_FILE_CANDIDATES", [auth_file]):
            creds = get_credentials(auth_file=auth_file)
            assert creds.sessdata == "file"
            assert creds.mid == 1

    def test_env_used_when_no_file(self, tmp_path, monkeypatch):
        """get_credentials uses env when no .auth.json exists."""
        no_file = tmp_path / ".auth.json"
        monkeypatch.setenv("FAV_SESSDATA", "env_sess")
        monkeypatch.setenv("FAV_BILI_JCT", "env_jct")
        monkeypatch.setenv("FAV_BUVID3", "env_buv")
        monkeypatch.setenv("FAV_MID", "42")

        with patch("bili_core.auth._AUTH_FILE_DEFAULT", no_file):
            creds = get_credentials()
            assert creds.sessdata == "env_sess"
            assert creds.bili_jct == "env_jct"
            assert creds.buvid3 == "env_buv"
            assert creds.mid == 42
