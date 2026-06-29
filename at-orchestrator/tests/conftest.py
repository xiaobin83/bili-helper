"""Pytest fixtures for at-orchestrator tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Return a Path to a temporary SQLite database file."""
    return tmp_path / "test.db"


@pytest.fixture
def sample_task() -> dict[str, Any]:
    """Factory fixture returning a sample Task dict with sensible defaults."""
    return {
        "id": 1,
        "bvid": "BV1GJ411x7DF",
        "uid": 123456,
        "username": "示例UP主",
        "title": "示例视频标题",
        "rule_name": "mention_reply",
        "status": "pending",
        "created_at": "2026-06-29T00:00:00",
    }


@pytest.fixture
def mock_credentials() -> dict[str, str]:
    """Return mock Bilibili credentials for testing."""
    return {
        "sessdata": "mock_sessdata_abc123",
        "bili_jct": "mock_bili_jct_abc123",
        "buvid3": "mock_buvid3_abc123",
        "buvid4": "mock_buvid4_abc123",
    }
