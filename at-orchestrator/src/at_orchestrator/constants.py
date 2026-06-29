"""Centralised constants for at-orchestrator.

All magic numbers, mapping tables, and configuration values that were
previously scattered across modules are consolidated here.  Consumers
should ``from at_orchestrator.constants import ...`` rather than
redeclaring these values locally.
"""

from __future__ import annotations

from pathlib import Path


# ── Workspace root (auto-detected) ────────────────────────────────────

WORKSPACE_ROOT: str = str(
    Path(__file__).resolve().parent.parent.parent.parent
)


# ── Business ID → comment type mapping ────────────────────────────────
# B站 uses different `type` values depending on the source:
#   video (business_id=1)     → type=1
#   dynamic (business_id=11)   → type=17
#   article (business_id=17)   → type=17

BUSINESS_ID_TO_TYPE: dict[int, int] = {
    1: 1,
    11: 17,
    17: 17,
}


# ── Skill → CLI mapping ───────────────────────────────────────────────
# Each entry defines how to invoke the skill via ``uv run``.
#   - command:      the skill CLI entry point name
#   - subcommand:   (optional) sub-command to pass after the entry point
#   - output_flag:  (optional) ``--output`` flag for skills that produce
#                   an output file

SKILL_CLI_MAP: dict[str, dict] = {
    "video-analyzer": {
        "command": "video-analyzer",
        "output_flag": "--output",
    },
    "watch-later-recommender": {
        "command": "watch-later-recommender",
    },
    "dyn-publisher": {
        "command": "dyn-publisher",
        "subcommand": "publish",
    },
    "fav-organizer": {
        "command": "fav-organizer",
        "subcommand": "classify",
    },
}


# ── Content size limits ───────────────────────────────────────────────
# Based on the B站 API constraints documented in the replier module.

MAX_COMMENT_CHARS: int = 1000
"""Maximum character count for a comment reply."""

MAX_PM_CJK_CHARS: int = 600
"""Maximum character count for a private message (CJK-safe, ≤ floor(2000/3))."""


# ── Subprocess defaults ───────────────────────────────────────────────

SUBPROCESS_TIMEOUT: int = 120
"""Default timeout in seconds for skill subprocess execution."""


# ── Database ──────────────────────────────────────────────────────────

DB_PATH_DEFAULT: str = ".at-orchestrator/tasks.db"
"""Default path for the task database (relative to workspace root)."""
