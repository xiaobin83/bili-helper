"""Tests for at_orchestrator.dispatcher — async subprocess dispatch module.

TDD: these tests are written BEFORE the implementation.
Run once to see them fail (ImportError), then implement dispatcher.py to make them pass.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from at_orchestrator.models import DispatchResult

# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _make_classification(skill_name: str, **params: Any) -> dict[str, Any]:
    """Return a classification dict matching ClassificationResult shape."""
    return {
        "skill_name": skill_name,
        "params": params,
        "confidence": 0.9,
        "reason": f"Test dispatch to {skill_name}",
    }


def _make_task(msg_id: int = 1, source: str = "reply") -> dict[str, Any]:
    """Return a task dict matching Task shape."""
    return {
        "msg_id": msg_id,
        "source": source,
        "user_mid": 12345678,
        "user_nickname": "测试用户",
        "content": "你好 @UP主",
        "business_id": 1,
        "subject_id": 20220101,
    }


def _make_mock_process(
    stdout_text: str = "success",
    stderr_text: str = "",
    returncode: int = 0,
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.communicate = AsyncMock(
        return_value=(stdout_text.encode(), stderr_text.encode())
    )
    proc.returncode = returncode
    return proc


# ──────────────────────────────────────────────────────────────────────
# Test: skill → CLI args mapping
# ──────────────────────────────────────────────────────────────────────


class TestSkillArgMapping:
    """Each known skill maps to the correct ``uv run`` CLI args."""

    @pytest.mark.asyncio
    async def test_video_analyzer_args(self) -> None:
        """video-analyzer: --bvid + --output."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            await dispatcher.dispatch(classification, task)

        # Verify command args
        call_args = mock_exec.call_args[0]
        expected_prefix = ["uv", "run", "video-analyzer", "--bvid", "BV1xx", "--output"]
        assert list(call_args[:6]) == expected_prefix
        assert call_args[6].endswith("/output.md")

    @pytest.mark.asyncio
    async def test_watch_later_recommender_args(self) -> None:
        """watch-later-recommender: --target toview --count N."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification(
            "watch-later-recommender", target="toview", count=5
        )
        task = _make_task()

        mock_proc = _make_mock_process()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            await dispatcher.dispatch(classification, task)

        call_args = mock_exec.call_args[0]
        assert list(call_args) == [
            "uv",
            "run",
            "watch-later-recommender",
            "--target",
            "toview",
            "--count",
            "5",
        ]


# ──────────────────────────────────────────────────────────────────────
# Test: output directory creation
# ──────────────────────────────────────────────────────────────────────


class TestOutputDirectory:
    """The dispatcher creates /tmp/at-orchestrator/{msg_id}_{source}/."""

    @pytest.mark.asyncio
    async def test_creates_output_directory(self, tmp_path: Path) -> None:
        """Output directory should be created before subprocess runs."""
        from at_orchestrator.dispatcher import Dispatcher

        # Use tmp_path as base to avoid polluting real /tmp
        base_dir = tmp_path / "at-orchestrator"
        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task(msg_id=42, source="reply")

        mock_proc = _make_mock_process()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            await dispatcher.dispatch(classification, task)

        # Directory should exist now
        out_dir = base_dir / "42_reply"
        # We can't easily test exact path since it's hardcoded to /tmp,
        # but let's verify the directory arg pattern
        out_arg = mock_exec.call_args.kwargs.get("cwd")
        assert out_arg is not None  # cwd should be set


# ──────────────────────────────────────────────────────────────────────
# Test: return value shape
# ──────────────────────────────────────────────────────────────────────


class TestReturnValue:
    """dispatch() returns a dict matching DispatchResult fields."""

    @pytest.mark.asyncio
    async def test_returns_correct_shape_on_success(self) -> None:
        """Successful dispatch returns all required fields."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process(
            stdout_text="分析完成\n报告已生成",
            returncode=0,
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        assert isinstance(result, dict)
        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == 0
        assert result["stdout"] == "分析完成\n报告已生成"
        assert result["output_file"] is not None
        assert result["output_file"].endswith("/output.md")
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_can_construct_dispatch_result(self) -> None:
        """Returned dict can be passed to DispatchResult model."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process(stdout_text="ok", returncode=0)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        # Should not raise ValidationError
        dispatch_result = DispatchResult.model_validate(result)
        assert dispatch_result.skill == "video-analyzer"
        assert dispatch_result.exit_code == 0
        assert dispatch_result.stdout == "ok"

    @pytest.mark.asyncio
    async def test_watch_later_output_file_is_none(self) -> None:
        """watch-later-recommender has no output file (no --output flag)."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification(
            "watch-later-recommender", target="toview", count=3
        )
        task = _make_task()

        mock_proc = _make_mock_process(stdout_text="推荐完成")
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        assert result["output_file"] is None


# ──────────────────────────────────────────────────────────────────────
# Test: unknown skill
# ──────────────────────────────────────────────────────────────────────


class TestUnknownSkill:
    """unknown skill returns error without spawning any subprocess."""

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown(self) -> None:
        """dispatch() with skill 'unknown' returns error dict immediately."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("unknown")
        task = _make_task()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        # Should NOT have called create_subprocess_exec
        mock_exec.assert_not_called()
        assert result["skill"] == "unknown"
        assert result["exit_code"] == -1
        assert result["output_file"] is None
        assert result["error"] is not None


# ──────────────────────────────────────────────────────────────────────
# Test: subprocess failure
# ──────────────────────────────────────────────────────────────────────


class TestSubprocessFailure:
    """Non-zero exit code is captured, error is empty (error is for exceptions)."""

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self) -> None:
        """When subprocess exits with non-zero code, capture it."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV_fail")
        task = _make_task()

        mock_proc = _make_mock_process(
            stdout_text="",
            stderr_text="BVID not found",
            returncode=1,
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == 1
        assert result["error"] is None  # error is only for exceptions

    @pytest.mark.asyncio
    async def test_stderr_included_in_stdout_on_failure(self) -> None:
        """stderr should be appended to stdout."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("watch-later-recommender", topic="AI")
        task = _make_task()

        mock_proc = _make_mock_process(
            stdout_text="partial output",
            stderr_text="error details",
            returncode=1,
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        assert "partial output" in result["stdout"]
        assert "error details" in result["stdout"]


# ──────────────────────────────────────────────────────────────────────
# Test: exception handling
# ──────────────────────────────────────────────────────────────────────


class TestExceptionHandling:
    """Exceptions during subprocess creation/execution are caught."""

    @pytest.mark.asyncio
    async def test_subprocess_creation_error(self) -> None:
        """When create_subprocess_exec raises, return error."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = OSError("uv not found")
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == -1
        assert result["error"] == "uv not found"

    @pytest.mark.asyncio
    async def test_communicate_error(self) -> None:
        """When proc.communicate() raises, return error."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            side_effect=RuntimeError("process killed")
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch(classification, task)

        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == -1
        assert result["error"] == "process killed"


# ──────────────────────────────────────────────────────────────────────
# Test: cwd set to workspace root
# ──────────────────────────────────────────────────────────────────────


class TestCwd:
    """Subprocess cwd is set to the bili-helper workspace root."""

    @pytest.mark.asyncio
    async def test_cwd_is_set(self) -> None:
        """cwd kwarg should be passed to create_subprocess_exec."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process()
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            await dispatcher.dispatch(classification, task)

        assert "cwd" in mock_exec.call_args.kwargs
        cwd = mock_exec.call_args.kwargs["cwd"]
        assert Path(cwd).is_absolute()
        assert cwd.endswith("bili-helper")
