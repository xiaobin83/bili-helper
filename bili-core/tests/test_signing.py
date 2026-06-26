"""Tests for the Wbi signing utility (bili_core.signing).

Covers sign_params output structure, input immutability, mixin key
determinism, and cache reset.
"""

from unittest.mock import MagicMock, patch

import pytest

from bili_core.signing import _cache, _compute_mixin_key, clear_cache, sign_params

# ── test data ────────────────────────────────────────────────────────────────

MOCK_NAV_OK = {
    "data": {
        "wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
        }
    }
}


class TestSignParams:
    """End-to-end tests for the public ``sign_params`` function."""

    def setup_method(self) -> None:
        clear_cache()

    # -- test 1: sign_params produces w_rid and wts ---------------------------

    @patch("bili_core.signing.httpx.get")
    def test_sign_params_adds_wrid_and_wts(self, mock_get: MagicMock) -> None:
        """sign_params must add ``w_rid`` (32-char hex) and ``wts``."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        result = sign_params({"keyword": "test"})

        assert "w_rid" in result
        assert "wts" in result
        assert len(result["w_rid"]) == 32

    # -- test 2: does not mutate input ----------------------------------------

    @patch("bili_core.signing.httpx.get")
    def test_sign_params_does_not_mutate_input(self, mock_get: MagicMock) -> None:
        """The input dict must remain unchanged after sign_params."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        original = {"keyword": "test"}
        copy = dict(original)
        sign_params(original)

        assert original == copy

    # -- test 3: _compute_mixin_key is deterministic -------------------------

    def test_compute_mixin_key_deterministic(self) -> None:
        """Same keys must produce same 32-char mixin key."""
        img_key = "7cd084941338484aae1ad9425b84077c"
        sub_key = "4932caff0ff746eab6f01bf08b70ac45"

        result = _compute_mixin_key(img_key, sub_key)

        assert len(result) == 32
        assert result == _compute_mixin_key(img_key, sub_key)

    # -- test 4: clear_cache resets internal state ----------------------------

    @patch("bili_core.signing.httpx.get")
    def test_clear_cache_resets_state(self, mock_get: MagicMock) -> None:
        """After clear_cache, internal _cache must have None/0 values."""
        mock_get.return_value = MagicMock(json=lambda: MOCK_NAV_OK)

        # Populate the cache
        sign_params({"a": "1"})

        clear_cache()

        assert _cache["mixin_key"] is None
        assert _cache["timestamp"] == 0
