"""Tests for bili_core.auth module."""

from __future__ import annotations

import json
import os
import pathlib
import warnings
from unittest.mock import patch

import pytest

from bili_core.auth import Credentials, get_credentials, _load_from_env


class TestCredentialsDataclass:
    """Credentials dataclass: cookie_str property and repr masking."""

    def test_cookie_str(self) -> None:
        """Cookie string should include all three fields when buvid3 is set."""
        c = Credentials(sessdata="sess_val", bili_jct="jct_val", buvid3="buv_val")
        assert "SESSDATA=sess_val" in c.cookie_str
        assert "bili_jct=jct_val" in c.cookie_str
        assert "buvid3=buv_val" in c.cookie_str

    def test_mask_repr(self) -> None:
        """repr() should show first/last 4 chars for long values, mask short ones entirely."""
        c = Credentials(sessdata="abc123def456", bili_jct="xyz789", buvid3="uvwxyz123456")
        r = repr(c)
        # sessdata (12 chars > 8): first 4 "abc1" + "..." + last 4 "f456"
        assert "abc1" in r
        assert "f456" in r
        assert "def4" not in r  # middle masked
        # bili_jct (6 chars <= 8): fully masked
        assert "xyz7" not in r
        assert "******" in r
        # buvid3 (12 chars > 8): shows first/last 4
        assert "uvwx" in r
        assert "3456" in r


class TestLoadFromEnv:
    """Environment variable loading with configurable prefix."""

    def test_load_from_env_with_prefix(self, tmp_path: pathlib.Path) -> None:
        """get_credentials should load BILI_* env vars when prefix is BILI_."""
        auth_file = tmp_path / ".auth.json"
        env = {
            "BILI_SESSDATA": "test_sess",
            "BILI_BILI_JCT": "test_jct",
            "BILI_BUVID3": "buv",
        }
        with patch.dict(os.environ, env, clear=False):
            c = get_credentials(env_prefix="BILI_", auth_file=auth_file)
        assert c.sessdata == "test_sess"
        assert c.bili_jct == "test_jct"
        assert c.buvid3 == "buv"

    def test_load_from_env_fav_fallback(self, tmp_path: pathlib.Path) -> None:
        """get_credentials should fall back to FAV_* env vars with deprecation warning."""
        auth_file = tmp_path / ".auth.json"
        env = {
            "FAV_SESSDATA": "fav_sess",
            "FAV_BILI_JCT": "fav_jct",
            "FAV_BUVID3": "fav_buv",
        }
        with patch.dict(os.environ, env, clear=False):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                c = get_credentials(env_prefix="BILI_", auth_file=auth_file)

        assert c.sessdata == "fav_sess"
        assert c.bili_jct == "fav_jct"
        assert c.buvid3 == "fav_buv"

        # Check deprecation warnings were emitted
        messages = [str(x.message) for x in w]
        assert any(
            "FAV_SESSDATA is deprecated" in msg and "BILI_SESSDATA" in msg
            for msg in messages
        )
        assert any(
            "FAV_BILI_JCT is deprecated" in msg and "BILI_BILI_JCT" in msg
            for msg in messages
        )
        assert any(
            "FAV_BUVID3 is deprecated" in msg and "BILI_BUVID3" in msg
            for msg in messages
        )


class TestLoadFromFile:
    """File-based credential loading."""

    def test_get_credentials_custom_auth_file(self, tmp_path: pathlib.Path) -> None:
        """get_credentials should load from a custom auth file path."""
        auth_file = tmp_path / ".auth.json"
        data = {
            "sessdata": "file_sess",
            "bili_jct": "file_jct",
            "buvid3": "",
            "mid": 0,
        }
        auth_file.write_text(json.dumps(data))

        c = get_credentials(auth_file=auth_file)
        assert c.sessdata == "file_sess"
        assert c.bili_jct == "file_jct"
        assert c.buvid3 == ""
        assert c.mid == 0
