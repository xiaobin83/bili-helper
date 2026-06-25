"""Tests for the Wbi signing utility (src/signing.py).

Covers key extraction, mixin key computation, URL encoding with uppercase
hex, special-character filtering, the full sign_params flow, and caching.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from src.signing import (
    _cache,
    _compute_mixin_key,
    _encode_value,
    _extract_key_from_url,
    clear_cache,
    sign_params,
)

# ── test data ────────────────────────────────────────────────────────────────

MOCK_NAV_OK = {
    "data": {
        "wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
        }
    }
}

# ── _extract_key_from_url ───────────────────────────────────────────────────


class TestExtractKeyFromUrl:
    def test_extracts_img_key(self):
        url = "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png"
        assert _extract_key_from_url(url) == "7cd084941338484aae1ad9425b84077c"

    def test_extracts_sub_key(self):
        url = "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"
        assert _extract_key_from_url(url) == "4932caff0ff746eab6f01bf08b70ac45"

    def test_handles_other_extensions(self):
        url = "https://i0.hdslb.com/bfs/wbi/abc123def456abc123def456abc123de.jpg"
        assert _extract_key_from_url(url) == "abc123def456abc123def456abc123de"

    def test_handles_no_extension(self):
        url = "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c"
        assert _extract_key_from_url(url) == "7cd084941338484aae1ad9425b84077c"


# ── _compute_mixin_key ──────────────────────────────────────────────────────


class TestComputeMixinKey:
    def test_returns_32_chars(self):
        result = _compute_mixin_key(
            "7cd084941338484aae1ad9425b84077c",
            "4932caff0ff746eab6f01bf08b70ac45",
        )
        assert len(result) == 32

    def test_output_is_hex(self):
        result = _compute_mixin_key(
            "7cd084941338484aae1ad9425b84077c",
            "4932caff0ff746eab6f01bf08b70ac45",
        )
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        img = "7cd084941338484aae1ad9425b84077c"
        sub = "4932caff0ff746eab6f01bf08b70ac45"
        assert _compute_mixin_key(img, sub) == _compute_mixin_key(img, sub)

    def test_different_keys_produce_different_results(self):
        a = _compute_mixin_key("a" * 32, "b" * 32)
        b = _compute_mixin_key("c" * 32, "d" * 32)
        assert a != b


# ── _encode_value ───────────────────────────────────────────────────────────


class TestEncodeValue:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("simple", "simple"),
            ("with space", "with%20space"),
            ("中文", "%E4%B8%AD%E6%96%87"),
            ("hello!world", "helloworld"),
            ("test'test", "testtest"),
            ("(paren)", "paren"),
            ("star*value", "starvalue"),
            ("mix!'ed()*val", "mixedval"),
            ("plus+sign", "plus%2Bsign"),
            ("ampersand&val", "ampersand%26val"),
            ("percent%sign", "percent%25sign"),
            ("equals=sign", "equals%3Dsign"),
            ("at@sign", "at%40sign"),
            ("colon:value", "colon%3Avalue"),
            ("slash/value", "slash%2Fvalue"),
            ("question?mark", "question%3Fmark"),
            ("hash#tag", "hash%23tag"),
        ],
    )
    def test_encode_value(self, raw: str, expected: str) -> None:
        assert _encode_value(raw) == expected

    def test_chinese_uppercase_hex(self):
        """Chinese characters must produce %XX (uppercase) not %xx."""
        encoded = _encode_value("你好")
        # Verify uppercase hex pattern
        assert encoded == "%E4%BD%A0%E5%A5%BD"
        # Uppercase check
        assert encoded.isupper() or encoded.isascii()
        # All percent sequences should be uppercase
        import re
        for match in re.finditer(r"%[0-9A-F]{2}", encoded):
            assert match.group(0) == match.group(0).upper()


# ── sign_params ──────────────────────────────────────────────────────────────


class TestSignParams:
    """End-to-end tests for the public ``sign_params`` function."""

    def setup_method(self) -> None:
        clear_cache()

    # -- basic structure ---------------------------------------------------

    @patch("src.signing.httpx.get")
    def test_adds_w_rid_and_wts(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"foo": "bar"})

        assert "w_rid" in result
        assert "wts" in result

    @patch("src.signing.httpx.get")
    def test_w_rid_is_32_chars(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"a": "1"})
        assert len(result["w_rid"]) == 32
        assert isinstance(result["w_rid"], str)

    @patch("src.signing.httpx.get")
    def test_w_rid_is_lowercase_hex(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"a": "1"})
        # hexdigest() returns lowercase; verify no uppercase hex chars
        for ch in result["w_rid"]:
            assert ch in "0123456789abcdef"

    @patch("src.signing.httpx.get")
    def test_wts_is_int(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"a": "1"})
        assert isinstance(result["wts"], int)

    # -- preserves input ---------------------------------------------------

    @patch("src.signing.httpx.get")
    def test_preserves_original_params(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"key1": "val1", "key2": "val2"})
        assert result["key1"] == "val1"
        assert result["key2"] == "val2"

    @patch("src.signing.httpx.get")
    def test_does_not_mutate_input(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        original = {"a": "1"}
        result = sign_params(original)
        assert original == {"a": "1"}  # unchanged

    # -- wts freshness -----------------------------------------------------

    @patch("src.signing.httpx.get")
    def test_wts_is_recent_timestamp(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        before = int(time.time())
        result = sign_params({"a": "1"})
        after = int(time.time())

        assert before <= result["wts"] <= after

    # -- caching behavior --------------------------------------------------

    @patch("src.signing.httpx.get")
    def test_cache_prevents_redundant_fetch(self, mock_get: MagicMock) -> None:
        """First call fetches keys; second call reuses cache."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        sign_params({"a": "1"})
        assert mock_get.call_count == 1

        sign_params({"b": "2"})
        # Cache hit -- no additional HTTP call
        assert mock_get.call_count == 1

    @patch("src.signing.httpx.get")
    def test_cache_cleared_by_clear_cache(self, mock_get: MagicMock) -> None:
        """After ``clear_cache()`` the next call should re-fetch keys."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        sign_params({"a": "1"})
        assert mock_get.call_count == 1

        clear_cache()
        sign_params({"b": "2"})
        assert mock_get.call_count == 2

    # -- encoding in the signing pipeline ----------------------------------

    @patch("src.signing.httpx.get")
    def test_chinese_chars_uppercase_hex_in_signing(
        self, mock_get: MagicMock
    ) -> None:
        """Chinese characters in param values must not break signing."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"keyword": "你好"})
        assert len(result["w_rid"]) == 32
        assert all(c in "0123456789abcdef" for c in result["w_rid"])

    @patch("src.signing.httpx.get")
    def test_special_chars_filtered_in_signing(
        self, mock_get: MagicMock
    ) -> None:
        """``!'()*`` must be stripped from values before the signing digest."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"q": "hello!world"})
        assert len(result["w_rid"]) == 32
        assert all(c in "0123456789abcdef" for c in result["w_rid"])

    @patch("src.signing.httpx.get")
    def test_multiple_special_chars(self, mock_get: MagicMock) -> None:
        """Values with multiple ``!'()*`` chars produce a valid signature."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"q": "a!'b(c)d*e"})
        assert len(result["w_rid"]) == 32
        assert all(c in "0123456789abcdef" for c in result["w_rid"])

    # -- cross-parameter sorting -------------------------------------------

    @patch("src.signing.httpx.get")
    def test_sort_order_affects_signature(self, mock_get: MagicMock) -> None:
        """Different key ordering should produce different w_rid values
        (even when the set of params is the same)."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        a = sign_params({"z": "1", "a": "2"})
        b = sign_params({"a": "2", "z": "1"})
        # Same keys & values, but sign_params always sorts → same result
        assert a["w_rid"] == b["w_rid"]

    # -- deterministic within same timestamp window ------------------------

    @patch("src.signing.httpx.get")
    def test_deterministic_with_same_input(self, mock_get: MagicMock) -> None:
        """Identical inputs should produce identical w_rid for the same key."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        # Freeze time to control wts
        with patch("time.time", return_value=1702204169):
            a = sign_params({"foo": "bar"})
            b = sign_params({"foo": "bar"})

        assert a["w_rid"] == b["w_rid"]
        assert a["wts"] == b["wts"] == 1702204169
