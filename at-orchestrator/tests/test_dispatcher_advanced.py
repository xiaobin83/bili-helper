"""Tests for at_orchestrator.dispatcher — advanced features: timeout, error
handling, process tree cleanup, and explicit CLI arg builder.

TDD: these tests are written BEFORE the implementation.
Run once to see them fail, then implement dispatcher.py to make them pass.
"""

from __future__ import annotations

import asyncio
import signal
import time
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from at_orchestrator.models import DispatchResult

# ──────────────────────────────────────────────────────────────────────
# Helpers (shared with test_dispatcher.py)
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
# Test: _to_skill_args helper function
# ──────────────────────────────────────────────────────────────────────


class TestToSkillArgs:
    """_to_skill_args(skill_name, params) → list[str] — converts params
    dict to CLI argument list."""

    def test_basic_conversion(self) -> None:
        """Each key-value pair becomes --key + value."""
        from at_orchestrator.dispatcher import _to_skill_args

        result = _to_skill_args("video-analyzer", {"bvid": "BV1xx", "output": "/tmp/out.md"})
        assert result == ["--bvid", "BV1xx", "--output", "/tmp/out.md"]

    def test_empty_params(self) -> None:
        """Empty params dict returns empty list."""
        from at_orchestrator.dispatcher import _to_skill_args

        result = _to_skill_args("fav-organizer", {})
        assert result == []

    def test_values_converted_to_strings(self) -> None:
        """Non-string values are converted to str."""
        from at_orchestrator.dispatcher import _to_skill_args

        result = _to_skill_args("watch-later-recommender", {"count": 5, "target": "toview"})
        assert result == ["--count", "5", "--target", "toview"]

    def test_skill_name_does_not_affect_output(self) -> None:
        """Skill name is not part of the returned arg list."""
        from at_orchestrator.dispatcher import _to_skill_args

        result = _to_skill_args("video-analyzer", {"bvid": "BV1xx"})
        assert "video-analyzer" not in result

    def test_params_with_special_characters(self) -> None:
        """Params with spaces or special chars are preserved as-is."""
        from at_orchestrator.dispatcher import _to_skill_args

        result = _to_skill_args("dyn-publisher", {"text": "Hello World! @user #bili"})
        assert result == ["--text", "Hello World! @user #bili"]

    def test_multiple_params_ordered(self) -> None:
        """Params are output in dict insertion order (Python 3.7+)."""
        from at_orchestrator.dispatcher import _to_skill_args

        params = {"a": 1, "b": 2, "c": 3}
        result = _to_skill_args("test", params)
        assert result == ["--a", "1", "--b", "2", "--c", "3"]

    def test_boolean_params(self) -> None:
        """Boolean values are stringified."""
        from at_orchestrator.dispatcher import _to_skill_args

        result = _to_skill_args("test", {"flag": True, "verbose": False})
        assert result == ["--flag", "True", "--verbose", "False"]


# ──────────────────────────────────────────────────────────────────────
# Test: _kill_process_tree helper function
# ──────────────────────────────────────────────────────────────────────


class TestKillProcessTree:
    """_kill_process_tree(pid) — sends SIGTERM, waits 5s, then SIGKILL."""

    def test_sends_sigterm_then_sigkill(self) -> None:
        """On a running process: SIGTERM first, then SIGKILL after 5s."""
        from at_orchestrator.dispatcher import _kill_process_tree

        pid = 99999

        with patch("os.kill") as mock_kill, patch("time.sleep") as mock_sleep:
            _kill_process_tree(pid)

        assert mock_kill.call_count == 2
        assert mock_kill.call_args_list[0] == call(pid, signal.SIGTERM)
        mock_sleep.assert_called_once_with(5)
        assert mock_kill.call_args_list[1] == call(pid, signal.SIGKILL)

    def test_handles_process_lookup_error(self) -> None:
        """When SIGTERM raises ProcessLookupError, skip SIGKILL and return."""
        from at_orchestrator.dispatcher import _kill_process_tree

        pid = 99999

        with patch("os.kill", side_effect=ProcessLookupError) as mock_kill:
            # Should not raise
            _kill_process_tree(pid)

        # Should only have attempted the SIGTERM call
        assert mock_kill.call_count == 1

    def test_handles_process_lookup_error_on_sigkill(self) -> None:
        """When SIGTERM succeeds but SIGKILL raises ProcessLookupError, ignore."""
        from at_orchestrator.dispatcher import _kill_process_tree

        pid = 99999

        with patch("os.kill") as mock_kill, patch("time.sleep"):
            # First call succeeds (SIGTERM), second raises (SIGKILL)
            mock_kill.side_effect = [None, ProcessLookupError]
            # Should not raise
            _kill_process_tree(pid)

        assert mock_kill.call_count == 2

    def test_sigterm_kills_process_immediately(self) -> None:
        """When SIGTERM kills the process, SIGKILL is still sent safely."""
        from at_orchestrator.dispatcher import _kill_process_tree

        pid = 99999

        # ProcessLookupError on SIGTERM means it was already dead
        with patch("os.kill", side_effect=ProcessLookupError):
            _kill_process_tree(pid)


# ──────────────────────────────────────────────────────────────────────
# Test: dispatch_with_timeout — success path
# ──────────────────────────────────────────────────────────────────────


class TestDispatchWithTimeoutSuccess:
    """dispatch_with_timeout completes within the timeout and returns
    the same shape as dispatch()."""

    @pytest.mark.asyncio
    async def test_successful_dispatch(self) -> None:
        """Completes within timeout, returns DispatchResult-compatible dict."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process(
            stdout_text="分析完成",
            returncode=0,
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task, timeout=120)

        assert isinstance(result, dict)
        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == 0
        assert result["stdout"] == "分析完成"
        assert result["output_file"] is not None
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_can_construct_dispatch_result(self) -> None:
        """Returned dict can be validated as DispatchResult."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process(stdout_text="ok", returncode=0)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task)

        dispatch_result = DispatchResult.model_validate(result)
        assert dispatch_result.skill == "video-analyzer"
        assert dispatch_result.exit_code == 0


# ──────────────────────────────────────────────────────────────────────
# Test: dispatch_with_timeout — timeout handling
# ──────────────────────────────────────────────────────────────────────


class TestDispatchWithTimeoutHandling:
    """When the subprocess exceeds the timeout, it is killed with SIGTERM
    → SIGKILL cascade."""

    @pytest.mark.asyncio
    async def test_timeout_sends_sigterm_then_sigkill(self) -> None:
        """On timeout: SIGTERM, wait 5s, SIGKILL, return error."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        # communicate() hangs forever (simulates timeout)
        async def hang_forever():
            await asyncio.sleep(999)
            return b"", b""

        mock_proc.communicate = hang_forever
        mock_proc.send_signal = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task, timeout=0.1)

        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == -15
        assert result["error"] == "timeout after 0.1s"
        # Verify SIGTERM was sent
        mock_proc.send_signal.assert_any_call(signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_timeout_sigkill_after_grace_period(self) -> None:
        """When process survives SIGTERM, SIGKILL is sent after 5s."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = MagicMock()
        mock_proc.pid = 12345

        async def hang_forever():
            await asyncio.sleep(999)
            return b"", b""

        mock_proc.communicate = hang_forever
        mock_proc.send_signal = MagicMock()

        # wait() hangs on first call (process resists SIGTERM), succeeds on second
        _wait_calls = 0

        async def _wait_side_effect():
            nonlocal _wait_calls
            _wait_calls += 1
            if _wait_calls == 1:
                await asyncio.sleep(999)  # force asyncio.wait_for timeout

        mock_proc.wait = AsyncMock(side_effect=_wait_side_effect)

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task, timeout=0.1)

        # exit_code should be -9 when SIGKILL is needed
        assert result["exit_code"] == -9
        assert "timeout after 0.1s" in result["error"]
        # Both signals should have been sent
        signals_sent = [c.args[0] for c in mock_proc.send_signal.call_args_list]
        assert signal.SIGTERM in signals_sent
        assert signal.SIGKILL in signals_sent


# ──────────────────────────────────────────────────────────────────────
# Test: dispatch_with_timeout — non-zero exit code
# ──────────────────────────────────────────────────────────────────────


class TestDispatchWithTimeoutNonZero:
    """Non-zero exit codes are captured as the error field."""

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_captured(self) -> None:
        """When subprocess exits with non-zero, capture exit_code and stderr."""
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
            result = await dispatcher.dispatch_with_timeout(classification, task)

        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == 1
        assert result["error"] is not None
        assert "non-zero exit code 1" in result["error"]

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_combined_with_stderr(self) -> None:
        """Error message includes stderr output from the subprocess."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("fav-organizer")
        task = _make_task()

        mock_proc = _make_mock_process(
            stdout_text="",
            stderr_text="permission denied",
            returncode=2,
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task)

        assert result["exit_code"] == 2
        assert result["error"] is not None
        assert "non-zero exit code 2" in result["error"]


# ──────────────────────────────────────────────────────────────────────
# Test: dispatch_with_timeout — exception handling
# ──────────────────────────────────────────────────────────────────────


class TestDispatchWithTimeoutExceptions:
    """Exceptions during subprocess creation are caught and returned as errors."""

    @pytest.mark.asyncio
    async def test_subprocess_creation_error(self) -> None:
        """When create_subprocess_exec raises OSError, return error dict."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.side_effect = OSError("uv not found")
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task)

        assert result["skill"] == "video-analyzer"
        assert result["exit_code"] == -1
        assert result["error"] == "uv not found"

    @pytest.mark.asyncio
    async def test_unknown_skill(self) -> None:
        """Skill 'unknown' returns error without spawning subprocess."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("unknown")
        task = _make_task()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task)

        mock_exec.assert_not_called()
        assert result["skill"] == "unknown"
        assert result["exit_code"] == -1
        assert result["output_file"] is None
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_unregistered_skill(self) -> None:
        """Skill not in _SKILL_BUILDERS returns error without spawning."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("some-nonexistent-skill")
        task = _make_task()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task)

        mock_exec.assert_not_called()
        assert result["exit_code"] == -1
        assert result["error"] is not None
        assert "Unknown skill" in result["error"]


# ──────────────────────────────────────────────────────────────────────
# Test: dispatch_with_timeout — default timeout
# ──────────────────────────────────────────────────────────────────────


class TestDispatchWithTimeoutDefault:
    """Default timeout is 120s."""

    @pytest.mark.asyncio
    async def test_default_timeout_is_120(self) -> None:
        """When timeout is not specified, asyncio.wait_for uses 120s."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            with patch("asyncio.wait_for") as mock_wait_for:
                async def fake_wait_for(coro, timeout):
                    return await coro

                mock_wait_for.side_effect = fake_wait_for
                dispatcher = Dispatcher()
                await dispatcher.dispatch_with_timeout(classification, task)

            # wait_for should have been called with timeout=120
            mock_wait_for.assert_called_once()
            assert mock_wait_for.call_args[1]["timeout"] == 120


# ──────────────────────────────────────────────────────────────────────
# Test: dispatch_with_timeout — stderr merging
# ──────────────────────────────────────────────────────────────────────


class TestDispatchWithTimeoutStderr:
    """stderr is merged into stdout for both success and failure cases."""

    @pytest.mark.asyncio
    async def test_stderr_merged_on_success(self) -> None:
        """stderr appended to stdout even on success."""
        from at_orchestrator.dispatcher import Dispatcher

        classification = _make_classification("video-analyzer", bvid="BV1xx")
        task = _make_task()

        mock_proc = _make_mock_process(
            stdout_text="done",
            stderr_text="warning: deprecated API",
            returncode=0,
        )
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_exec.return_value = mock_proc
            dispatcher = Dispatcher()
            result = await dispatcher.dispatch_with_timeout(classification, task)

        assert "done" in result["stdout"]
        assert "warning: deprecated API" in result["stdout"]
