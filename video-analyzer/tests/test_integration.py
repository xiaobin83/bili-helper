"""End-to-end integration tests for video-analyzer CLI.

Invokes ``uv run video-analyzer --bvid <bvid>`` via subprocess and
checks exit codes and stdout output.  No mocking of network calls.

``test_cli_with_valid_bvid*`` tests require credentials
(either ``.auth.json`` or ``BILI_SESSDATA`` / ``FAV_SESSDATA`` env vars).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# Path to the video-analyzer project root (where pyproject.toml lives)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _credentials_available() -> bool:
    """Check whether B站 credentials are available (file or env var)."""
    if (PROJECT_ROOT / ".auth.json").exists():
        return True
    if os.environ.get("BILI_SESSDATA"):
        return True
    if os.environ.get("FAV_SESSDATA"):
        return True
    return False


def _run_cli(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run ``uv run video-analyzer`` with the given arguments."""
    cmd = ["uv", "run", "video-analyzer", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Valid BVID (requires credentials — otherwise the QR login flow blocks)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _credentials_available(), reason="需要 B站 凭证 (BILI_SESSDATA)")
def test_cli_with_valid_bvid() -> None:
    """``uv run video-analyzer --bvid BV1xx411c7m9`` exits 0, output contains
    ``## 🎬`` section header."""
    result = _run_cli("--bvid", "BV1xx411c7m9")
    assert result.returncode == 0, (
        f"CLI exited with {result.returncode}.\n"
        f"stderr: {result.stderr}\n"
        f"stdout: {result.stdout[:500]}"
    )
    assert "## 🎬" in result.stdout


@pytest.mark.skipif(not _credentials_available(), reason="需要 B站 凭证 (BILI_SESSDATA)")
def test_cli_with_valid_bvid_contains_video_title() -> None:
    """Output for a valid BVID includes the video title and basic stats."""
    result = _run_cli("--bvid", "BV1xx411c7m9")
    assert result.returncode == 0
    assert "**播放**" in result.stdout
    assert "**BVID**" in result.stdout
    assert "`BV1xx411c7m9`" in result.stdout


# ---------------------------------------------------------------------------
# Invalid BVID (also needs credentials — VideoAPIClient.__init__ calls
# get_credentials() which triggers QR login flow if none are found)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _credentials_available(), reason="需要 B站 凭证 (BILI_SESSDATA)")
def test_cli_with_invalid_bvid() -> None:
    """``uv run video-analyzer --bvid INVALID`` exits non-zero."""
    result = _run_cli("--bvid", "INVALID", timeout=15)
    assert result.returncode != 0


@pytest.mark.skipif(not _credentials_available(), reason="需要 B站 凭证 (BILI_SESSDATA)")
def test_cli_with_invalid_bvid_error_message() -> None:
    """Invalid BVID prints an error message to stderr."""
    result = _run_cli("--bvid", "INVALID", timeout=15)
    assert result.returncode != 0
    assert "错误" in result.stderr or "错误" in result.stdout
