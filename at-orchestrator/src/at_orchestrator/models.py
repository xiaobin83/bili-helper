"""Pydantic v2 data models for at-orchestrator.

All models use ``model_config = ConfigDict(extra="ignore")`` for resilience
against Bilibili API field additions / shape changes.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ──────────────────────────────────────────────────────────────────────
# Shared constants
# ──────────────────────────────────────────────────────────────────────

VALID_SKILLS: frozenset[str] = frozenset({
    "video-analyzer",
    "watch-later-recommender",
    "unknown",
})

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
#   command:      the skill CLI entry point name
#   subcommand:   (optional) sub-command to pass after the entry point
#   output_flag:  (optional) ``--output`` flag for skills that produce
#                 an output file

SKILL_CLI_MAP: dict[str, dict] = {
    "video-analyzer": {
        "command": "video-analyzer",
        "output_flag": "--output",
    },
    "watch-later-recommender": {
        "command": "watch-later-recommender",
    },
}


# ──────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    """Lifecycle of an AT/reply task."""

    pending = "pending"
    classifying = "classifying"
    dispatching = "dispatching"
    replying = "replying"
    replied = "replied"
    failed = "failed"


# ──────────────────────────────────────────────────────────────────────
# Domain models
# ──────────────────────────────────────────────────────────────────────


class Task(BaseModel):
    """An inbound AT (@) or reply notification from Bilibili."""

    model_config = ConfigDict(extra="ignore")

    msg_id: int
    source: str
    user_mid: int
    user_nickname: str
    content: str
    business_id: int
    subject_id: int
    root_id: int | None = None
    source_id: int | None = None
    status: TaskStatus = TaskStatus.pending
    created_at: float = Field(default_factory=time.time)
    processed_at: float | None = None
    reply_method: str | None = None
    reply_error: str | None = None
    cursor_id: int | None = None
    cursor_time: float | None = None


class ClassificationResult(BaseModel):
    """Result of classifying a task to a skill."""

    model_config = ConfigDict(extra="ignore")

    skill_name: str
    params: dict[str, Any] = {}
    confidence: float
    reason: str

    @field_validator("skill_name")
    @classmethod
    def _validate_skill_name(cls, v: str) -> str:
        if v not in VALID_SKILLS:
            msg = f"skill_name must be one of {sorted(VALID_SKILLS)}, got '{v}'"
            raise ValueError(msg)
        return v

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError(f"confidence must be between 0 and 1, got {v}")
        return v


class DispatchResult(BaseModel):
    """Result of dispatching a task to a skill."""

    model_config = ConfigDict(extra="ignore")

    skill: str
    exit_code: int
    stdout: str
    output_file: str | None = None
    error: str | None = None


class ReplyRequest(BaseModel):
    """Request payload for posting a reply (comment or PM)."""

    model_config = ConfigDict(extra="ignore")

    task: Any
    reply_content: str
    method: str

    @field_validator("method")
    @classmethod
    def _validate_method(cls, v: str) -> str:
        if v not in ("comment", "pm"):
            raise ValueError(f"method must be 'comment' or 'pm', got '{v}'")
        return v
