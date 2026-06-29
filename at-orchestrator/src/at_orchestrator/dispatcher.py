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


def _build_dyn_publisher_args(params: dict[str, Any]) -> list[str]:
    """Build CLI args for the dyn-publisher skill."""
    text = str(params["text"])
    return ["uv", "run", "dyn-publisher", "publish", "--text", text]


def _build_fav_organizer_args(_params: dict[str, Any]) -> list[str]:
    """Build CLI args for the fav-organizer skill."""
    return ["uv", "run", "fav-organizer", "classify", "--all"]


_SKILL_BUILDERS: dict[str, Any] = {
    "video-analyzer": _build_video_analyzer_args,
    "watch-later-recommender": _build_watch_later_args,
    "dyn-publisher": _build_dyn_publisher_args,
    "fav-organizer": _build_fav_organizer_args,
}

# Skills that produce an output file (``--output`` flag is passed).
_SKILLS_WITH_OUTPUT_FILE: frozenset[str] = frozenset({"video-analyzer"})


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
