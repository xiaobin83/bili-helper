"""Async subprocess dispatcher for at-orchestrator.

Runs sub-skills via ``uv run <skill> <args>`` using
:func:`asyncio.create_subprocess_exec`.  Each dispatch creates a unique
output directory under ``/tmp/at-orchestrator/``.

Usage::

    from at_orchestrator.dispatcher import Dispatcher

    dispatcher = Dispatcher()
    result = await dispatcher.dispatch(classification, task)

.. note::

    No timeout is applied here — long-running subprocess timeout is
    handled by the higher-level orchestrator (Task 9).
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

_WORKSPACE_ROOT: str = str(
    Path(__file__).resolve().parent.parent.parent.parent
)

_OUTPUT_BASE: str = "/tmp/at-orchestrator"

# ──────────────────────────────────────────────────────────────────────
# Skill → CLI args builder
# ──────────────────────────────────────────────────────────────────────


def _build_video_analyzer_args(params: dict[str, Any], out_dir: str) -> list[str]:
    """Build CLI args for the video-analyzer skill."""
    bvid = str(params["bvid"])
    output_file = os.path.join(out_dir, "output.md")
    return ["uv", "run", "video-analyzer", "--bvid", bvid, "--output", output_file]


def _build_watch_later_args(params: dict[str, Any]) -> list[str]:
    """Build CLI args for the watch-later-recommender skill."""
    target = str(params.get("target", "toview"))
    count = str(params.get("count", 5))
    return ["uv", "run", "watch-later-recommender", "--target", target, "--count", count]


_SKILL_BUILDERS: dict[str, Any] = {
    "video-analyzer": _build_video_analyzer_args,
    "watch-later-recommender": _build_watch_later_args,
}

# Skills that produce an output file (``--output`` flag is passed).
_SKILLS_WITH_OUTPUT_FILE: frozenset[str] = frozenset({"video-analyzer"})


# ──────────────────────────────────────────────────────────────────────
# Generic CLI arg builder
# ──────────────────────────────────────────────────────────────────────


def _to_skill_args(skill_name: str, params: dict[str, Any]) -> list[str]:
    """Convert a classification result's params dict to CLI argument list.

    Each key-value pair in ``params`` is converted to ``--{key}`` followed
    by ``str(value)``.  The ``skill_name`` itself is *not* included in
    the result — callers must prefix ``uv run <skill_name>`` separately.

    Args:
        skill_name: The skill name (e.g. ``"video-analyzer"``).
        params: Parameter dict from classification result.

    Returns:
        A flat list of CLI tokens, e.g.
        ``["--bvid", "BV1xx", "--output", "/tmp/out.md"]``.

    Examples:
        >>> _to_skill_args("video-analyzer", {"bvid": "BV1xx"})
        ['--bvid', 'BV1xx']
        >>> _to_skill_args("video-analyzer", {})
        []
    """
    args: list[str] = []
    for key, value in params.items():
        args.append(f"--{key}")
        args.append(str(value))
    return args


# ──────────────────────────────────────────────────────────────────────
# Process tree cleanup
# ──────────────────────────────────────────────────────────────────────


def _kill_process_tree(pid: int) -> None:
    """Kill a process tree starting with SIGTERM, then SIGKILL after 5 s.

    Uses :func:`os.kill` to send ``SIGTERM`` first.  Waits 5 seconds,
    then sends ``SIGKILL`` if the process is still alive.  Catches
    :class:`ProcessLookupError` silently (process already dead).

    Args:
        pid: The PID of the root process to kill.
    """
    # Phase 1 — graceful termination
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return  # process already dead — nothing to do

    time.sleep(5)

    # Phase 2 — force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # process died during the 5 s grace period


# ──────────────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────────────


class Dispatcher:
    """Runs sub-skills as subprocesses via ``uv run``.

    Each dispatch creates a unique output directory at
    ``/tmp/at-orchestrator/{msg_id}_{source}/`` and runs the skill
    with the workspace root as ``cwd``.
    """

    def __init__(self, workspace_root: str | None = None) -> None:
        """Initialise the dispatcher.

        Args:
            workspace_root: Override the detected workspace root path.
        """
        self._workspace_root = workspace_root or _WORKSPACE_ROOT

    async def dispatch(
        self, classification: dict[str, Any], task: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a skill subprocess based on classification results.

        Args:
            classification: A dict with at least ``skill_name`` and ``params``.
            task: A dict with at least ``msg_id`` and ``source``.

        Returns:
            A dict matching :class:`~at_orchestrator.models.DispatchResult` fields:
            ``skill``, ``exit_code``, ``stdout``, ``output_file``, ``error``.
        """
        skill_name: str = classification.get("skill_name", "unknown")
        params: dict[str, Any] = classification.get("params", {})
        msg_id: int = int(task.get("msg_id", 0))
        source: str = str(task.get("source", "unknown"))

        # ── unknown skill — bail out immediately ─────────────────────
        if skill_name == "unknown":
            return {
                "skill": "unknown",
                "exit_code": -1,
                "stdout": "",
                "output_file": None,
                "error": "Cannot dispatch unknown skill — no subprocess spawned",
            }

        # ── build output directory ───────────────────────────────────
        out_dir = os.path.join(_OUTPUT_BASE, f"{msg_id}_{source}")
        os.makedirs(out_dir, exist_ok=True)

        # ── build CLI args ───────────────────────────────────────────
        builder = _SKILL_BUILDERS.get(skill_name)
        if builder is None:
            return {
                "skill": skill_name,
                "exit_code": -1,
                "stdout": "",
                "output_file": None,
                "error": f"Unknown skill '{skill_name}' — no CLI builder registered",
            }

        try:
            if skill_name in _SKILLS_WITH_OUTPUT_FILE:
                args = builder(params, out_dir)
            else:
                args = builder(params)
        except KeyError as exc:
            return {
                "skill": skill_name,
                "exit_code": -1,
                "stdout": "",
                "output_file": None,
                "error": f"Missing required parameter for '{skill_name}': {exc}",
            }

        # ── run subprocess ───────────────────────────────────────────
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._workspace_root,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
        except Exception as exc:
            return {
                "skill": skill_name,
                "exit_code": -1,
                "stdout": "",
                "output_file": None if skill_name not in _SKILLS_WITH_OUTPUT_FILE else None,
                "error": str(exc),
            }

        # ── build result ─────────────────────────────────────────────
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        combined_output = stdout_text
        if stderr_text:
            combined_output = (
                f"{stdout_text}\n{stderr_text}" if stdout_text else stderr_text
            )

        output_file: str | None = None
        if skill_name in _SKILLS_WITH_OUTPUT_FILE:
            output_file = os.path.join(out_dir, "output.md")

        return {
            "skill": skill_name,
            "exit_code": proc.returncode or 0,
            "stdout": combined_output,
            "output_file": output_file,
            "error": None,
        }

    async def dispatch_with_timeout(
        self,
        classification: dict[str, Any],
        task: dict[str, Any],
        timeout: float = 120,
    ) -> dict[str, Any]:
        """Run a skill subprocess with a hard timeout.

        Same as :meth:`dispatch` but wraps the subprocess execution in
        :func:`asyncio.wait_for`.  On timeout the process receives
        ``SIGTERM``, a 5 s grace period, then ``SIGKILL``.

        Non-zero exit codes are captured in the ``error`` field (unlike
        ``dispatch`` where they are ignored).

        Args:
            classification: A dict with at least ``skill_name`` and ``params``.
            task: A dict with at least ``msg_id`` and ``source``.
            timeout: Maximum seconds to wait (default 120).

        Returns:
            A dict matching :class:`DispatchResult` fields.  On timeout
            ``exit_code`` is -15 (SIGTERM) or -9 (SIGKILL).
        """
        skill_name: str = classification.get("skill_name", "unknown")
        params: dict[str, Any] = classification.get("params", {})
        msg_id: int = int(task.get("msg_id", 0))
        source: str = str(task.get("source", "unknown"))

        # ── unknown skill — bail out immediately ─────────────────────
        if skill_name == "unknown":
            return {
                "skill": "unknown",
                "exit_code": -1,
                "stdout": "",
                "output_file": None,
                "error": "Cannot dispatch unknown skill — no subprocess spawned",
            }

        # ── build output directory ───────────────────────────────────
        out_dir = os.path.join(_OUTPUT_BASE, f"{msg_id}_{source}")
        os.makedirs(out_dir, exist_ok=True)

        # ── build CLI args ───────────────────────────────────────────
        builder = _SKILL_BUILDERS.get(skill_name)
        if builder is None:
            return {
                "skill": skill_name,
                "exit_code": -1,
                "stdout": "",
                "output_file": None,
                "error": f"Unknown skill '{skill_name}' — no CLI builder registered",
            }

        try:
            if skill_name in _SKILLS_WITH_OUTPUT_FILE:
                args = builder(params, out_dir)
            else:
                args = builder(params)
        except KeyError as exc:
            return {
                "skill": skill_name,
                "exit_code": -1,
                "stdout": "",
                "output_file": None,
                "error": f"Missing required parameter for '{skill_name}': {exc}",
            }

        # ── run subprocess with timeout ──────────────────────────────
        proc = None
        exit_code: int = -1
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._workspace_root,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                exit_code = proc.returncode or 0
            except asyncio.TimeoutError:
                # ── timeout → SIGTERM ────────────────────────────────
                try:
                    proc.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(proc.wait(), timeout=5)
                    exit_code = -15
                except asyncio.TimeoutError:
                    # ── still alive → SIGKILL ────────────────────────
                    proc.send_signal(signal.SIGKILL)
                    await proc.wait()
                    exit_code = -9

                # If process has a pid, clean up process tree
                if proc.pid is not None:
                    _kill_process_tree(proc.pid)

                output_file = os.path.join(out_dir, "output.md") if skill_name in _SKILLS_WITH_OUTPUT_FILE else None
                return {
                    "skill": skill_name,
                    "exit_code": exit_code,
                    "stdout": f"timeout after {timeout}s",
                    "output_file": output_file,
                    "error": f"timeout after {timeout}s",
                }
        except Exception as exc:
            return {
                "skill": skill_name,
                "exit_code": -1,
                "stdout": "",
                "output_file": None if skill_name not in _SKILLS_WITH_OUTPUT_FILE else None,
                "error": str(exc),
            }

        # ── build result ─────────────────────────────────────────────
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        combined_output = stdout_text
        if stderr_text:
            combined_output = (
                f"{stdout_text}\n{stderr_text}" if stdout_text else stderr_text
            )

        output_file: str | None = None
        if skill_name in _SKILLS_WITH_OUTPUT_FILE:
            output_file = os.path.join(out_dir, "output.md")

        error: str | None = None
        if exit_code != 0:
            error = f"non-zero exit code {exit_code}"
            if stderr_text:
                error = f"{error}: {stderr_text}"

        return {
            "skill": skill_name,
            "exit_code": exit_code,
            "stdout": combined_output,
            "output_file": output_file,
            "error": error,
        }
