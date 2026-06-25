"""Tests for confirm.py — interactive confirmation prompt."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.confirm import confirm_execution


class TestConfirmExecution:
    """confirm_execution() accepting/rejecting behaviour."""

    PREVIEW = "This will delete 42 items."

    # -- accept -----------------------------------------------------------

    @pytest.mark.parametrize("text", ["y", "Y", "yes", "YeS"])
    def test_accepts_yes_variants(self, text: str) -> None:
        """``y``/``Y``/``yes`` (any casing) returns ``True``."""
        with patch("builtins.input", return_value=text):
            with patch("sys.stdout"):
                assert confirm_execution(self.PREVIEW) is True

    # -- reject -----------------------------------------------------------

    @pytest.mark.parametrize("text", ["n", "N", "no", "maybe", ""])
    def test_rejects_other_input(self, text: str) -> None:
        """Anything other than y/yes returns ``False``."""
        with patch("builtins.input", return_value=text):
            with patch("sys.stdout"):
                assert confirm_execution(self.PREVIEW) is False

    # -- timeout ----------------------------------------------------------

    def test_timeout_returns_false(self) -> None:
        """When input takes longer than *timeout*, return ``False``."""
        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout"):
                # Use a zero timeout so the timer fires immediately.
                assert confirm_execution(self.PREVIEW, timeout=0) is False

    # -- edge cases -------------------------------------------------------

    def test_timeout_none_waits_indefinitely(self) -> None:
        """``timeout=None`` disables the safety timeout."""
        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout"):
                assert confirm_execution(self.PREVIEW, timeout=None) is True

    @pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
    def test_eof_or_interrupt_returns_false(self, exc: type[BaseException]) -> None:
        """EOFError / KeyboardInterrupt during input returns ``False``."""
        with patch("builtins.input", side_effect=exc()):
            with patch("sys.stdout"):
                assert confirm_execution(self.PREVIEW) is False

    def test_preview_printed_to_stdout(self) -> None:
        """The *preview_text* is written to stdout before prompting."""
        with patch("builtins.input", return_value="y"):
            with patch("sys.stdout") as mock_stdout:
                confirm_execution(self.PREVIEW)
                # print(x) calls write(x) then write("\n") separately.
                written = "".join(
                    call.args[0]
                    for call in mock_stdout.write.mock_calls
                    if call.args
                )
                assert self.PREVIEW in written
